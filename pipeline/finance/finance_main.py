"""
finance_main.py
─────────────────────────────────────────────────────────────────────────────
금융동향 파이프라인 — 메인 오케스트레이터

실행 흐름:
  Step 00  수집 기간 결정   (PERIOD_START / PERIOD_END 환경변수)
  Step 01  병렬 데이터 수집 (ECOS / FRED / KOSIS 금융)
  Step 02  검증             (category_key 비어있음 감지)
  Step 03  BigQuery 적재   (Staging INSERT → MERGE Upsert → Dedup View)
  Step 04  R 분석           (run_analysis.R subprocess 호출)

사용:
  python finance_main.py                            # 전체 소스, 자동 기간
  python finance_main.py --source ecos fred         # 특정 소스만
  python finance_main.py --dry-run                  # 수집+검증만, BQ 적재 없음
  python finance_main.py --period 2023-01 2025-12   # 수집 기간 직접 지정
  python finance_main.py --no-analysis              # R 분석 건너뜀

환경변수:
  필수: GCP_PROJECT_ID
  선택: ECOS_API_KEY, FRED_API_KEY, KOSIS_API_KEY
        PERIOD_START, PERIOD_END  (없으면 실행일 기준 자동 산출)
        SLACK_WEBHOOK_URL
        FINANCE_BQ_DATASET   (기본: finance_stats)
        ANALYSIS_ENABLED     (기본: true)
        ANALYSIS_TIMEOUT_SEC (기본: 1800)
"""

import argparse
import json
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from finance_config import set_env_periods, get_env_periods
from ecos_fetcher import fetch_all_ecos
from fred_fetcher import fetch_all_fred
from finance_kosis_fetcher import fetch_all_finance_kosis
from finance_bq_loader import upsert_records, validate_category_keys


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 로깅 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("finance_main")

ALL_SOURCES = ["ecos", "fred", "kosis_finance"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 01 — 소스별 수집 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _run_ecos(run_id: str) -> tuple[str, list[dict], dict]:
    records = fetch_all_ecos(run_id)
    return "ecos", records, {"ecos_count": len(records)}


def _run_fred(run_id: str) -> tuple[str, list[dict], dict]:
    records = fetch_all_fred(run_id)
    return "fred", records, {"fred_count": len(records)}


def _run_kosis_finance(run_id: str) -> tuple[str, list[dict], dict]:
    records = fetch_all_finance_kosis(run_id)
    return "kosis_finance", records, {"kosis_finance_count": len(records)}


SOURCE_RUNNERS = {
    "ecos":          _run_ecos,
    "fred":          _run_fred,
    "kosis_finance": _run_kosis_finance,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run_pipeline(
    sources:         list[str] | None = None,
    dry_run:         bool = False,
    no_analysis:     bool = False,
    period_override: tuple[str, str] | None = None,
) -> dict:
    """
    금융동향 파이프라인 통합 실행.

    Parameters
    ----------
    sources         : 수집할 소스 목록. None이면 ALL_SOURCES 전체.
    dry_run         : True이면 BQ 적재 없이 수집+검증만 실행.
    no_analysis     : True이면 R 분석 건너뜀.
    period_override : (start, end) 직접 지정. None이면 자동 산출.

    Returns
    -------
    dict : 실행 결과 요약
    """
    run_id     = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    sources    = sources or ALL_SOURCES

    logger.info("=" * 70)
    logger.info(f"[Finance] RUN START  run_id={run_id}")
    logger.info(
        f"[Finance] sources={sources}  dry_run={dry_run}  "
        f"no_analysis={no_analysis}"
    )

    source_errors: dict[str, str] = {}
    all_records:   list[dict] = []
    source_meta:   dict = {}
    period_start = period_end = ""

    # ── Step 00: 수집 기간 결정 ────────────────────────────────────────────
    if period_override:
        period_start, period_end = period_override
        os.environ["PERIOD_START"] = period_start
        os.environ["PERIOD_END"]   = period_end
        logger.info(f"[00] 수집 기간 (직접 지정): {period_start} ~ {period_end}")
    elif os.environ.get("PERIOD_START") and os.environ.get("PERIOD_END"):
        period_start, period_end = get_env_periods()
        logger.info(f"[00] 수집 기간 (환경변수):  {period_start} ~ {period_end}")
    else:
        period_start, period_end = set_env_periods()
        logger.info(f"[00] 수집 기간 (자동 산출): {period_start} ~ {period_end}")

    # ── Step 01: 병렬 수집 ────────────────────────────────────────────────
    logger.info(f"[01] 병렬 수집 시작 ({len(sources)}개 소스)")

    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = {
            executor.submit(SOURCE_RUNNERS[src], run_id): src
            for src in sources
            if src in SOURCE_RUNNERS
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                src_name, records, meta = future.result()
                all_records.extend(records)
                source_meta[src_name] = meta
                logger.info(f"[01] {src_name:15s} ✓ {len(records):,}건  {meta}")
            except Exception as exc:
                logger.error(f"[01] {src} ✗ 수집 실패: {exc}", exc_info=True)
                source_errors[src] = str(exc)

    logger.info(f"[01] 완료: 총 {len(all_records):,}건")

    # ── Step 02: 검증 ────────────────────────────────────────────────────
    logger.info("[02] 데이터 검증 시작")
    val_report = validate_category_keys(all_records)

    empty_key_count = val_report["empty_category_key"]
    if empty_key_count:
        logger.warning(f"[02] category_key 누락 {empty_key_count:,}건 — 적재 제외")
        all_records = [r for r in all_records if r.get("category_key")]

    logger.info(
        f"[02] 검증 완료 — 유효: {len(all_records):,}건 / "
        f"category_key 누락: {empty_key_count:,}건"
    )
    logger.info(f"[02] 소스별: {val_report['by_source']}")

    # ── Step 03: BigQuery 적재 ────────────────────────────────────────────
    bq_result: dict = {"staged": 0, "dropped": 0, "merged": 0}

    if dry_run:
        logger.info("[03] dry-run 모드: BQ 적재 건너뜀")
    elif not all_records:
        logger.warning("[03] 적재 대상 레코드 없음: BQ 적재 건너뜀")
    else:
        logger.info(f"[03] BigQuery Upsert 시작 ({len(all_records):,}건)")
        try:
            bq_result = upsert_records(all_records, run_id)
            logger.info(
                f"[03] BQ 완료 — "
                f"staged: {bq_result['staged']:,}건 / "
                f"dedup 제거: {bq_result['dropped']:,}건 / "
                f"merged: {bq_result['merged']:,}건"
            )
        except Exception as exc:
            logger.error(f"[03] BQ 적재 실패: {exc}", exc_info=True)
            source_errors["bq_load"] = str(exc)

    # ── Step 04: R 분석 ───────────────────────────────────────────────────
    analysis_result: dict = {"skipped": False, "returncode": None, "error": None}

    run_analysis = (
        not dry_run
        and not no_analysis
        and "bq_load" not in source_errors
        and os.getenv("ANALYSIS_ENABLED", "true").lower() == "true"
    )

    if not run_analysis:
        reason = (
            "dry-run" if dry_run else
            "--no-analysis" if no_analysis else
            "BQ 오류" if "bq_load" in source_errors else
            "ANALYSIS_ENABLED=false"
        )
        logger.info(f"[04] R 분석 건너뜀 ({reason})")
        analysis_result["skipped"] = True
    else:
        import subprocess, shutil
        rscript_bin = shutil.which("Rscript")
        if not rscript_bin:
            logger.warning("[04] Rscript 미설치 — R 분석 건너뜀")
            analysis_result["skipped"] = True
        else:
            analysis_dir = os.getenv(
                "FINANCE_ANALYSIS_DIR",
                os.path.join(os.path.dirname(__file__), "analysis"),
            )
            r_env = {
                **os.environ,
                "PIPELINE_RUN_ID": run_id,
                "PERIOD_START":    period_start,
                "PERIOD_END":      period_end,
            }
            r_cmd = [rscript_bin, "run_analysis.R"]
            logger.info(
                f"[04] R 분석 시작  dir={analysis_dir}  run_id={run_id}"
            )
            try:
                proc = subprocess.run(
                    r_cmd,
                    cwd           = analysis_dir,
                    env           = r_env,
                    capture_output= False,
                    timeout       = int(os.getenv("ANALYSIS_TIMEOUT_SEC", "1800")),
                )
                analysis_result["returncode"] = proc.returncode
                if proc.returncode != 0:
                    logger.error(f"[04] R 분석 실패 (exit={proc.returncode})")
                    source_errors["analysis"] = f"Rscript exit={proc.returncode}"
                else:
                    logger.info("[04] R 분석 완료")
            except subprocess.TimeoutExpired:
                logger.error("[04] R 분석 타임아웃")
                source_errors["analysis"] = "timeout"
                analysis_result["error"]  = "timeout"
            except Exception as exc:
                logger.error(f"[04] R 분석 실행 오류: {exc}", exc_info=True)
                source_errors["analysis"] = str(exc)
                analysis_result["error"]  = str(exc)

    # ── 결과 요약 ────────────────────────────────────────────────────────
    finished_at = datetime.now(timezone.utc)
    elapsed_sec = (finished_at - started_at).total_seconds()

    bq_failed  = "bq_load" in source_errors
    has_error  = bool(source_errors)

    if not has_error:
        status = "SUCCESS"
    elif bq_failed or not all_records:
        status = "FAILURE"
    else:
        status = "PARTIAL_FAILURE"

    summary = {
        "run_id":             run_id,
        "period_start":       period_start,
        "period_end":         period_end,
        "sources":            sources,
        "started_at":         started_at.isoformat(),
        "finished_at":        finished_at.isoformat(),
        "elapsed_sec":        round(elapsed_sec, 1),
        "total_records":      len(all_records),
        "empty_category_key": empty_key_count,
        "bq_staged":          bq_result["staged"],
        "bq_dedup_dropped":   bq_result["dropped"],
        "bq_merged":          bq_result["merged"],
        "analysis_skipped":   analysis_result.get("skipped", True),
        "analysis_returncode":analysis_result.get("returncode"),
        "source_meta":        source_meta,
        "errors":             source_errors,
        "status":             status,
        "dry_run":            dry_run,
    }

    logger.info("[Finance] 실행 요약:")
    logger.info(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    # Slack 알림 (선택 — SLACK_WEBHOOK_URL 있을 때만)
    _notify_slack(summary)

    if status == "FAILURE" and not dry_run:
        logger.error(f"[Finance] FAILURE — {list(source_errors.keys())}")
        sys.exit(1)
    elif status == "PARTIAL_FAILURE":
        logger.warning(
            f"[Finance] PARTIAL_FAILURE — 실패 소스: {list(source_errors.keys())}"
        )

    return summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Slack 알림 (경량 버전 — notifier.py 없어도 동작)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _notify_slack(summary: dict) -> None:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return

    status  = summary["status"]
    icon    = "✅" if status == "SUCCESS" else ("⚠️" if status == "PARTIAL_FAILURE" else "❌")
    elapsed = summary["elapsed_sec"]
    merged  = summary["bq_merged"]
    errors  = summary.get("errors", {})

    text = (
        f"{icon} *[금융동향] {status}*\n"
        f"• 기간: {summary['period_start']} ~ {summary['period_end']}\n"
        f"• 소스: {', '.join(summary['sources'])}\n"
        f"• BQ merged: {merged:,}건  (소요: {elapsed:.0f}초)\n"
    )
    if errors:
        text += f"• 오류: {list(errors.keys())}\n"

    try:
        import requests as _req
        _req.post(webhook, json={"text": text}, timeout=10)
    except Exception as e:
        logger.warning(f"[Slack] 알림 실패: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="금융동향 파이프라인 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python finance_main.py                                # 전체 소스, 자동 기간
  python finance_main.py --source ecos fred             # 특정 소스만
  python finance_main.py --dry-run                      # 수집+검증만, BQ 적재 없음
  python finance_main.py --period 2023-01 2025-12       # 기간 직접 지정
  python finance_main.py --no-analysis                  # R 분석 건너뜀
        """,
    )
    p.add_argument(
        "--source", "-s",
        nargs="*",
        choices=ALL_SOURCES,
        default=None,
        metavar="SRC",
        help=f"수집 소스 지정 (기본: 전체). 선택: {ALL_SOURCES}",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="BQ 적재 없이 수집 + 검증만 실행",
    )
    p.add_argument(
        "--no-analysis",
        action="store_true",
        default=False,
        help="R 분석 건너뜀 (BQ 적재까지만 실행)",
    )
    p.add_argument(
        "--period",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="수집/조회 기간 직접 지정 (YYYY-MM YYYY-MM). 예: --period 2023-01 2025-12",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    period = tuple(args.period) if args.period else None
    run_pipeline(
        sources         = args.source,
        dry_run         = args.dry_run,
        no_analysis     = args.no_analysis,
        period_override = period,
    )

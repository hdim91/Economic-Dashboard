"""
main.py
─────────────────────────────────────────────────────────────────────────────
고용동향 분석 파이프라인 — 메인 오케스트레이터

실행 흐름 (기본):
  Step 00  수집 기간 결정   (PERIOD_START / PERIOD_END 환경변수)
  Step 01  병렬 데이터 수집 (KOSIS / NAVER / BLS / Google Trends)
  Step 02  검증             (Pydantic — 형식·타입·허용값)
  Step 03  Pipeline & Transform
              └─ 이상치 마킹 (IQR × 3σ)
              └─ 파생 지표  (MoM% / YoY% / MA3 / MA6 / MA12)
  Step 04  BigQuery 적재   (Staging INSERT → MERGE Upsert → Dedup View)
  Step 05  Slack 알림

실행 흐름 (--transform-only):
  Step 00  수집 기간 결정
  Step 01  [SKIP] API 수집 없음
  Step 02  BQ Raw 테이블에서 원본 레코드 직접 읽기
              └─ adjustment_type IN ('raw','seasonal','index') 만 조회
              └─ 파생 레코드(derived_pct/ma)는 중복 방지를 위해 제외
  Step 03  Pipeline & Transform (동일)
  Step 04  BigQuery 적재 (동일 — 파생 레코드 MERGE Upsert)
  Step 05  Slack 알림

  사용 사례:
    - 이미 BQ에 원본 데이터가 적재된 상태에서 transform 로직을 변경/재실행
    - transform 오류로 파생 레코드만 재생성이 필요한 경우
    - API 수집 없이 기존 데이터로 백필(backfill) 처리

사용:
  python main.py                           # 전체 소스, 자동 기간
  python main.py --source kosis naver      # 특정 소스만
  python main.py --dry-run                 # 수집+변환만, BQ 적재 없음
  python main.py --no-transform            # 변환 스킵, 원본만 적재
  python main.py --period 2025-01 2025-12  # 수집 기간 직접 지정
  python main.py --transform-only          # BQ 원본 읽기 → 변환 → 재적재
  python main.py --transform-only --source kosis --period 2024-01 2025-12
  python main.py --mart-only               # Mart View만 갱신 (수집·적재 없음)
  python main.py --no-mart                 # Mart View 갱신 스킵

환경변수:
  필수: KOSIS_API_KEY, GCP_PROJECT_ID
  선택: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, BLS_API_KEY
        SLACK_WEBHOOK_URL
        PERIOD_START, PERIOD_END  (없으면 실행일 기준 자동 산출)
        TRANSFORM_ENABLED=false   (변환 비활성화)
        TRANSFORM_IQR_K=3.0       (이상치 배수)
"""

import argparse
import json
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config import (
    set_env_periods,
    get_env_periods,
    TRANSFORM_ENABLED,
)
from kosis_fetcher import fetch_all_kosis
from naver_fetcher import fetch_all_naver
from bls_fetcher import fetch_all_bls
from google_trends_fetcher import fetch_google_trends
from validator import validate_records
from transform import run_transform
from bq_loader import upsert_records, fetch_raw_records
from mart_loader import refresh_all_marts, MART_VIEW_NAMES
from feature_store import refresh_all_fs, FS_VIEW_NAMES
from notifier import notify_slack
from export_dashboard_data import export_dashboard_data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 로깅 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

ALL_SOURCES = ["kosis", "naver", "bls", "google"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 01 — 소스별 수집 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _run_kosis(run_id: str) -> tuple[str, list[dict], dict]:
    result = fetch_all_kosis(run_id)
    records = (
        result["kosis_emp"]
        + result["kosis_wage"]
        + result["kosis_benefit"]
    )
    meta = {
        "kosis_emp_count":     len(result["kosis_emp"]),
        "kosis_wage_count":    len(result["kosis_wage"]),
        "kosis_benefit_count": len(result["kosis_benefit"]),
    }
    return "kosis", records, meta


def _run_naver(run_id: str) -> tuple[str, list[dict], dict]:
    result = fetch_all_naver(run_id)
    records = (
        result["naver_datalab"]
        # + result["naver_jobpost"]
        # + result["naver_news"]
    )
    meta = {
        "datalab_count": len(result["naver_datalab"]),
        # "jobpost_count": len(result["naver_jobpost"]),
        # "news_count":    len(result["naver_news"]),
    }
    return "naver", records, meta


def _run_bls(run_id: str) -> tuple[str, list[dict], dict]:
    records = fetch_all_bls(run_id)
    return "bls", records, {"bls_count": len(records)}


def _run_google(run_id: str) -> tuple[str, list[dict], dict]:
    records = fetch_google_trends(run_id)
    return "google", records, {"google_trends_count": len(records)}


SOURCE_RUNNERS = {
    "kosis":  _run_kosis,
    "naver":  _run_naver,
    "bls":    _run_bls,
    "google": _run_google,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run_pipeline(
    sources:         list[str] | None = None,
    dry_run:         bool = False,
    no_transform:    bool = False,
    transform_only:  bool = False,
    mart_only:       bool = False,
    fs_only:         bool = False,
    no_mart:         bool = False,
    period_override: tuple[str, str] | None = None,
) -> dict:
    """
    파이프라인 통합 실행.

    Parameters
    ----------
    sources          : 수집할 소스 목록. None이면 ALL_SOURCES 전체.
    dry_run          : True이면 BQ 적재 없이 수집+변환까지만 실행.
    no_transform     : True이면 변환 스킵, 검증된 원본만 적재.
    transform_only   : True이면 API 수집 없이 BQ 원본 레코드를 읽어
                       변환 후 파생 레코드만 재적재.
    mart_only        : True이면 수집·변환·BQ 적재를 건너뛰고
                       Mart View 갱신만 실행 (Feature Store 제외).
    fs_only          : True이면 수집·변환·BQ 적재·Mart를 건너뛰고
                       Feature Store View 갱신만 실행.
    no_mart          : True이면 Mart 및 Feature Store 갱신을 모두 건너뜀.
    period_override  : (start, end) 직접 지정. None이면 자동 산출.

    Returns
    -------
    dict : 실행 결과 요약
    """
    run_id     = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    sources    = sources or ALL_SOURCES

    logger.info("=" * 70)
    logger.info(f"[Pipeline] RUN START  run_id={run_id}")
    logger.info(
        f"[Pipeline] sources={sources}  dry_run={dry_run}  "
        f"no_transform={no_transform}  transform_only={transform_only}  "
        f"mart_only={mart_only}  fs_only={fs_only}  no_mart={no_mart}"
    )

    source_errors: dict[str, str] = {}

    # ── 초기값 (mart_only 시 Step 01~04 미실행) ───────────────────────
    period_start:     str        = ""
    period_end:       str        = ""
    all_raw:          list[dict] = []
    source_meta:      dict       = {}
    valid_records:    list[dict] = []
    val_report:       dict       = {"valid_count": 0, "invalid_count": 0, "errors_by_source": {}}
    do_transform:     bool       = False
    transform_report: dict       = {}
    bq_ready:         list[dict] = []
    bq_result:        dict       = {"staged": 0, "dropped": 0, "merged": 0}

    if mart_only or fs_only:
        logger.info(
            f"[Pipeline] {'MART-ONLY' if mart_only else 'FS-ONLY'} 모드 "
            f"— 수집·변환·BQ 적재 건너뜀"
        )
    else:
        # ── Step 00: 수집 기간 결정 ────────────────────────────────────
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

        # ── Step 01: 수집 or BQ 읽기 ──────────────────────────────────
        if transform_only:
            logger.info("[01] TRANSFORM-ONLY 모드 — BQ Raw에서 원본 레코드 읽기")
            logger.info(f"[01] 조회 기간: {period_start} ~ {period_end}  소스 필터: {sources}")
            try:
                from bq_loader import get_bq_client
                bq_client = get_bq_client()
                all_raw = fetch_raw_records(
                    client       = bq_client,
                    period_start = period_start,
                    period_end   = period_end,
                    sources      = sources if sources != ALL_SOURCES else None,
                )
                for rec in all_raw:
                    src = rec.get("source", "unknown")
                    source_meta.setdefault(src, {"bq_read_count": 0})
                    source_meta[src]["bq_read_count"] += 1
                logger.info(f"[01] BQ 읽기 완료: {len(all_raw):,}건  {source_meta}")
            except Exception as exc:
                logger.error(f"[01] BQ 읽기 실패: {exc}", exc_info=True)
                source_errors["bq_read"] = str(exc)
        else:
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
                        all_raw.extend(records)
                        source_meta[src_name] = meta
                        logger.info(f"[01] {src_name:8s} ✓ {len(records):,}건  {meta}")
                    except Exception as exc:
                        logger.error(f"[01] {src} ✗ 수집 실패: {exc}", exc_info=True)
                        source_errors[src] = str(exc)

        logger.info(f"[01] 완료: 총 {len(all_raw):,}건")

        # ── Step 02: 검증 ─────────────────────────────────────────────
        logger.info("[02] 데이터 검증 시작")
        valid_records, val_report = validate_records(all_raw)
        logger.info(
            f"[02] 검증 완료 — 유효: {val_report['valid_count']:,}건 / "
            f"제거: {val_report['invalid_count']:,}건"
        )
        if val_report["invalid_count"]:
            logger.warning(f"[02] 소스별 오류: {val_report['errors_by_source']}")

        # ── Step 03: Pipeline & Transform ─────────────────────────────
        do_transform = TRANSFORM_ENABLED and not no_transform

        if not valid_records:
            logger.warning("[03] 유효 레코드 없음 — 변환 스킵")
            bq_ready = []
        elif do_transform:
            logger.info("[03] Pipeline & Transform 시작")
            try:
                tf_result        = run_transform(valid_records, run_id=run_id)
                bq_ready         = tf_result["transformed"]
                transform_report = tf_result["report"]
                logger.info(
                    f"[03] Transform 완료 — "
                    f"원본 {val_report['valid_count']:,}건 "
                    f"+ 파생 {transform_report.get('derived_count', 0):,}건 "
                    f"= 적재 대상 {len(bq_ready):,}건"
                )
            except Exception as exc:
                logger.error(f"[03] Transform 실패: {exc}", exc_info=True)
                source_errors["transform"] = str(exc)
                bq_ready = valid_records
                logger.warning("[03] Transform 실패로 원본만 적재합니다")
        else:
            reason = "no_transform 플래그" if no_transform else "TRANSFORM_ENABLED=false"
            logger.info(f"[03] Transform 스킵 ({reason}) — 원본만 적재")
            bq_ready = valid_records

        # ── Step 04: BigQuery 적재 ─────────────────────────────────────
        if dry_run:
            logger.info("[04] dry-run 모드: BQ 적재 건너뜀")
        elif not bq_ready:
            logger.warning("[04] 적재 대상 레코드 없음: BQ 적재 건너뜀")
        else:
            logger.info(f"[04] BigQuery Upsert 시작 ({len(bq_ready):,}건)")
            try:
                bq_result = upsert_records(bq_ready, run_id)
                logger.info(
                    f"[04] BQ 완료 — "
                    f"staged: {bq_result['staged']:,}건 / "
                    f"dedup 제거: {bq_result['dropped']:,}건 / "
                    f"merged: {bq_result['merged']:,}건"
                )
            except Exception as exc:
                logger.error(f"[04] BQ 적재 실패: {exc}", exc_info=True)
                source_errors["bq_load"] = str(exc)

    # ── Step 04.5: Mart View 갱신 ─────────────────────────────────────
    # 실행 조건: mart_only OR 일반 실행 (fs_only·no_mart·dry_run·bq실패 제외)
    mart_result: dict = {"created": [], "failed": {}}

    if dry_run:
        logger.info("[04.5] dry-run 모드: Mart 갱신 건너뜀")
    elif no_mart:
        logger.info("[04.5] --no-mart: Mart 갱신 건너뜀")
    elif fs_only:
        logger.info("[04.5] --fs-only: Mart 갱신 건너뜀")
    elif "bq_load" in source_errors:
        logger.warning("[04.5] BQ 적재 실패로 Mart 갱신 건너뜀")
    else:
        logger.info(f"[04.5] Mart View 갱신 시작 ({len(MART_VIEW_NAMES)}개)")
        try:
            mart_result = refresh_all_marts()
            logger.info(
                f"[04.5] Mart 완료 — "
                f"성공 {len(mart_result['created'])}개 / "
                f"실패 {len(mart_result['failed'])}개"
            )
            if mart_result["failed"]:
                source_errors["mart"] = str(mart_result["failed"])
        except Exception as exc:
            logger.error(f"[04.5] Mart 갱신 실패: {exc}", exc_info=True)
            source_errors["mart"] = str(exc)

    # ── Step 04.6: Feature Store View 갱신 ───────────────────────────
    # 실행 조건: fs_only OR 일반 실행 (mart_only·no_mart·dry_run·bq실패·mart실패 제외)
    fs_result: dict = {"created": [], "failed": {}}

    if dry_run:
        logger.info("[04.6] dry-run 모드: Feature Store 갱신 건너뜀")
    elif no_mart:
        logger.info("[04.6] --no-mart: Feature Store 갱신 건너뜀")
    elif mart_only:
        logger.info("[04.6] --mart-only: Feature Store 갱신 건너뜀")
    elif "bq_load" in source_errors:
        logger.warning("[04.6] BQ 적재 실패로 Feature Store 갱신 건너뜀")
    elif not fs_only and mart_result.get("failed"):
        logger.warning("[04.6] Mart 갱신 실패로 Feature Store 갱신 건너뜀")
    else:
        logger.info(f"[04.6] Feature Store View 갱신 시작 ({len(FS_VIEW_NAMES)}개)")
        try:
            fs_result = refresh_all_fs()
            logger.info(
                f"[04.6] Feature Store 완료 — "
                f"성공 {len(fs_result['created'])}개 / "
                f"실패 {len(fs_result['failed'])}개"
            )
            if fs_result["failed"]:
                source_errors["feature_store"] = str(fs_result["failed"])
        except Exception as exc:
            logger.error(f"[04.6] Feature Store 갱신 실패: {exc}", exc_info=True)
            source_errors["feature_store"] = str(exc)

    # ── Step 05: R Analysis Layer ──────────────────────────────────────
    # Rscript run_analysis.R 을 subprocess로 호출
    # ANALYSIS_SCRIPT_DIR 환경변수로 R 스크립트 경로 지정 (기본: ./analysis)
    analysis_result: dict = {"skipped": False, "returncode": None, "error": None}

    run_analysis = (
        not dry_run
        and not mart_only
        and not fs_only
        and "bq_load" not in source_errors
        and not source_errors.get("feature_store")
        and os.getenv("ANALYSIS_ENABLED", "true").lower() == "true"
    )

    if not run_analysis:
        reason = (
            "dry-run" if dry_run else
            "mart-only/fs-only" if (mart_only or fs_only) else
            "BQ 오류" if "bq_load" in source_errors else
            "FS 오류" if source_errors.get("feature_store") else
            "ANALYSIS_ENABLED=false"
        )
        logger.info(f"[05] R 분석 건너뜀 ({reason})")
        analysis_result["skipped"] = True
    else:
        import subprocess, shutil
        rscript_bin = shutil.which("Rscript")
        if not rscript_bin:
            logger.warning("[05] Rscript 미설치 — R 분석 건너뜀")
            analysis_result["skipped"] = True
        else:
            analysis_dir = os.getenv(
                "ANALYSIS_SCRIPT_DIR",
                os.path.join(os.path.dirname(__file__), "analysis")
            )
            r_env = {
                **os.environ,
                "PIPELINE_RUN_ID": run_id,
                "PERIOD_START":    period_start or "",
                "PERIOD_END":      period_end   or "",
            }
            r_steps = os.getenv("ANALYSIS_STEPS", "")  # 예: "02,03" → 특정 단계만
            r_cmd   = [rscript_bin, "run_analysis.R"]
            if r_steps:
                r_cmd += ["--steps", r_steps]

            logger.info(
                f"[05] R 분석 시작  dir={analysis_dir}  "
                f"cmd={' '.join(r_cmd[1:])}  run_id={run_id}"
            )
            try:
                proc = subprocess.run(
                    r_cmd,
                    cwd    = analysis_dir,
                    env    = r_env,
                    capture_output = False,   # R 로그를 stdout/stderr로 그대로 출력
                    timeout = int(os.getenv("ANALYSIS_TIMEOUT_SEC", "3600")),
                )
                analysis_result["returncode"] = proc.returncode
                if proc.returncode != 0:
                    logger.error(f"[05] R 분석 실패 (exit={proc.returncode})")
                    source_errors["analysis"] = f"Rscript exit={proc.returncode}"
                else:
                    logger.info("[05] R 분석 완료")
            except subprocess.TimeoutExpired:
                logger.error("[05] R 분석 타임아웃")
                source_errors["analysis"] = "timeout"
                analysis_result["error"]  = "timeout"
            except Exception as exc:
                logger.error(f"[05] R 분석 실행 오류: {exc}", exc_info=True)
                source_errors["analysis"] = str(exc)
                analysis_result["error"]  = str(exc)

    # ── Step 06: RMarkdown 리포트 렌더링 ──────────────────────────────
    report_result: dict = {"skipped": False, "path": None, "error": None}

    run_report = (
        not dry_run
        and not mart_only
        and not fs_only
        and "analysis" not in source_errors
        and analysis_result.get("returncode") == 0
        and os.getenv("REPORT_ENABLED", "true").lower() == "true"
    )

    if not run_report:
        reason = (
            "dry-run" if dry_run else
            "mart-only/fs-only" if (mart_only or fs_only) else
            "분석 실패" if "analysis" in source_errors else
            "REPORT_ENABLED=false"
        )
        logger.info(f"[06] 리포트 렌더링 건너뜀 ({reason})")
        report_result["skipped"] = True
    else:
        import subprocess, shutil
        rscript_bin = shutil.which("Rscript")
        if not rscript_bin:
            logger.warning("[06] Rscript 미설치 — 리포트 렌더링 건너뜀")
            report_result["skipped"] = True
        else:
            report_dir = os.getenv(
                "REPORT_SCRIPT_DIR",
                os.path.join(os.path.dirname(__file__), "report")
            )
            r_env = {
                **os.environ,
                "PIPELINE_RUN_ID": run_id,
                "PERIOD_START":    period_start or "",
                "PERIOD_END":      period_end   or "",
            }
            r_cmd = [rscript_bin, "render_report.R"]

            logger.info(f"[06] 리포트 렌더링 시작  dir={report_dir}  run_id={run_id}")
            try:
                proc = subprocess.run(
                    r_cmd,
                    cwd            = report_dir,
                    env            = r_env,
                    capture_output = True,
                    text           = True,
                    timeout        = int(os.getenv("REPORT_TIMEOUT_SEC", "1800")),
                )
                report_result["returncode"] = proc.returncode
                # render_report.R 마지막 줄에 출력 파일 경로를 cat()으로 출력
                out_path = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None
                if proc.returncode != 0:
                    logger.error(
                        f"[06] 리포트 렌더링 실패 (exit={proc.returncode})\n"
                        f"{proc.stderr[-2000:]}"
                    )
                    source_errors["report"] = f"Rscript exit={proc.returncode}"
                else:
                    report_result["path"] = out_path
                    logger.info(f"[06] 리포트 생성 완료: {out_path}")
            except subprocess.TimeoutExpired:
                logger.error("[06] 리포트 렌더링 타임아웃")
                source_errors["report"] = "timeout"
                report_result["error"]  = "timeout"
            except Exception as exc:
                logger.error(f"[06] 리포트 렌더링 오류: {exc}", exc_info=True)
                source_errors["report"] = str(exc)
                report_result["error"]  = str(exc)
    finished_at  = datetime.now(timezone.utc)
    elapsed_sec  = (finished_at - started_at).total_seconds()

    # ── 상태 결정 ─────────────────────────────────────────────────────
    # SUCCESS        : 오류 없음
    # PARTIAL_FAILURE: 일부 소스/변환/mart 실패, BQ 적재는 완료
    # FAILURE        : BQ 적재 자체 실패 또는 유효 레코드 0건
    bq_failed  = "bq_load" in source_errors or "bq_read" in source_errors
    no_records = (not mart_only and not fs_only) and val_report["valid_count"] == 0
    has_error  = bool(source_errors)

    if not has_error:
        status = "SUCCESS"
    elif bq_failed or no_records:
        status = "FAILURE"
    else:
        status = "PARTIAL_FAILURE"

    summary = {
        "run_id":           run_id,
        "period_start":     period_start,
        "period_end":       period_end,
        "sources":          sources,
        "started_at":       started_at.isoformat(),
        "finished_at":      finished_at.isoformat(),
        "elapsed_sec":      round(elapsed_sec, 1),
        # 모드
        "transform_only":     transform_only,
        "mart_only":          mart_only,
        "fs_only":            fs_only,
        # 수집
        "total_raw":          len(all_raw),
        "total_valid":        val_report["valid_count"],
        "total_invalid":      val_report["invalid_count"],
        # 변환
        "transform_enabled":  do_transform,
        "derived_count":      transform_report.get("derived_count", 0),
        "outlier_marked":     transform_report.get("clean", {}).get("outlier_count", 0),
        "total_bq_ready":     len(bq_ready),
        # BQ
        "bq_staged":          bq_result["staged"],
        "bq_dedup_dropped":   bq_result["dropped"],
        "bq_merged":          bq_result["merged"],
        # Mart
        "mart_created":       mart_result["created"],
        "mart_failed":        mart_result["failed"],
        # Feature Store
        "fs_created":         fs_result["created"],
        "fs_failed":          fs_result["failed"],
        # Analysis
        "analysis_skipped":   analysis_result.get("skipped", True),
        "analysis_returncode":analysis_result.get("returncode"),
        # Report
        "report_skipped":     report_result.get("skipped", True),
        "report_path":        report_result.get("path"),
        # 메타
        "source_meta":        source_meta,
        "validation_errors":  val_report.get("errors_by_source", {}),
        "errors":             source_errors,
        "status":             status,
        "dry_run":            dry_run,
    }

    logger.info("[05] 실행 요약:")
    logger.info(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    try:
        slack_sent = notify_slack(summary)
    except Exception as exc:
        logger.warning(f"[05] Slack 알림 실패 (비치명): {exc}")
        slack_sent = False

    summary["slack_notified"] = slack_sent

    if status == "FAILURE" and not dry_run:
        logger.error(f"[Pipeline] FAILURE — {list(source_errors.keys())}")
        sys.exit(1)
    elif status == "PARTIAL_FAILURE":
        logger.warning(f"[Pipeline] PARTIAL_FAILURE — 실패 소스: {list(source_errors.keys())}")

    return summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="고용동향 파이프라인 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py                                   # 전체 소스, 자동 기간
  python main.py --source kosis bls                # 특정 소스만
  python main.py --dry-run                         # 수집+변환만, BQ 적재 없음
  python main.py --no-transform                    # 변환 스킵, 원본만 적재
  python main.py --period 2024-01 2025-12          # 수집 기간 직접 지정
  python main.py --transform-only                  # BQ 원본 → 변환 → 재적재
  python main.py --transform-only --source kosis   # 특정 소스 원본만 재변환
  python main.py --mart-only                       # Mart View만 갱신 (FS 제외)
  python main.py --fs-only                         # Feature Store View만 갱신
  python main.py --no-mart                         # Mart·FS 갱신 모두 건너뜀
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
        help="BQ 적재 없이 수집 + 변환만 실행",
    )
    p.add_argument(
        "--no-transform",
        action="store_true",
        default=False,
        help="파생 지표 계산 스킵, 원본 레코드만 적재",
    )
    p.add_argument(
        "--transform-only",
        action="store_true",
        default=False,
        help=(
            "API 수집 없이 BQ Raw 테이블의 원본 레코드를 읽어 "
            "변환(파생 지표) 후 재적재."
        ),
    )
    p.add_argument(
        "--mart-only",
        action="store_true",
        default=False,
        help="수집·변환·BQ 적재를 건너뛰고 Mart View 갱신만 실행 (Feature Store 제외).",
    )
    p.add_argument(
        "--fs-only",
        action="store_true",
        default=False,
        help="수집·변환·BQ 적재·Mart를 건너뛰고 Feature Store View 갱신만 실행.",
    )
    p.add_argument(
        "--no-mart",
        action="store_true",
        default=False,
        help="Mart 및 Feature Store View 갱신을 모두 건너뜀.",
    )
    p.add_argument(
        "--period",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="수집/조회 기간 직접 지정 (YYYY-MM YYYY-MM). 예: --period 2024-01 2025-12",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # 상호 배제 검사
    if args.transform_only and args.no_transform:
        print("오류: --transform-only와 --no-transform은 함께 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(2)
    if args.mart_only and args.no_mart:
        print("오류: --mart-only와 --no-mart는 함께 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(2)
    if args.fs_only and args.no_mart:
        print("오류: --fs-only와 --no-mart는 함께 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(2)
    if args.mart_only and args.fs_only:
        print("오류: --mart-only와 --fs-only는 함께 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(2)

    period = tuple(args.period) if args.period else None
    run_pipeline(
        sources         = args.source,
        dry_run         = args.dry_run,
        no_transform    = args.no_transform,
        transform_only  = args.transform_only,
        mart_only       = args.mart_only,
        fs_only         = args.fs_only,
        no_mart         = args.no_mart,
        period_override = period,
    )
    # 대시보드 
    try:
        export_dashboard_data()
    except Exception as e:
        logger.warning(f"[Dashboard] 데이터 export 실패 (비치명): {e}")


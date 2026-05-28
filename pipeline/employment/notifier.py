"""
notifier.py
─────────────────────────────────────────────────────────────────────────────
Slack 알림 모듈

SLACK_WEBHOOK_URL 환경변수가 없으면 조용히 스킵.
성공 / 실패 상태에 따라 색상이 다른 Block Kit 메시지를 전송.

summary 딕셔너리 기대 필드 (main.run_pipeline 반환값 기준):
  run_id, period_start, period_end, sources, elapsed_sec, dry_run
  total_raw, total_valid, total_invalid
  transform_enabled, derived_count, outlier_marked, total_bq_ready
  bq_staged, bq_merged
  source_meta, validation_errors, errors, status
"""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)



# ── 상태별 색상 (Slack attachment color) ─────────────────────────────────────
STATUS_COLOR = {
    "SUCCESS":         "#36a64f",   # 초록
    "PARTIAL_FAILURE": "#ff9800",   # 주황
    "FAILURE":         "#e53935",   # 빨강
}

# ── 상태별 이모지 ────────────────────────────────────────────────────────────
STATUS_EMOJI = {
    "SUCCESS":         "✅",
    "PARTIAL_FAILURE": "⚠️",
    "FAILURE":         "❌",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 포매터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _fmt_errors(errors: dict) -> str:
    """오류 딕셔너리 → 읽기 좋은 문자열."""
    if not errors:
        return "없음"
    return "\n".join(f"  • `{k}`: {v[:120]}..." if len(str(v)) > 120
                     else f"  • `{k}`: {v}"
                     for k, v in errors.items())


def _fmt_source_meta(meta: dict) -> str:
    """소스별 수집 현황 딕셔너리 → 읽기 좋은 문자열."""
    lines = []
    for source, counts in meta.items():
        inner = "  /  ".join(f"{k}: *{v:,}*" for k, v in counts.items())
        lines.append(f"  • `{source}` — {inner}")
    return "\n".join(lines) if lines else "없음"


def _fmt_val_errors(errors_by_source: dict) -> str:
    """검증 오류 소스별 건수 → 문자열."""
    if not errors_by_source:
        return "없음"
    return "  " + ",  ".join(f"`{s}`: {n}건" for s, n in errors_by_source.items())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메시지 빌드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _build_payload(summary: dict) -> dict:
    """
    Slack Incoming Webhook payload 생성.
    attachments + fields 조합으로 구성.
    """
    status      = summary.get("status", "UNKNOWN")
    color       = STATUS_COLOR.get(status, "#888888")
    emoji       = STATUS_EMOJI.get(status, "❓")
    dry_run     = summary.get("dry_run", False)
    tf_enabled  = summary.get("transform_enabled", False)
    errors      = summary.get("errors", {})
    source_meta = summary.get("source_meta", {})
    val_errors  = summary.get("validation_errors", {})

    # ── 제목 ────────────────────────────────────────────────────────────
    tags = []
    if dry_run:
        tags.append("DRY-RUN")
    if summary.get("mart_only"):
        tags.append("MART-ONLY")
    if summary.get("fs_only"):
        tags.append("FS-ONLY")
    if summary.get("transform_only"):
        tags.append("TRANSFORM-ONLY")
    elif not tf_enabled:
        tags.append("NO-TRANSFORM")
    tag_str = f" [{' | '.join(tags)}]" if tags else ""
    title = f"{emoji} 고용동향 파이프라인{tag_str} — {status}"

    # ── fields: 핵심 지표 (2열 그리드) ──────────────────────────────────
    fields = [
        {
            "title": "수집 기간",
            "value": f"{summary.get('period_start')} ~ {summary.get('period_end')}",
            "short": True,
        },
        {
            "title": "소요 시간",
            "value": f"{summary.get('elapsed_sec', 0):.1f}초",
            "short": True,
        },
        # ── 수집 ─────────────────────────────────────────────────────────
        {
            "title": "수집 (원시)",
            "value": f"{summary.get('total_raw', 0):,}건",
            "short": True,
        },
        {
            "title": "검증 통과 / 실패",
            "value": (
                f"{summary.get('total_valid', 0):,}건 "
                f"/ {summary.get('total_invalid', 0):,}건"
            ),
            "short": True,
        },
        # ── Transform ────────────────────────────────────────────────────
        {
            "title": "파생 지표 생성",
            "value": (
                f"{summary.get('derived_count', 0):,}건"
                if tf_enabled else "스킵"
            ),
            "short": True,
        },
        {
            "title": "이상치 마킹",
            "value": (
                f"{summary.get('outlier_marked', 0):,}건"
                if tf_enabled else "스킵"
            ),
            "short": True,
        },
        # ── BQ 적재 ──────────────────────────────────────────────────────
        {
            "title": "BQ 적재 대상",
            "value": f"{summary.get('total_bq_ready', 0):,}건",
            "short": True,
        },
        {
            "title": "BQ MERGE 완료",
            "value": (
                f"staged {summary.get('bq_staged', 0):,} / "
                f"dedup 제거 {summary.get('bq_dedup_dropped', 0):,} / "
                f"merged {summary.get('bq_merged', 0):,}"
            ),
            "short": True,
        },
        # ── Mart ─────────────────────────────────────────────────────────
        {
            "title": "Mart View",
            "value": (
                f"갱신 {len(summary.get('mart_created', []))}개"
                + (
                    f" / 실패 {len(summary.get('mart_failed', {}))}: "
                    + ", ".join(summary.get("mart_failed", {}).keys())
                    if summary.get("mart_failed")
                    else ""
                )
            ),
            "short": True,
        },
        # ── Feature Store ─────────────────────────────────────────────────
        {
            "title": "Feature Store",
            "value": (
                f"갱신 {len(summary.get('fs_created', []))}개"
                + (
                    f" / 실패 {len(summary.get('fs_failed', {}))}: "
                    + ", ".join(summary.get("fs_failed", {}).keys())
                    if summary.get("fs_failed")
                    else ""
                )
            ),
            "short": True,
        },
        # ── Report ────────────────────────────────────────────────────────
        {
            "title": "리포트",
            "value": (
                "건너뜀" if summary.get("report_skipped")
                else (
                    f"✅ {summary.get('report_path', '경로 미확인')}"
                    if not summary.get("errors", {}).get("report")
                    else f"❌ 실패: {summary['errors']['report']}"
                )
            ),
            "short": False,
        },
    ]

    # ── 본문 텍스트 블록 ─────────────────────────────────────────────────
    text_blocks = [
        f"*run_id*: `{summary.get('run_id', '-')}`",
        f"*소스*: {', '.join(summary.get('sources', []))}",
        "",
        f"*소스별 수집 현황*:\n{_fmt_source_meta(source_meta)}",
    ]

    if val_errors:
        text_blocks += [
            "",
            f"*검증 오류 (소스별)*:\n{_fmt_val_errors(val_errors)}",
        ]

    if errors:
        text_blocks += [
            "",
            f"*파이프라인 오류*:\n{_fmt_errors(errors)}",
        ]

    return {
        "attachments": [
            {
                "color":     color,
                "title":     title,
                "text":      "\n".join(text_blocks),
                "fields":    fields,
                "footer":    "Employment Analytics Pipeline",
                "ts":        int(time.time()),
                "mrkdwn_in": ["text", "fields"],
            }
        ]
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 인터페이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def notify_slack(summary: dict) -> bool:
    """
    파이프라인 실행 결과를 Slack에 전송.

    Parameters
    ----------
    summary : main.run_pipeline()이 반환하는 결과 딕셔너리

    Returns
    -------
    bool : 전송 성공 여부 (URL 미설정 포함 전송 안 된 경우 False)

    동작:
      - SLACK_WEBHOOK_URL 미설정 → WARNING 로그 + False 반환
      - 전송 실패 → WARNING 로그 + False 반환 (파이프라인 중단 없음)
    """
    # 모듈 import 시점이 아닌 호출 시점에 환경변수를 읽어
    # Cloud Run Job 등에서 뒤늦게 주입된 값도 반영되도록 한다.
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(
            "[Notifier] SLACK_WEBHOOK_URL 미설정 — Slack 알림 건너뜀. "
            "알림을 받으려면 SLACK_WEBHOOK_URL 환경변수를 설정하세요."
        )
        return False

    payload = _build_payload(summary)

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[Notifier] Slack 알림 전송 완료")
        return True
    except requests.RequestException as exc:
        logger.warning(f"[Notifier] Slack 알림 전송 실패: {exc}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단독 실행 (메시지 미리보기)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import json

    # 성공 케이스
    sample_success = {
        "run_id":           "550e8400-e29b-41d4-a716-446655440000",
        "period_start":     "2021-02",
        "period_end":       "2026-02",
        "sources":          ["kosis", "naver", "bls", "google"],
        "elapsed_sec":      142.3,
        "dry_run":          False,
        "total_raw":        48320,
        "total_valid":      48301,
        "total_invalid":    19,
        "transform_enabled": True,
        "derived_count":    241505,
        "outlier_marked":   12,
        "total_bq_ready":   289806,
        "bq_staged":        289806,
        "bq_merged":        289806,
        "source_meta": {
            "kosis":  {"kosis_emp_count": 38400, "kosis_wage_count": 7200, "kosis_benefit_count": 2720},
            "naver":  {"datalab_count": 540, "jobpost_count": 300, "news_count": 60},
            "bls":    {"bls_count": 3000},
            "google": {"google_trends_count": 180},
        },
        "validation_errors": {"naver_news": 19},
        "errors":           {},
        "status":           "SUCCESS",
    }

    # 실패 케이스
    sample_failure = {
        **sample_success,
        "errors":  {"bls": "ConnectionError: Max retries exceeded"},
        "status":  "FAILURE",
        "dry_run": True,
    }

    for label, s in [("SUCCESS", sample_success), ("FAILURE", sample_failure)]:
        payload = _build_payload(s)
        print(f"\n{'='*60}")
        print(f"[ {label} 케이스 페이로드 ]")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

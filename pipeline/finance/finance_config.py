"""
finance_config.py
─────────────────────────────────────────────────────────────────────────────
금융동향 파이프라인 공통 설정 및 환경변수 관리
"""

import os
from datetime import date
from dateutil.relativedelta import relativedelta

# ── API 인증 정보 ──────────────────────────────────────────────────────────
ECOS_API_KEY   = os.getenv("ECOS_API_KEY",   "")
FRED_API_KEY   = os.getenv("FRED_API_KEY",   "")
KOSIS_API_KEY  = os.getenv("KOSIS_API_KEY",  "")   # 고용동향과 공유
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
BQ_DATASET     = os.getenv("FINANCE_BQ_DATASET", "finance_stats")
BQ_RAW_TABLE   = os.getenv("FINANCE_BQ_RAW_TABLE", "finance_raw")
BQ_STG_TABLE   = os.getenv("FINANCE_BQ_STG_TABLE", "finance_stg")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ── 수집 기간 결정 (고용동향과 동일한 규칙) ──────────────────────────────
def get_period_range(ref_date: date | None = None) -> tuple[str, str]:
    today = ref_date or date.today()
    n = today.day
    end   = today.replace(day=1) - relativedelta(months=1)
    start = (end - relativedelta(years=5)) if n < 15 \
            else (today.replace(day=1) - relativedelta(years=5))
    return start.strftime("%Y-%m"), end.strftime("%Y-%m")


def set_env_periods(ref_date: date | None = None) -> tuple[str, str]:
    start, end = get_period_range(ref_date)
    os.environ["PERIOD_START"] = start
    os.environ["PERIOD_END"]   = end
    return start, end


def get_env_periods() -> tuple[str, str]:
    start = os.environ.get("PERIOD_START", "")
    end   = os.environ.get("PERIOD_END",   "")
    if not start or not end:
        raise EnvironmentError(
            "PERIOD_START / PERIOD_END 환경변수가 설정되지 않았습니다."
        )
    return start, end

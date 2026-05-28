"""
config.py
─────────────────────────────────────────────────────────────────────────────
공통 설정, 환경변수 관리, 수집 기간 결정 로직

━━━ 수집 기간 결정 규칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  실행일 n일 기준:
    공통:   end   = 전월 (항상)
    n < 15: start = end 기준 -5년     (≈ 60개월)
    n ≥ 15: start = 당월 기준 -5년   (≈ 61개월)

  예) 2026년 3월 10일 실행 (n=10, n<15):
      end   = 2026-02
      start = 2021-02

  예) 2026년 3월 20일 실행 (n=20, n≥15):
      end   = 2026-02
      start = 2021-03

━━━ Transform 설정 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TRANSFORM_ENABLED : True이면 파생 지표 계산 수행 (기본 True)
  TRANSFORM_IQR_K   : 이상치 판정 배수 (기본 3.0)
"""

import os
from datetime import date
from dateutil.relativedelta import relativedelta


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 인증 정보 (환경변수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KOSIS_API_KEY       = os.getenv("KOSIS_API_KEY", "")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
BLS_API_KEY         = os.getenv("BLS_API_KEY", "")
GCP_PROJECT_ID      = os.getenv("GCP_PROJECT_ID", "")
BQ_DATASET          = os.getenv("BQ_DATASET",   "kosis_stats")
BQ_RAW_TABLE        = os.getenv("BQ_RAW_TABLE", "employment_raw")
BQ_STG_TABLE        = os.getenv("BQ_STG_TABLE", "employment_stg")
SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Transform 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRANSFORM_ENABLED = os.getenv("TRANSFORM_ENABLED", "true").lower() != "false"
TRANSFORM_IQR_K   = float(os.getenv("TRANSFORM_IQR_K", "3.0"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 수집 기간 결정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_period_range(ref_date: date | None = None) -> tuple[str, str]:
    """
    실행일 기준으로 (PERIOD_START, PERIOD_END)를 계산.

    Returns
    -------
    (period_start, period_end) : "YYYY-MM" 형식 튜플
    """
    today = ref_date or date.today()
    n = today.day

    # end: 항상 전월
    end = today.replace(day=1) - relativedelta(months=1)

    # start: n < 15이면 end 기준 -5년, n ≥ 15이면 당월 기준 -5년
    if n < 15:
        start = end - relativedelta(years=5)
    else:
        start = today.replace(day=1) - relativedelta(years=5)

    return start.strftime("%Y-%m"), end.strftime("%Y-%m")


def set_env_periods(ref_date: date | None = None) -> tuple[str, str]:
    """
    PERIOD_START / PERIOD_END 환경변수를 주입하고 값을 반환.
    Cloud Run Job 진입점 또는 main.py에서 최초 1회 호출.
    """
    start, end = get_period_range(ref_date)
    os.environ["PERIOD_START"] = start
    os.environ["PERIOD_END"]   = end
    return start, end


def get_env_periods() -> tuple[str, str]:
    """환경변수에서 수집 기간을 읽어 반환."""
    start = os.environ.get("PERIOD_START", "")
    end   = os.environ.get("PERIOD_END",   "")
    if not start or not end:
        raise EnvironmentError(
            "PERIOD_START / PERIOD_END 환경변수가 설정되지 않았습니다. "
            "set_env_periods()를 먼저 호출하세요."
        )
    return start, end


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def period_to_year_month(period: str) -> tuple[int, int]:
    """'YYYY-MM' → (int year, int month)"""
    y, m = period.split("-")
    return int(y), int(m)

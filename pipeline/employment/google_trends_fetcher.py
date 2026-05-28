"""
google_trends_fetcher.py
─────────────────────────────────────────────────────────────────────────────
Google Trends 수집 모듈 (pytrends 라이브러리 사용)

수집 대상
  키워드: 취업, 이직, 채용
  지역: KR
  단위: 월별 상대 검색빈도 (0~100)
  변수명: gtrend_{keyword}

━━━ 알려진 제약 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Google은 GCP / Cloud Run 등 데이터센터 IP의 pytrends 요청을 차단할 수 있다.
  차단 시 예외 없이 빈 DataFrame을 반환하므로 로그에 shape=(0,0)이 찍힌다.
  이 경우 파이프라인은 정상 완료되며 google_trends_count=0으로 기록된다.

  해결 방법 (우선순위):
    1. Cloud Run 환경에서 Cloud NAT + 고정 IP 할당 후 pytrends 실행
    2. proxies 파라미터에 주거용 프록시(Residential Proxy) 설정
    3. SerpAPI / DataForSEO 등 유료 API로 대체
    4. 현재 상태 유지: Naver DataLab으로 국내 검색 트렌드 대체 (권장)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
import logging
from datetime import datetime, timezone
from calendar import monthrange

import pandas as pd
from pytrends.request import TrendReq

from config import get_env_periods

logger = logging.getLogger(__name__)

KEYWORDS = {
    "employment": "취업",
    "job_change":  "이직",
    "recruitment": "채용",
}

GEO = "KR"

# 브라우저처럼 보이도록 User-Agent 설정
_REQUESTS_ARGS = {
    "headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
}


def _period_range_to_timeframe(period_start: str, period_end: str) -> str:
    """
    'YYYY-MM' → pytrends timeframe 형식 'YYYY-MM-DD YYYY-MM-DD'

    pytrends 반환 granularity:
      < ~3개월  → 일별
      ~3개월~5년 → 주별  ← 우리 케이스 (5년 기간)
      > 5년    → 월별
    """
    y_e, m_e = map(int, period_end.split("-"))
    last_day  = monthrange(y_e, m_e)[1]
    return f"{period_start}-01 {period_end}-{last_day:02d}"


def _aggregate_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """
    pytrends가 반환하는 주별(weekly) DataFrame을 월별 평균으로 집계.

    주별 데이터는 같은 달에 여러 행이 존재하므로,
    Period("M") 기준으로 groupby 후 mean()을 적용한다.
    """
    if df.empty:
        return df

    df = df.copy()
    # DatetimeIndex → PeriodIndex(M)
    df.index = df.index.to_period("M")
    df_monthly = df.groupby(df.index).mean(numeric_only=True)
    return df_monthly


def fetch_google_trends(run_id: str) -> list[dict]:
    """
    Google Trends 월별 검색 상대빈도 수집.

    Returns
    -------
    list[dict] : 정규화된 레코드 목록 (차단·실패 시 빈 리스트)
    """
    period_start, period_end = get_env_periods()
    timeframe   = _period_range_to_timeframe(period_start, period_end)
    ingested_at = datetime.now(timezone.utc).isoformat()
    results     = []
    df          = None

    pytrends = TrendReq(
        hl="ko-KR",
        tz=540,
        timeout=(15, 45),
        retries=3,
        backoff_factor=0.5,
        requests_args=_REQUESTS_ARGS,
    )

    kw_keys = list(KEYWORDS.keys())
    kw_vals = list(KEYWORDS.values())

    logger.info(
        f"[Google Trends] 수집 시작 | timeframe: {timeframe} | 키워드: {kw_vals}"
    )

    for attempt in range(1, 4):
        try:
            pytrends.build_payload(
                kw_list=kw_vals,
                cat=0,
                timeframe=timeframe,
                geo=GEO,
                gprop="",
            )
            df = pytrends.interest_over_time()

            if df is None:
                raise ValueError("interest_over_time() returned None")

            logger.info(
                f"[Google Trends] 응답 수신 (시도 {attempt}/3) "
                f"| shape={df.shape} | columns={list(df.columns)}"
            )
            break

        except Exception as e:
            logger.warning(
                f"[Google Trends] 시도 {attempt}/3 실패: {type(e).__name__}: {e}"
            )
            if attempt < 3:
                time.sleep(10 * attempt)
            else:
                logger.error(
                    "[Google Trends] 최종 실패 — "
                    "Cloud Run IP 차단 가능성 높음 (docs/decisions.md 참고)"
                )
                return []

    # df가 빈 경우: IP 차단 또는 rate limit
    if df is None or df.empty:
        logger.warning(
            f"[Google Trends] 빈 응답 반환 | shape={df.shape if df is not None else 'None'} "
            "— GCP 데이터센터 IP 차단 의심. Naver DataLab으로 대체 권장."
        )
        return []

    # isPartial 컬럼 제거 (현재 기간 데이터는 부분 집계이므로 제외)
    df = df.drop(columns=["isPartial"], errors="ignore")

    raw_rows = len(df)

    # 주별 → 월별 집계
    df_monthly = _aggregate_to_monthly(df)

    logger.info(
        f"[Google Trends] 월별 집계: 원본 {raw_rows}행 → {len(df_monthly)}개월"
    )

    for period_idx, row in df_monthly.iterrows():
        # PeriodIndex → "YYYY-MM"
        period = str(period_idx)

        # 수집 기간 필터
        if not (period_start <= period <= period_end):
            continue

        for kw_key, kw_val in zip(kw_keys, kw_vals):
            value = row.get(kw_val)
            results.append({
                "source":          "google_trends",
                "period":          period,
                "variable_name":   f"gtrend_{kw_key}",
                "category_key":    kw_key,
                "value":           float(value) if value is not None else None,
                "adjustment_type": "index",
                "ingested_at":     ingested_at,
                "run_id":          run_id,
                "category_name":   kw_val,
            })

    logger.info(f"[Google Trends] 수집 완료: {len(results)}건")
    return results

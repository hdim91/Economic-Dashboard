"""
naver_fetcher.py
─────────────────────────────────────────────────────────────────────────────
NAVER API 수집 모듈

① DataLab 검색어 트렌드 API  — 연령×성별 검색 상대빈도
   키워드: '실업', '채용', '이직'
   변수명: search_trend_{keyword}_{gender}_{agegroup}

② 검색 API (뉴스) — 채용공고 건수
   변수명: job_posting_count_{sector}

③ 검색 API (뉴스) — 뉴스 트렌드 건수 + 감성지수
   변수명: news_count_{topic}, news_sentiment_employment
"""

import time
import logging
from datetime import datetime, timezone
from itertools import product

import requests

from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, get_env_periods

logger = logging.getLogger(__name__)

# ── 상수 ────────────────────────────────────────────────────────────────────
DATALAB_URL  = "https://openapi.naver.com/v1/datalab/search"
SEARCH_URL   = "https://openapi.naver.com/v1/search/news.json"

NAVER_HEADERS = {
    "X-Naver-Client-Id":     NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    "Content-Type":          "application/json",
}

# DataLab 연령 그룹 코드 → 변수명 suffix
AGE_GROUPS = {
    "1": "0s",   # 0-12
    "2": "10s",  # 13-18
    "3": "20s_early",  # 19-24
    "4": "20s_late",   # 25-29
    "5": "30s",  # 30-39
    "6": "40s",  # 40-49
    "7": "50s",  # 50-59
    "8": "60s",  # 60+
}

GENDERS = {"m": "m", "f": "f"}

# DataLab 수집 키워드
SEARCH_KEYWORDS = {
    "unemployment": "실업",
    "recruitment":  "채용",
    "job_change":   "이직",
}

# 채용공고 수집 키워드 (검색 API)
# JOB_POSTING_KEYWORDS = {
#     "total":         "채용공고",
#     "it":            "IT 개발자 채용",
#     "manufacturing": "제조 생산직 채용",
#     "service":       "서비스 판매 채용",
#     "finance":       "금융 보험 채용",
# }

# 뉴스 수집 키워드 (검색 API)
# NEWS_KEYWORDS = {
#     "employment":   "고용 취업",
#     "unemployment": "실업 실직",
#     "layoff":       "해고 감원 구조조정",
# }


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────
def _period_to_date_str(period: str, end: bool = False) -> str:
    """
    'YYYY-MM' → 'YYYY-MM-DD'
    end=False → 1일, end=True → 말일
    """
    from calendar import monthrange
    y, m = map(int, period.split("-"))
    if end:
        last_day = monthrange(y, m)[1]
        return f"{y:04d}-{m:02d}-{last_day:02d}"
    return f"{y:04d}-{m:02d}-01"


def _make_ingested_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_request(method: str, url: str, retries: int = 3, backoff: float = 2.0, **kwargs) -> requests.Response:
    for attempt in range(1, retries + 1):
        try:
            resp = getattr(requests, method)(url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"[NAVER] 요청 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                raise
    raise RuntimeError("unreachable")


# ── ① NAVER DataLab 검색어 트렌드 ────────────────────────────────────────────
def _build_datalab_payload(
    keyword_name: str,
    keyword_ko:   str,
    start_date:   str,
    end_date:     str,
    gender:       str,
    age:          str,
) -> dict:
    return {
        "startDate": start_date,
        "endDate":   end_date,
        "timeUnit":  "month",
        "keywordGroups": [
            {"groupName": keyword_name, "keywords": [keyword_ko]}
        ],
        "gender": gender,
        "ages":   [age],
    }


def fetch_datalab_trends(run_id: str) -> list[dict]:
    """
    DataLab API: 키워드 × 성별 × 연령별 월별 검색 상대빈도 수집.
    변수명: search_trend_{keyword}_{gender}_{agegroup}
    """
    period_start, period_end = get_env_periods()
    start_date = _period_to_date_str(period_start, end=False)
    end_date   = _period_to_date_str(period_end,   end=True)
    ingested_at = _make_ingested_at()
    results = []

    total_calls = len(SEARCH_KEYWORDS) * len(GENDERS) * len(AGE_GROUPS)
    call_count  = 0

    for (kw_key, kw_ko), (gender_key, gender_val), (age_key, age_suffix) in product(
        SEARCH_KEYWORDS.items(), GENDERS.items(), AGE_GROUPS.items()
    ):
        variable_name = f"search_trend_{kw_key}_{gender_key}_{age_suffix}"
        call_count += 1
        logger.info(f"[DataLab] ({call_count}/{total_calls}) {variable_name}")

        payload = _build_datalab_payload(
            keyword_name=kw_key,
            keyword_ko=kw_ko,
            start_date=start_date,
            end_date=end_date,
            gender=gender_val,
            age=age_key,
        )

        try:
            resp = _retry_request(
                "post", DATALAB_URL,
                headers=NAVER_HEADERS,
                json=payload,
            )
            data = resp.json()

            # 응답: {"results": [{"title": ..., "data": [{"period": "YYYY-MM-DD", "ratio": 0.0~100.0}]}]}
            for result in data.get("results", []):
                for point in result.get("data", []):
                    period_raw = point.get("period", "")[:7]  # YYYY-MM
                    ratio = point.get("ratio")
                    results.append({
                        "source":          "naver_datalab",
                        "period":          period_raw,
                        "variable_name":   variable_name,
                        "category_key":    f"{gender_key}|{age_suffix}",
                        "value":           float(ratio) if ratio is not None else None,
                        "adjustment_type": "index",
                        "ingested_at":     ingested_at,
                        "run_id":          run_id,
                        "category_name":   f"성별:{gender_key} 연령:{age_suffix}",
                    })

        except Exception as e:
            logger.error(f"[DataLab] {variable_name} 실패: {e}")

        time.sleep(0.3)  # DataLab API rate limit

    logger.info(f"[DataLab] 수집 완료: {len(results)}건")
    return results


# ── ② NAVER 검색 API — 채용공고 건수 ────────────────────────────────────────
def _fetch_search_total(query: str) -> int | None:
    """검색 API에서 결과 total 건수만 반환."""
    try:
        resp = _retry_request(
            "get", SEARCH_URL,
            headers=NAVER_HEADERS,
            params={"query": query, "display": 1, "start": 1, "sort": "sim"},
        )
        return resp.json().get("total")
    except Exception as e:
        logger.warning(f"[NAVER Search] '{query}' 조회 실패: {e}")
        return None


def fetch_job_postings(run_id: str) -> list[dict]:
    """
    검색 API로 월별 직종별 채용공고 건수를 수집.
    결과 total을 상대 지수화 (전체 대비 비율) 하여 저장.
    """
    from calendar import monthrange
    import datetime as dt

    period_start, period_end = get_env_periods()
    ingested_at = _make_ingested_at()
    results = []

    # 기간 내 월 목록 생성
    y_s, m_s = map(int, period_start.split("-"))
    y_e, m_e = map(int, period_end.split("-"))
    periods: list[str] = []
    y, m = y_s, m_s
    while (y, m) <= (y_e, m_e):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1; y += 1

    for period in periods:
        logger.info(f"[NAVER JobPost] {period} 수집 중")
        totals = {}

        for sector_key, keyword in JOB_POSTING_KEYWORDS.items():
            query = f"{keyword} {period[:4]}년 {int(period[5:7])}월"
            count = _fetch_search_total(query)
            totals[sector_key] = count
            time.sleep(0.2)

        # 정규화: total 대비 비율 산출 (전체가 없으면 None)
        base = totals.get("total") or 1

        for sector_key, count in totals.items():
            variable_name = f"job_posting_count_{sector_key}"
            results.append({
                "source":          "naver_jobpost",
                "period":          period,
                "variable_name":   variable_name,
                "category_key":    sector_key,
                "value":           float(count) if count is not None else None,
                "adjustment_type": "raw",
                "ingested_at":     ingested_at,
                "run_id":          run_id,
                "category_name":   JOB_POSTING_KEYWORDS.get(sector_key, sector_key),
            })

    logger.info(f"[NAVER JobPost] 수집 완료: {len(results)}건")
    return results


# ── ③ NAVER 검색 API — 뉴스 트렌드 건수 ─────────────────────────────────────
def fetch_news_trends(run_id: str) -> list[dict]:
    """
    검색 API로 월별 고용 관련 뉴스 건수 수집.
    감성 분석은 별도 파이프라인(sentiment_analyzer.py)에서 처리.
    """
    period_start, period_end = get_env_periods()
    ingested_at = _make_ingested_at()
    results = []

    y_s, m_s = map(int, period_start.split("-"))
    y_e, m_e = map(int, period_end.split("-"))
    periods: list[str] = []
    y, m = y_s, m_s
    while (y, m) <= (y_e, m_e):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1; y += 1

    for period in periods:
        logger.info(f"[NAVER News] {period} 수집 중")

        for topic_key, keyword in NEWS_KEYWORDS.items():
            query = f"{keyword} {period[:4]}년 {int(period[5:7])}월"
            count = _fetch_search_total(query)
            variable_name = f"news_count_{topic_key}"

            results.append({
                "source":          "naver_news",
                "period":          period,
                "variable_name":   variable_name,
                "category_key":    topic_key,
                "value":           float(count) if count is not None else None,
                "adjustment_type": "raw",
                "ingested_at":     ingested_at,
                "run_id":          run_id,
                "category_name":   NEWS_KEYWORDS.get(topic_key, topic_key),
            })
            time.sleep(0.2)

    logger.info(f"[NAVER News] 수집 완료: {len(results)}건")
    return results


# ── 전체 수집 진입점 ─────────────────────────────────────────────────────────
def fetch_all_naver(run_id: str) -> dict[str, list[dict]]:
    """
    NAVER 전체 수집 실행.

    Returns
    -------
    {
        "naver_datalab": [...],
        "naver_jobpost": [...],
        "naver_news":    [...],
    }
    """
    logger.info("=" * 60)
    logger.info("[NAVER] DataLab 검색어 트렌드 수집 시작")
    datalab = fetch_datalab_trends(run_id)

    # logger.info("=" * 60)
    # logger.info("[NAVER] 채용공고 건수 수집 시작")
    # jobpost = fetch_job_postings(run_id)

    # logger.info("=" * 60)
    # logger.info("[NAVER] 뉴스 트렌드 수집 시작")
    # news = fetch_news_trends(run_id)

    return {
        "naver_datalab": datalab#,
        #"naver_jobpost": jobpost,
        #"naver_news":    news,
    }

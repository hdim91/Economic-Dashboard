"""
bls_fetcher.py
─────────────────────────────────────────────────────────────────────────────
BLS (Bureau of Labor Statistics) API v2 수집 모듈

수집 대상 (원계열 + 계절조정)
  ① 미국 고용자 수 (Total Nonfarm) — CES
     raw/seasonal_us_nonfarm_employment
     raw/seasonal_us_nonfarm_employment_by_industry

  ② 시간당 임금 (Total Nonfarm Private) — CES
     raw/seasonal_us_hourly_wage
     raw/seasonal_us_weekly_hours
     raw/seasonal_us_weekly_earnings

  ③ 연령별 고용자 수 (Not Seasonally Adjusted) — CPS
     raw/seasonal_us_employment_by_age
     raw/seasonal_us_unemployment_rate_by_age

  ④ 채용공고 및 퇴직 — JOLTS
     raw/seasonal_us_job_openings
     raw/seasonal_us_hires
     raw/seasonal_us_total_separations
     raw/seasonal_us_quits
     raw/seasonal_us_layoffs_discharges

API: https://api.bls.gov/publicAPI/v2/timeseries/data/
"""

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from config import BLS_API_KEY, get_env_periods

logger = logging.getLogger(__name__)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS API v2: 한 번에 최대 50개 시리즈, 20년 범위
BLS_MAX_SERIES_PER_REQUEST = 50


# ── 시리즈 정의 ──────────────────────────────────────────────────────────────
@dataclass
class BlsSeries:
    series_id:     str
    variable_name: str           # prefix 없음 (us_xxx)
    adjustment_type: str         # "raw" | "seasonal"
    category_key:  str
    category_name: str
    survey:        str           # "CES" | "CPS" | "JOLTS"


# ── ① CES: 고용자 수 (Total Nonfarm) ─────────────────────────────────────────
CES_NONFARM_SERIES: list[BlsSeries] = [
    # 전체
    BlsSeries("CES0000000001", "us_nonfarm_employment", "seasonal", "total",          "Total Nonfarm",         "CES"),
    BlsSeries("CEU0000000001", "us_nonfarm_employment", "raw",      "total",          "Total Nonfarm",         "CES"),
    # 산업별 (NAICS 대분류, 계절조정)
    BlsSeries("CES1000000001", "us_nonfarm_employment_by_industry", "seasonal", "mining",         "Mining & Logging",                    "CES"),
    BlsSeries("CES2000000001", "us_nonfarm_employment_by_industry", "seasonal", "construction",   "Construction",                        "CES"),
    BlsSeries("CES3000000001", "us_nonfarm_employment_by_industry", "seasonal", "manufacturing",  "Manufacturing",                       "CES"),
    BlsSeries("CES4000000001", "us_nonfarm_employment_by_industry", "seasonal", "trade_transport","Trade, Transportation & Utilities",   "CES"),
    BlsSeries("CES5000000001", "us_nonfarm_employment_by_industry", "seasonal", "information",    "Information",                         "CES"),
    BlsSeries("CES5500000001", "us_nonfarm_employment_by_industry", "seasonal", "financial",      "Financial Activities",                "CES"),
    BlsSeries("CES6000000001", "us_nonfarm_employment_by_industry", "seasonal", "professional",   "Professional & Business Services",    "CES"),
    BlsSeries("CES6500000001", "us_nonfarm_employment_by_industry", "seasonal", "education",      "Education & Health Services",         "CES"),
    BlsSeries("CES7000000001", "us_nonfarm_employment_by_industry", "seasonal", "leisure",        "Leisure & Hospitality",               "CES"),
    BlsSeries("CES9091000001", "us_nonfarm_employment_by_industry", "seasonal", "government",     "Government",                          "CES"),
    # 산업별 (원계열)
    BlsSeries("CEU1000000001", "us_nonfarm_employment_by_industry", "raw", "mining",         "Mining & Logging",                    "CES"),
    BlsSeries("CEU2000000001", "us_nonfarm_employment_by_industry", "raw", "construction",   "Construction",                        "CES"),
    BlsSeries("CEU3000000001", "us_nonfarm_employment_by_industry", "raw", "manufacturing",  "Manufacturing",                       "CES"),
    BlsSeries("CEU4000000001", "us_nonfarm_employment_by_industry", "raw", "trade_transport","Trade, Transportation & Utilities",   "CES"),
    BlsSeries("CEU5000000001", "us_nonfarm_employment_by_industry", "raw", "information",    "Information",                         "CES"),
    BlsSeries("CEU5500000001", "us_nonfarm_employment_by_industry", "raw", "financial",      "Financial Activities",                "CES"),
    BlsSeries("CEU6000000001", "us_nonfarm_employment_by_industry", "raw", "professional",   "Professional & Business Services",    "CES"),
    BlsSeries("CEU6500000001", "us_nonfarm_employment_by_industry", "raw", "education",      "Education & Health Services",         "CES"),
    BlsSeries("CEU7000000001", "us_nonfarm_employment_by_industry", "raw", "leisure",        "Leisure & Hospitality",               "CES"),
    BlsSeries("CEU9091000001", "us_nonfarm_employment_by_industry", "raw", "government",     "Government",                          "CES"),
]

# ── ② CES: 시간당 임금 (Total Private) ───────────────────────────────────────
CES_WAGE_SERIES: list[BlsSeries] = [
    BlsSeries("CES0500000008", "us_hourly_wage",    "seasonal", "total_private", "Avg Hourly Earnings (SA)",  "CES"),
    BlsSeries("CEU0500000008", "us_hourly_wage",    "raw",      "total_private", "Avg Hourly Earnings (NSA)", "CES"),
    BlsSeries("CES0500000007", "us_weekly_hours",   "seasonal", "total_private", "Avg Weekly Hours (SA)",     "CES"),
    BlsSeries("CEU0500000007", "us_weekly_hours",   "raw",      "total_private", "Avg Weekly Hours (NSA)",    "CES"),
    BlsSeries("CES0500000011", "us_weekly_earnings","seasonal", "total_private", "Avg Weekly Earnings (SA)",  "CES"),
    BlsSeries("CEU0500000011", "us_weekly_earnings","raw",      "total_private", "Avg Weekly Earnings (NSA)", "CES"),
]

# ── ③ CPS: 연령별 고용자 수 ──────────────────────────────────────────────────
# NSA = Not Seasonally Adjusted (LNU prefix)
# SA  = Seasonally Adjusted (LNS prefix) — 일부 연령대만 제공
CPS_AGE_SERIES: list[BlsSeries] = [
    # 고용자 수 — 원계열
    BlsSeries("LNU02000012", "us_employment_by_age", "raw", "16_19", "Employed 16-19 (NSA)", "CPS"),
    BlsSeries("LNU02000036", "us_employment_by_age", "raw", "20_24", "Employed 20-24 (NSA)", "CPS"),
    BlsSeries("LNU02000089", "us_employment_by_age", "raw", "25_34", "Employed 25-34 (NSA)", "CPS"),
    BlsSeries("LNU02000096", "us_employment_by_age", "raw", "35_44", "Employed 35-44 (NSA)", "CPS"),
    BlsSeries("LNU02000103", "us_employment_by_age", "raw", "45_54", "Employed 45-54 (NSA)", "CPS"),
    BlsSeries("LNU02000110", "us_employment_by_age", "raw", "55_64", "Employed 55-64 (NSA)", "CPS"),
    BlsSeries("LNU02000117", "us_employment_by_age", "raw", "65_up", "Employed 65+ (NSA)",   "CPS"),
    # 고용자 수 — 계절조정 (제공 연령대만)
    BlsSeries("LNS12000012", "us_employment_by_age", "seasonal", "16_19", "Employed 16-19 (SA)", "CPS"),
    BlsSeries("LNS12000036", "us_employment_by_age", "seasonal", "20_24", "Employed 20-24 (SA)", "CPS"),
    # 실업률 — 원계열
    BlsSeries("LNU04000012", "us_unemployment_rate_by_age", "raw", "16_19", "Unemp Rate 16-19 (NSA)", "CPS"),
    BlsSeries("LNU04000036", "us_unemployment_rate_by_age", "raw", "20_24", "Unemp Rate 20-24 (NSA)", "CPS"),
    BlsSeries("LNU04000089", "us_unemployment_rate_by_age", "raw", "25_34", "Unemp Rate 25-34 (NSA)", "CPS"),
    BlsSeries("LNU04000096", "us_unemployment_rate_by_age", "raw", "35_44", "Unemp Rate 35-44 (NSA)", "CPS"),
    BlsSeries("LNU04000103", "us_unemployment_rate_by_age", "raw", "45_54", "Unemp Rate 45-54 (NSA)", "CPS"),
    BlsSeries("LNU04000110", "us_unemployment_rate_by_age", "raw", "55_64", "Unemp Rate 55-64 (NSA)", "CPS"),
    # 실업률 — 계절조정 (제공 연령대만)
    BlsSeries("LNS14000012", "us_unemployment_rate_by_age", "seasonal", "16_19", "Unemp Rate 16-19 (SA)", "CPS"),
    BlsSeries("LNS14000036", "us_unemployment_rate_by_age", "seasonal", "20_24", "Unemp Rate 20-24 (SA)", "CPS"),
    BlsSeries("LNS14000089", "us_unemployment_rate_by_age", "seasonal", "25_34", "Unemp Rate 25-34 (SA)", "CPS"),
]

# ── ④ JOLTS: 채용공고 및 퇴직 ────────────────────────────────────────────────
JOLTS_SERIES: list[BlsSeries] = [
    # 계절조정 (JTS prefix)
    BlsSeries("JTS000000000000000JOL", "us_job_openings",       "seasonal", "total", "Job Openings (SA)",        "JOLTS"),
    BlsSeries("JTS000000000000000HIL", "us_hires",              "seasonal", "total", "Hires (SA)",               "JOLTS"),
    BlsSeries("JTS000000000000000TSL", "us_total_separations",  "seasonal", "total", "Total Separations (SA)",   "JOLTS"),
    BlsSeries("JTS000000000000000QUL", "us_quits",              "seasonal", "total", "Quits (SA)",               "JOLTS"),
    BlsSeries("JTS000000000000000LDL", "us_layoffs_discharges", "seasonal", "total", "Layoffs & Discharges (SA)","JOLTS"),
    # 원계열 (JTU prefix)
    BlsSeries("JTU000000000000000JOL", "us_job_openings",       "raw", "total", "Job Openings (NSA)",        "JOLTS"),
    BlsSeries("JTU000000000000000HIL", "us_hires",              "raw", "total", "Hires (NSA)",               "JOLTS"),
    BlsSeries("JTU000000000000000TSL", "us_total_separations",  "raw", "total", "Total Separations (NSA)",   "JOLTS"),
    BlsSeries("JTU000000000000000QUL", "us_quits",              "raw", "total", "Quits (NSA)",               "JOLTS"),
    BlsSeries("JTU000000000000000LDL", "us_layoffs_discharges", "raw", "total", "Layoffs & Discharges (NSA)","JOLTS"),
]

ALL_BLS_SERIES = CES_NONFARM_SERIES + CES_WAGE_SERIES + CPS_AGE_SERIES + JOLTS_SERIES


# ── API 호출 ─────────────────────────────────────────────────────────────────
def _call_bls_api(
    series_ids:  list[str],
    start_year:  int,
    end_year:    int,
    retries:     int = 3,
    backoff:     float = 2.0,
) -> dict:
    """
    BLS API v2 POST 호출.
    한 번에 최대 50개 시리즈, 최대 20년 범위.

    Returns
    -------
    dict : 원시 API 응답 전체
    """
    payload = {
        "seriesid":  series_ids,
        "startyear": str(start_year),
        "endyear":   str(end_year),
        "registrationkey": BLS_API_KEY,
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(BLS_API_URL, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "REQUEST_SUCCEEDED":
                msg = data.get("message", ["Unknown BLS error"])
                raise ValueError(f"BLS API 오류: {msg}")

            return data

        except (requests.RequestException, ValueError) as e:
            logger.warning(f"[BLS] API 호출 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                raise

    return {}


# ── 응답 정규화 ───────────────────────────────────────────────────────────────
def _normalize_bls_response(
    api_response: dict,
    series_map:   dict[str, BlsSeries],  # series_id → BlsSeries
    run_id:       str,
) -> list[dict]:
    """
    BLS API 응답 → BigQuery 공통 스키마 변환.
    """
    ingested_at = datetime.now(timezone.utc).isoformat()
    results = []

    for series_data in api_response.get("Results", {}).get("series", []):
        sid = series_data.get("seriesID", "")
        meta = series_map.get(sid)
        if not meta:
            continue

        for point in series_data.get("data", []):
            year  = point.get("year", "")
            period_raw = point.get("period", "")  # "M01"~"M12" 또는 "M13"(연간)
            if not period_raw.startswith("M") or period_raw == "M13":
                continue  # 연간 데이터 제외
            month = period_raw[1:]  # "01"~"12"
            period = f"{year}-{month}"

            value_raw = point.get("value", "")
            try:
                value = float(value_raw.replace(",", "")) if value_raw else None
            except ValueError:
                value = None

            results.append({
                "source":          "bls",
                "period":          period,
                "variable_name":   f"{meta.adjustment_type}_{meta.variable_name}",
                "category_key":    meta.category_key,
                "value":           value,
                "adjustment_type": meta.adjustment_type,
                "ingested_at":     ingested_at,
                "run_id":          run_id,
                "category_name":   meta.category_name,
            })

    return results


# ── 배치 수집 (50개 제한 청크 처리) ──────────────────────────────────────────
def _fetch_bls_in_batches(
    series_list: list[BlsSeries],
    start_year:  int,
    end_year:    int,
    run_id:      str,
    chunk_size:  int = BLS_MAX_SERIES_PER_REQUEST,
) -> list[dict]:
    """
    시리즈 목록을 chunk_size 단위로 나눠 BLS API를 호출하고
    정규화된 레코드를 반환.
    """
    series_map = {s.series_id: s for s in series_list}
    all_ids    = [s.series_id for s in series_list]
    results    = []

    for i in range(0, len(all_ids), chunk_size):
        chunk = all_ids[i:i + chunk_size]
        logger.info(f"[BLS] 배치 {i // chunk_size + 1} — {len(chunk)}개 시리즈 요청")
        try:
            raw = _call_bls_api(chunk, start_year, end_year)
            records = _normalize_bls_response(raw, series_map, run_id)
            logger.info(f"  → {len(records)}건")
            results.extend(records)
        except Exception as e:
            logger.error(f"[BLS] 배치 {i // chunk_size + 1} 실패: {e}")

        time.sleep(1.0)  # BLS API rate limit (v2: 500 queries/day)

    return results


# ── 연도 범위 분할 (20년 제한) ────────────────────────────────────────────────
def _split_year_ranges(start_year: int, end_year: int, max_span: int = 19) -> list[tuple[int, int]]:
    """
    BLS API v2 최대 20년 제한에 맞춰 연도 범위를 분할.
    """
    ranges = []
    y = start_year
    while y <= end_year:
        ranges.append((y, min(y + max_span, end_year)))
        y += max_span + 1
    return ranges


# ── 전체 수집 진입점 ─────────────────────────────────────────────────────────
def fetch_all_bls(run_id: str) -> list[dict]:
    """
    BLS 전체 수집 실행 (4개 조사 통합).

    Returns
    -------
    list[dict] : 정규화된 레코드 목록 (source='bls')
    """
    period_start, period_end = get_env_periods()
    start_year = int(period_start[:4])
    end_year   = int(period_end[:4])

    logger.info("=" * 60)
    logger.info(f"[BLS] 수집 시작 | {start_year}~{end_year} | 총 {len(ALL_BLS_SERIES)}개 시리즈")

    year_ranges = _split_year_ranges(start_year, end_year)
    all_records: list[dict] = []

    for y_start, y_end in year_ranges:
        logger.info(f"[BLS] 연도 범위: {y_start}~{y_end}")
        records = _fetch_bls_in_batches(
            series_list=ALL_BLS_SERIES,
            start_year=y_start,
            end_year=y_end,
            run_id=run_id,
        )
        all_records.extend(records)

    # 수집 기간 필터링 (API 응답이 범위를 초과할 수 있음)
    all_records = [
        r for r in all_records
        if period_start <= r["period"] <= period_end
    ]

    logger.info(f"[BLS] 수집 완료: {len(all_records)}건")
    return all_records

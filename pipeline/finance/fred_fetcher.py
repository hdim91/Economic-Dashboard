"""
fred_fetcher.py
─────────────────────────────────────────────────────────────────────────────
미국 연준 FRED API 수집 모듈 — 금융동향 파이프라인

━━━ 수집 대상 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Series ID        설명                               주기
  ──────────────────────────────────────────────────────────
  FEDFUNDS         미국 연방기금금리 (월평균)            M
  GS10             미국 국채 10년 금리                   M
  GS2              미국 국채 2년 금리                    M
  T10Y2Y           장단기 금리 스프레드 (10Y-2Y)         D→M
  CPIAUCSL         미국 CPI (도시 소비자, 계절조정)       M
  PCEPI            미국 PCE 물가지수                     M
  CSUSHPINSA       Case-Shiller 전국 주택가격지수 (비조정) M
  MORTGAGE30US     30년 고정 모기지 금리                  W→M
  DRCCLACBS        소비자 신용 연체율                    Q
  TOTALSL          소비자신용 잔액                       M

━━━ API 구조 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  https://api.stlouisfed.org/fred/series/observations
  ?series_id={ID}&api_key={KEY}&file_type=json
  &observation_start={YYYY-MM-DD}&observation_end={YYYY-MM-DD}
  &frequency=m&aggregation_method=avg

API 문서: https://fred.stlouisfed.org/docs/api/fred/
"""

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from finance_config import FRED_API_KEY, get_env_periods

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass
class FredSeries:
    variable_name:  str
    series_id:      str
    description:    str = ""
    available_from: str = "2000-01"
    unit:           str = ""
    # FRED에서 월별로 집계할 방법 (avg / eop / sum)
    aggregation:    str = "avg"


FRED_SERIES: list[FredSeries] = [
    FredSeries("us_fed_funds_rate",   "FEDFUNDS",    "미국 연방기금금리 (월평균)",        "1954-07", "%"),
    FredSeries("us_treasury_10y",     "GS10",        "미국 국채 10년 금리",               "1953-04", "%"),
    FredSeries("us_treasury_2y",      "GS2",         "미국 국채 2년 금리",                "1976-06", "%"),
    FredSeries("us_yield_spread",     "T10Y2Y",      "장단기 스프레드 10Y-2Y",            "1976-06", "%"),
    FredSeries("us_cpi",              "CPIAUCSL",    "미국 CPI (계절조정, 2015=100)",     "1947-01", "지수"),
    FredSeries("us_pce",              "PCEPI",       "미국 PCE 물가지수 (2017=100)",      "1959-01", "지수"),
    FredSeries("us_cs_hpi",           "CSUSHPINSA",  "Case-Shiller 전국 주택가격지수",    "1987-01", "지수"),
    FredSeries("us_mortgage_30y",     "MORTGAGE30US","30년 고정 모기지 금리",             "1971-04", "%"),
    FredSeries("us_consumer_credit",  "TOTALSL",     "미국 소비자신용 잔액",              "1943-01", "백만달러"),
    FredSeries("us_credit_delinquency","DRCCLACBS",  "소비자 신용 연체율",               "1991-01", "%"),
]


def _call_fred_api(
    series:       FredSeries,
    period_start: str,
    period_end:   str,
    retries:      int   = 3,
    backoff:      float = 2.0,
) -> list[dict]:
    params = {
        "series_id":           series.series_id,
        "api_key":             FRED_API_KEY,
        "file_type":           "json",
        "observation_start":   f"{period_start}-01",
        "observation_end":     f"{period_end}-28",  # 말일 근사
        "frequency":           "m",
        "aggregation_method":  series.aggregation,
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(FRED_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error_code" in data:
                raise ValueError(
                    f"FRED 오류 ({data['error_code']}): {data.get('error_message','')}"
                )

            return data.get("observations", [])

        except (requests.RequestException, ValueError) as exc:
            logger.warning(f"  [retry {attempt}/{retries}] {series.series_id}: {exc}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                logger.error(f"  [fail] {series.series_id} 최종 실패")
                raise

    return []


def _normalize(
    observations: list[dict], series: FredSeries, run_id: str
) -> list[dict]:
    ingested_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []

    for obs in observations:
        date_str = obs.get("date", "")          # YYYY-MM-DD
        if not date_str or len(date_str) < 7:
            continue
        period = date_str[:7]                   # YYYY-MM

        raw_val = obs.get("value", "")
        try:
            value = float(raw_val) if raw_val not in ("", ".", None) else None
        except ValueError:
            value = None

        out.append({
            "source":          "fred",
            "period":          period,
            "variable_name":   series.variable_name,
            "category_key":    series.series_id,
            "value":           value,
            "adjustment_type": "raw",
            "ingested_at":     ingested_at,
            "run_id":          run_id,
            "category_name":   series.description,
            "tbl_id":          series.series_id,
        })

    return out


def fetch_fred_series(
    series: FredSeries, period_start: str, period_end: str, run_id: str
) -> list[dict]:
    logger.info(f"[FRED] {series.series_id:20s} | {series.description}")
    try:
        raw     = _call_fred_api(series, period_start, period_end)
        records = _normalize(raw, series, run_id)
        logger.info(f"         → {len(records):,}건")
        return records
    except Exception as exc:
        logger.error(f"         → 실패: {exc}")
        return []
    finally:
        time.sleep(0.2)


def fetch_all_fred(run_id: str) -> list[dict]:
    period_start, period_end = get_env_periods()
    all_records: list[dict] = []

    logger.info("=" * 65)
    logger.info(
        f"[FRED] 수집 시작 ({len(FRED_SERIES)}개 시계열) "
        f"| {period_start} ~ {period_end}"
    )

    for series in FRED_SERIES:
        records = fetch_fred_series(series, period_start, period_end, run_id)
        all_records.extend(records)

    logger.info("=" * 65)
    logger.info(f"[FRED] 수집 완료 — 총 {len(all_records):,}건")
    return all_records


if __name__ == "__main__":
    import json, os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    os.environ.setdefault("PERIOD_START", "2020-01")
    os.environ.setdefault("PERIOD_END",   "2025-12")
    os.environ.setdefault("FRED_API_KEY", "YOUR_KEY")

    result = fetch_all_fred("test-fred-00000000")
    print(f"\n총 {len(result)}건")
    for r in result[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))

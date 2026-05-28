"""
ecos_fetcher.py
─────────────────────────────────────────────────────────────────────────────
한국은행 ECOS API 수집 모듈 — 금융동향 파이프라인

━━━ 수집 대상 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  No  통계표코드  통계표명                      주기  항목코드
  ──────────────────────────────────────────────────────────────
  1   722Y001    기준금리                        M    0101000
  2   817Y002    시장금리 — 국고채 3년            M    010200000
  3   817Y002    시장금리 — 국고채 10년           M    010230000
  4   817Y002    시장금리 — CD 91일              M    010300000
  5   251Y002    가계신용 잔액                    Q    1000000
  6   251Y003    주택담보대출 잔액 (예금은행)       M    10100
  7   101Y004    M2 통화량 (광의통화, 평잔)        M    BBGA00
  8   731Y003    원달러 환율 (월평균)              M    0000001
  9   901Y009    소비자물가지수 CPI               M    0
  10  404Y014    생산자물가지수 PPI               M    AA

━━━ API URL 구조 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr
  /1/100000/{stat_code}/{cycle}/{start}/{end}/{item_code1}

  주기: M(YYYYMM) / Q(YYYYQN) / A(YYYY)
  응답: StatisticSearch.row[] -> TIME, DATA_VALUE, ITEM_CODE1, ITEM_NAME1

API 문서: https://ecos.bok.or.kr/api/
"""

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from finance_config import ECOS_API_KEY, get_env_periods

logger = logging.getLogger(__name__)

ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터셋 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dataclass
class EcosDataset:
    variable_name:  str
    stat_code:      str
    cycle:          str           # M / Q / A / D
    item_code1:     str
    item_code2:     str = ""
    description:    str = ""
    available_from: str = "2000-01"
    unit:           str = ""
    needs_agg_m:    bool = False  # True이면 D 주기 일별 데이터를 월별 평균으로 집계


ECOS_DATASETS: list[EcosDataset] = [

    # 기준금리
    EcosDataset("kr_base_rate",        "722Y001", "M", "0101000",
                description="한국은행 기준금리", available_from="1999-05", unit="%"),

    # 국고채 3년
    EcosDataset("kr_govbond_3y",       "721Y001", "M", "5020000",
                description="국고채 3년 금리 (월,분기,연)", available_from="2000-01", unit="%"),

    # 국고채 10년 (item_code 수정: 010230000→010210000)
    EcosDataset("kr_govbond_10y",      "721Y001", "M", "5050000",
                description="국고채 10년 금리 (월,분기,연)", available_from="2000-01", unit="%"),

    # CD 91일 (item_code 수정: 010300000→010502000)
    EcosDataset("kr_cd_91d",           "721Y001", "M", "2010000",
                description="CD 91일 금리 (월,분기,연)", available_from="2000-01", unit="%"),

    # 가계신용 전체 (stat_code 수정: 251Y002→151Y001, 분기→월)
    EcosDataset("kr_household_credit", "151Y001", "Q", "1000000",
                description="가계신용 잔액 (분기말)", available_from="2003-09", unit="십억원"),

    # 가계대출 (신규)
    EcosDataset("kr_household_loan",   "151Y001", "Q", "1100000",
                description="가계대출 잔액 (분기말)", available_from="2003-09", unit="십억원"),

    # M2 평잔 (item_code 수정: BBGA00→BBHA00)
    EcosDataset("kr_m2",               "101Y004", "M", "BBHA00",
                description="M2 통화량 (광의통화, 평잔)", available_from="2002-01", unit="십억원"),

    # 원달러 환율 매매기준율 (일별→월평균 집계)
    # 731Y003은 D 주기만 존재 → 731Y001/D/0000001 로 변경, needs_agg_m=True
    EcosDataset("kr_usd_rate",         "731Y001", "D", "0000001",
                description="원달러 환율 (매매기준율, 월평균)", available_from="1964-01", unit="원/달러",
                needs_agg_m=True),

    # CPI 전체
    EcosDataset("kr_cpi",              "901Y009", "M", "0",
                description="소비자물가지수 CPI (전체, 2020=100)", available_from="1975-01", unit="지수"),

    # PPI 총지수 (item_code 수정: AA→*AA)
    EcosDataset("kr_ppi",              "404Y014", "M", "*AA",
                description="생산자물가지수 PPI (총지수, 2015=100)", available_from="1990-01", unit="지수"),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기간 변환 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _to_ecos_period(period: str, cycle: str, is_start: bool = True) -> str:
    """
    'YYYY-MM' → ECOS 기간 형식.

    cycle == "D" 일 때:
      is_start=True  → YYYYMM01  (월 첫날)
      is_start=False → YYYYMMDD  (월 말일)
    """
    if cycle == "M":
        return period.replace("-", "")
    elif cycle == "Q":
        year, month = period.split("-")
        q = (int(month) - 1) // 3 + 1
        return f"{year}Q{q}"
    elif cycle == "A":
        return period[:4]
    elif cycle == "D":
        from calendar import monthrange
        year, month = map(int, period.split("-"))
        if is_start:
            return f"{year}{month:02d}01"
        else:
            last = monthrange(year, month)[1]
            return f"{year}{month:02d}{last:02d}"
    return period.replace("-", "")


def _from_ecos_period(time_str: str, cycle: str) -> str:
    """ECOS TIME 필드 -> 'YYYY-MM' 형식."""
    if cycle == "M" and len(time_str) == 6:
        return f"{time_str[:4]}-{time_str[4:]}"
    elif cycle == "Q" and "Q" in time_str:
        year, q = time_str.split("Q")
        month = (int(q) - 1) * 3 + 1
        return f"{year}-{month:02d}"
    elif cycle == "A" and len(time_str) == 4:
        return f"{time_str}-01"
    elif cycle == "D" and len(time_str) == 8:
        # "20240115" → "2024-01"
        return f"{time_str[:4]}-{time_str[4:6]}"
    return time_str


def _aggregate_daily_to_monthly(records: list[dict]) -> list[dict]:
    """
    일별 레코드를 월별 평균으로 집계.

    D 주기로 수집한 환율 등 일별 데이터를 월별 평균값 1건으로 압축.
    BQ MERGE 기준: (period, variable_name, category_key) 중복 방지.
    """
    from collections import defaultdict

    buckets: dict[tuple, list[float]] = defaultdict(list)
    template: dict[tuple, dict] = {}

    for rec in records:
        key = (rec["period"], rec["variable_name"], rec["category_key"])
        if rec["value"] is not None:
            buckets[key].append(rec["value"])
        if key not in template:
            template[key] = rec.copy()

    aggregated: list[dict] = []
    for key, values in buckets.items():
        rec = template[key].copy()
        rec["value"] = round(sum(values) / len(values), 4) if values else None
        aggregated.append(rec)

    return aggregated


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 호출
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _call_ecos_api(
    ds:           EcosDataset,
    period_start: str,
    period_end:   str,
    retries:      int   = 3,
    backoff:      float = 2.0,
) -> list[dict]:
    start_str = _to_ecos_period(period_start, ds.cycle, is_start=True)
    end_str   = _to_ecos_period(period_end,   ds.cycle, is_start=False)

    item_part = ds.item_code1
    if ds.item_code2:
        item_part += f"/{ds.item_code2}"

    url = (
        f"{ECOS_BASE_URL}/{ECOS_API_KEY}/json/kr"
        f"/1/100000"
        f"/{ds.stat_code}/{ds.cycle}/{start_str}/{end_str}"
        f"/{item_part}"
    )

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "RESULT" in data:
                code = data["RESULT"].get("CODE", "")
                msg  = data["RESULT"].get("MESSAGE", "")
                if code != "INFO-000":
                    raise ValueError(f"ECOS 오류 ({code}): {msg}")
                return []

            rows = data.get("StatisticSearch", {}).get("row", [])
            return rows if isinstance(rows, list) else []

        except (requests.RequestException, ValueError) as exc:
            logger.warning(f"  [retry {attempt}/{retries}] {ds.stat_code}: {exc}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                logger.error(f"  [fail] {ds.stat_code} 최종 실패")
                raise

    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 응답 정규화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _normalize(raw_rows: list[dict], ds: EcosDataset, run_id: str) -> list[dict]:
    ingested_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []

    for row in raw_rows:
        time_str = row.get("TIME", "")
        if not time_str:
            continue

        period = _from_ecos_period(time_str, ds.cycle)

        code1 = row.get("ITEM_CODE1", "")
        code2 = row.get("ITEM_CODE2", "")
        category_key  = "|".join(p for p in [code1, code2] if p) or "total"
        category_name = " | ".join(
            p for p in [row.get("ITEM_NAME1", ""), row.get("ITEM_NAME2", "")] if p
        )

        raw_val = row.get("DATA_VALUE", "")
        try:
            value = (
                float(str(raw_val).replace(",", ""))
                if raw_val not in ("", "-", None)
                else None
            )
        except ValueError:
            value = None

        out.append({
            "source":          "ecos",
            "period":          period,
            "variable_name":   ds.variable_name,
            "category_key":    category_key,
            "value":           value,
            "adjustment_type": "raw",
            "ingested_at":     ingested_at,
            "run_id":          run_id,
            "category_name":   category_name,
            "tbl_id":          ds.stat_code,
        })

    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단일 데이터셋 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_ecos_dataset(
    ds: EcosDataset, period_start: str, period_end: str, run_id: str
) -> list[dict]:
    logger.info(f"[ECOS] {ds.stat_code:12s} | {ds.cycle} | {ds.description}")
    try:
        raw     = _call_ecos_api(ds, period_start, period_end)
        records = _normalize(raw, ds, run_id)

        # D 주기 일별 데이터 → 월별 평균 집계
        if ds.needs_agg_m and records:
            before = len(records)
            records = _aggregate_daily_to_monthly(records)
            logger.info(f"         -> 일별→월별 집계: {before}건 → {len(records)}건")

        logger.info(f"         -> {len(records):,}건")
        return records
    except Exception as exc:
        logger.error(f"         -> 실패: {exc}")
        return []
    finally:
        time.sleep(0.3)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전체 수집 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_all_ecos(run_id: str) -> list[dict]:
    period_start, period_end = get_env_periods()
    all_records: list[dict] = []

    logger.info("=" * 65)
    logger.info(
        f"[ECOS] 수집 시작 ({len(ECOS_DATASETS)}개 시계열) "
        f"| {period_start} ~ {period_end}"
    )

    for ds in ECOS_DATASETS:
        records = fetch_ecos_dataset(ds, period_start, period_end, run_id)
        all_records.extend(records)

    logger.info("=" * 65)
    logger.info(f"[ECOS] 수집 완료 — 총 {len(all_records):,}건")
    return all_records


if __name__ == "__main__":
    import json, os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    os.environ.setdefault("PERIOD_START", "2020-01")
    os.environ.setdefault("PERIOD_END",   "2025-12")
    os.environ.setdefault("ECOS_API_KEY", "YOUR_KEY")

    result = fetch_all_ecos("test-ecos-00000000")
    print(f"\n총 {len(result)}건")
    for r in result[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
"""
finance_kosis_fetcher.py
─────────────────────────────────────────────────────────────────────────────
KOSIS API 수집 모듈 — 금융동향 파이프라인 (고용동향 kosis_fetcher.py 구조 재사용)

━━━ 수집 대상 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  No  통계표ID           통계표명                      orgId  비고
  ──────────────────────────────────────────────────────────────────────
  1   DT_1J22003        소비자물가지수 (CPI)             101   전체·대분류
  2   DT_1J24002        생산자물가지수 (PPI)             101   전체
  3   DT_1KA9001        주택매매가격지수                 408   전국·수도권
  4   DT_1KA9002        주택전세가격지수                 408   전국·수도권
  5   DT_1L9Y001        가계금융복지조사 (소득분위별)      101   연간

━━━ API 파라미터 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  고용동향 kosis_fetcher.py와 동일한 구조
  URL: https://kosis.kr/openapi/Param/statisticsParameterData.do
"""

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from finance_config import KOSIS_API_KEY, get_env_periods

logger = logging.getLogger(__name__)

KOSIS_BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"


@dataclass
class FinanceKosisDataset:
    variable_name:  str
    org_id:         str
    tbl_id:         str
    adjustment_type: str
    description:    str
    available_from: str
    itm_id:         str
    obj_l1:         str = "ALL"
    obj_l2:         str = ""
    unit:           str = ""


FINANCE_KOSIS_DATASETS: list[FinanceKosisDataset] = [

    # ① 소비자물가지수 CPI — 전체 및 대분류
    FinanceKosisDataset(
        variable_name   = "kr_cpi_kosis",
        org_id          = "101",
        tbl_id          = "DT_1J22003",
        adjustment_type = "raw",
        description     = "소비자물가지수 (2020=100, 전체·대분류)",
        available_from  = "1965-01",
        itm_id          = "13103112810M_102+",  # 총지수
        obj_l1          = "0+",                  # 전체
        unit            = "지수",
    ),

    # ② 생산자물가지수 PPI — 전체
    FinanceKosisDataset(
        variable_name   = "kr_ppi_kosis",
        org_id          = "101",
        tbl_id          = "DT_1J24002",
        adjustment_type = "raw",
        description     = "생산자물가지수 (2015=100, 전체)",
        available_from  = "1965-01",
        itm_id          = "13103212810M_101+",  # 총지수
        obj_l1          = "000+",
        unit            = "지수",
    ),

    # ③ 주택매매가격지수 — 전국·수도권·서울
    FinanceKosisDataset(
        variable_name   = "kr_house_price_sale",
        org_id          = "408",
        tbl_id          = "DT_KAB_11672_010",
        adjustment_type = "raw",
        description     = "주택매매가격지수 (전국·수도권·서울)",
        available_from  = "2003-11",
        itm_id          = "T10+",               # 매매가격지수
        obj_l1          = "ALL",
        unit            = "지수",
    ),

    # ④ 주택전세가격지수 — 전국·수도권·서울
    FinanceKosisDataset(
        variable_name   = "kr_house_price_rent",
        org_id          = "408",
        tbl_id          = "DT_KAB_11672_020",
        adjustment_type = "raw",
        description     = "주택전세가격지수 (전국·수도권·서울)",
        available_from  = "2003-11",
        itm_id          = "T20+",               # 전세가격지수
        obj_l1          = "ALL",
        unit            = "지수",
    ),
]


def _call_kosis_api(
    ds:           FinanceKosisDataset,
    period_start: str,
    period_end:   str,
    retries:      int   = 3,
    backoff:      float = 2.0,
) -> list[dict]:
    params = {
        "method":     "getList",
        "apiKey":     KOSIS_API_KEY,
        "orgId":      ds.org_id,
        "tblId":      ds.tbl_id,
        "itmId":      ds.itm_id,
        "objL1":      ds.obj_l1,
        "objL2":      ds.obj_l2,
        "objL3":      "", "objL4": "", "objL5": "",
        "objL6":      "", "objL7": "", "objL8": "",
        "format":     "json",
        "jsonVD":     "Y",
        "prdSe":      "M",
        "startPrdDe": period_start.replace("-", ""),
        "endPrdDe":   period_end.replace("-", ""),
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(KOSIS_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "err" in data:
                raise ValueError(
                    f"KOSIS 오류 (err={data.get('err')}): {data.get('errMsg', data)}"
                )
            return data if isinstance(data, list) else []

        except (requests.RequestException, ValueError) as exc:
            logger.warning(f"  [retry {attempt}/{retries}] {ds.tbl_id}: {exc}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                logger.error(f"  [fail] {ds.tbl_id} 최종 실패")
                raise

    return []


def _normalize(
    raw_records: list[dict], ds: FinanceKosisDataset, run_id: str
) -> list[dict]:
    ingested_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []

    for rec in raw_records:
        prd = rec.get("PRD_DE", "")
        if len(prd) != 6 or not prd.isdigit():
            continue
        period = f"{prd[:4]}-{prd[4:]}"

        cat_parts = [
            rec.get("C1", ""), rec.get("C2", ""),
            rec.get("C3", ""), rec.get("ITM_ID", ""),
        ]
        category_key  = "|".join(p for p in cat_parts if p) or "total"
        category_name = " | ".join(
            p for p in [
                rec.get("C1_NM",""), rec.get("C2_NM",""),
                rec.get("C3_NM",""), rec.get("ITM_NM",""),
            ] if p
        )

        raw_val = rec.get("DT", "")
        try:
            value = (
                float(str(raw_val).replace(",", ""))
                if raw_val not in ("", "-", None)
                else None
            )
        except ValueError:
            value = None

        out.append({
            "source":          "kosis_finance",
            "period":          period,
            "variable_name":   f"{ds.adjustment_type}_{ds.variable_name}",
            "category_key":    category_key,
            "value":           value,
            "adjustment_type": ds.adjustment_type,
            "ingested_at":     ingested_at,
            "run_id":          run_id,
            "category_name":   category_name,
            "tbl_id":          ds.tbl_id,
        })

    return out


def fetch_finance_kosis_dataset(
    ds: FinanceKosisDataset, period_start: str, period_end: str, run_id: str
) -> list[dict]:
    logger.info(f"[KOSIS-FIN] {ds.tbl_id:20s} | {ds.description}")
    try:
        raw     = _call_kosis_api(ds, period_start, period_end)
        records = _normalize(raw, ds, run_id)
        logger.info(f"            → {len(records):,}건")
        return records
    except Exception as exc:
        logger.error(f"            → 실패: {exc}")
        return []
    finally:
        time.sleep(0.5)


def fetch_all_finance_kosis(run_id: str) -> list[dict]:
    period_start, period_end = get_env_periods()
    all_records: list[dict] = []

    logger.info("=" * 65)
    logger.info(
        f"[KOSIS-FIN] 수집 시작 ({len(FINANCE_KOSIS_DATASETS)}개 테이블) "
        f"| {period_start} ~ {period_end}"
    )

    for ds in FINANCE_KOSIS_DATASETS:
        records = fetch_finance_kosis_dataset(ds, period_start, period_end, run_id)
        all_records.extend(records)

    logger.info("=" * 65)
    logger.info(f"[KOSIS-FIN] 수집 완료 — 총 {len(all_records):,}건")
    return all_records


if __name__ == "__main__":
    import json, os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    os.environ.setdefault("PERIOD_START", "2020-01")
    os.environ.setdefault("PERIOD_END",   "2025-12")
    os.environ.setdefault("KOSIS_API_KEY", "YOUR_KEY")

    result = fetch_all_finance_kosis("test-kosis-fin-00000000")
    print(f"\n총 {len(result)}건")
    for r in result[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))

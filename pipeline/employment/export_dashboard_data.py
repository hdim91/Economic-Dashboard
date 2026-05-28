"""
export_dashboard_data.py
─────────────────────────────────────────────────────────────────────────────
메인 페이지용 대시보드 데이터를 BigQuery에서 읽어 GCS에 JSON으로 export.

pipeline-job 완료 후 실행 (main.py에서 호출 또는 단독 실행 가능).

출력: gs://{GCS_BUCKET}/dashboard/data.json
"""

import json
import os
import logging
from datetime import datetime, timezone

import requests
from google.cloud import bigquery

logger = logging.getLogger(__name__)

GCP_PROJECT      = os.getenv("GCP_PROJECT_ID",      "auto-report-489722")
BQ_DATASET       = os.getenv("BQ_DATASET",          "kosis_stats")
FINANCE_DATASET  = os.getenv("FINANCE_BQ_DATASET",  "finance_stats")
GCS_BUCKET       = os.getenv("GCS_BUCKET",          "gs://auto-report-489722-reports")


def _bq_client():
    return bigquery.Client(project=GCP_PROJECT)


def _query(client, sql):
    return list(client.query(sql).result())


def _gcs_token():
    """Cloud Run ADC 메타데이터 서버에서 액세스 토큰 취득."""
    resp = requests.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
        timeout=5,
    )
    return resp.json()["access_token"]


def _upload_gcs(data: dict, gcs_uri: str):
    """JSON 데이터를 GCS에 업로드 (resumable upload)."""
    bucket = gcs_uri.replace("gs://", "").split("/")[0]
    obj    = "/".join(gcs_uri.replace("gs://", "").split("/")[1:])
    token  = _gcs_token()

    import urllib.parse
    obj_enc = urllib.parse.quote(obj, safe="")
    init_url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=resumable&name={obj_enc}"
    )
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")

    init_resp = requests.post(
        init_url,
        headers={
            "Authorization":             f"Bearer {token}",
            "Content-Type":              "application/json",
            "X-Upload-Content-Type":     "application/json",
            "X-Upload-Content-Length":   str(len(body)),
        },
        data="{}",
        timeout=15,
    )
    init_resp.raise_for_status()
    session_uri = init_resp.headers["location"]

    upload_resp = requests.put(
        session_uri,
        headers={"Content-Type": "application/json"},
        data=body,
        timeout=60,
    )
    upload_resp.raise_for_status()
    logger.info(f"[Dashboard] GCS 업로드 완료: {gcs_uri}")


def _fin_latest(client, variable_name: str) -> dict | None:
    """finance_raw_dedup에서 단일 지표 최신값 조회 (데이터 없으면 None)."""
    try:
        rows = _query(client, f"""
            SELECT period, value
            FROM `{GCP_PROJECT}.{FINANCE_DATASET}.finance_raw_dedup`
            WHERE variable_name = '{variable_name}'
              AND value IS NOT NULL
            ORDER BY period DESC
            LIMIT 1
        """)
        if not rows:
            return None
        return {"period": str(rows[0]["period"]), "value": float(rows[0]["value"])}
    except Exception as e:
        logger.warning(f"[Dashboard] finance indicator '{variable_name}' 조회 실패: {e}")
        return None


def _fin_spread_series(client) -> dict:
    """fin_analysis_kr_us_spread에서 최근 36개월 스프레드 시계열 조회."""
    try:
        rows = _query(client, f"""
            SELECT spread_name, period, spread, kr_val, us_val, kr_usd_rate
            FROM `{GCP_PROJECT}.{FINANCE_DATASET}.fin_analysis_kr_us_spread`
            WHERE period >= FORMAT_DATE('%Y-%m',
                    DATE_SUB(CURRENT_DATE(), INTERVAL 36 MONTH))
            ORDER BY spread_name, period
        """)
        result: dict = {}
        for r in rows:
            name = r["spread_name"]
            if name not in result:
                result[name] = []
            result[name].append({
                "period": str(r["period"]),
                "spread": round(float(r["spread"]), 3) if r["spread"] is not None else None,
            })
        return result
    except Exception as e:
        logger.warning(f"[Dashboard] finance spread series 조회 실패: {e}")
        return {}


def _fin_spread_summary(client) -> list:
    """fin_analysis_kr_us_summary에서 최신 스프레드 스냅샷 조회."""
    try:
        rows = _query(client, f"""
            SELECT spread_name, label, latest_period,
                   latest_spread, spread_direction, spread_12m_avg
            FROM `{GCP_PROJECT}.{FINANCE_DATASET}.fin_analysis_kr_us_summary`
            ORDER BY spread_name
        """)
        return [
            {
                "name":       r["spread_name"],
                "label":      r["label"],
                "period":     str(r["latest_period"]),
                "spread":     round(float(r["latest_spread"]), 3)
                              if r["latest_spread"] is not None else None,
                "direction":  r["spread_direction"],
                "avg_12m":    round(float(r["spread_12m_avg"]), 3)
                              if r["spread_12m_avg"] is not None else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[Dashboard] finance spread summary 조회 실패: {e}")
        return []


def _fin_report(client) -> dict:
    """finance_report_history에서 최신 금융동향 리포트 정보 조회."""
    try:
        rows = _query(client, f"""
            SELECT period_end, rendered_at, filename, gcs_url
            FROM `{GCP_PROJECT}.{FINANCE_DATASET}.finance_report_history`
            ORDER BY rendered_at DESC
            LIMIT 1
        """)
        if not rows:
            return {}
        r = rows[0]
        return {
            "period_end":  str(r["period_end"]),
            "rendered_at": str(r["rendered_at"]),
            "filename":    r["filename"],
            "gcs_url":     r["gcs_url"],
        }
    except Exception as e:
        logger.warning(f"[Dashboard] finance report history 조회 실패: {e}")
        return {}


def build_dashboard_data() -> dict:
    client = _bq_client()
    ds     = f"`{GCP_PROJECT}.{BQ_DATASET}`"

    # ── 1. 파이프라인 마지막 실행 시각 ─────────────────────────────────
    pipeline_run = _query(client, f"""
        SELECT MAX(ingested_at) AS last_run
        FROM {ds}.employment_raw_dedup
    """)
    last_pipeline_run = str(pipeline_run[0]["last_run"]) if pipeline_run else None

    # ── 2. 최신 리포트 발행일 ──────────────────────────────────────────
    report_rows = _query(client, f"""
        SELECT period_end, rendered_at, filename, gcs_url
        FROM {ds}.report_history
        ORDER BY rendered_at DESC
        LIMIT 1
    """)
    latest_report = {}
    if report_rows:
        r = report_rows[0]
        latest_report = {
            "period_end":   str(r["period_end"]),
            "rendered_at":  str(r["rendered_at"]),
            "filename":     r["filename"],
            "gcs_url":      r["gcs_url"],
        }

    # ── 3. 핵심 고용 지표 ──────────────────────────────────────────────
    emp_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'kr_employed_total_sa_value'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 13
    """)
    emp_series = [{"period": str(r["period"]), "value": float(r["value"])}
                  for r in reversed(emp_rows)]

    # 전년동월차
    emp_yoy_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'kr_employed_total_sa_yoy'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 1
    """)
    emp_yoy = float(emp_yoy_rows[0]["value"]) if emp_yoy_rows else None

    # 고용률
    emprate_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'kr_emprate_total_value'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 1
    """)
    emprate = float(emprate_rows[0]["value"]) if emprate_rows else None

    # 구직급여 신규 신청
    js_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'kr_jobseeker_new_value'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 1
    """)
    js_new = {"period": str(js_rows[0]["period"]), "value": float(js_rows[0]["value"])} \
        if js_rows else None

    # 월평균 임금
    wage_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'kr_wage_total_value'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 1
    """)
    wage = {"period": str(wage_rows[0]["period"]), "value": float(wage_rows[0]["value"])} \
        if wage_rows else None

    # 미국 비농업 취업자
    us_rows = _query(client, f"""
        SELECT period, value
        FROM {ds}.fs_long
        WHERE feature_name = 'us_nonfarm_total_sa_value'
          AND value IS NOT NULL
        ORDER BY period DESC
        LIMIT 2
    """)
    us_nonfarm = None
    if len(us_rows) >= 1:
        us_nonfarm = {
            "period":  str(us_rows[0]["period"]),
            "value":   float(us_rows[0]["value"]),
            "mom_chg": round(float(us_rows[0]["value"]) - float(us_rows[1]["value"]), 1)
                       if len(us_rows) >= 2 else None,
        }

    # ── 4. 금융 지표 ──────────────────────────────────────────────────
    fin_kr_base_rate  = _fin_latest(client, "kr_base_rate")
    fin_kr_cpi        = _fin_latest(client, "kr_cpi")
    fin_kr_house      = _fin_latest(client, "none_kr_house_price_sale")
    fin_kr_usd        = _fin_latest(client, "kr_usd_rate")
    fin_us_fed        = _fin_latest(client, "us_fed_funds_rate")
    fin_spread        = _fin_spread_series(client)
    fin_spread_sum    = _fin_spread_summary(client)
    fin_report        = _fin_report(client)

    # ── 5. 조립 ───────────────────────────────────────────────────────
    return {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "last_pipeline_run":    last_pipeline_run,
        "latest_report":        latest_report,
        "latest_finance_report": fin_report,
        "finance_spread":        fin_spread,
        "finance_spread_summary": fin_spread_sum,
        "indicators": {
            # ── 고용 ────────────────────────────────────────────────
            "kr_employed": {
                "label":   "한국 취업자 수 (계절조정)",
                "unit":    "만명",
                "latest":  emp_series[-1] if emp_series else None,
                "yoy_chg": emp_yoy,
                "series":  emp_series,
            },
            "kr_emprate": {
                "label":  "고용률",
                "unit":   "%",
                "latest": {"value": emprate} if emprate else None,
            },
            "kr_jobseeker": {
                "label":  "구직급여 신규 신청",
                "unit":   "명",
                "latest": js_new,
            },
            "kr_wage": {
                "label":  "월평균 임금",
                "unit":   "원",
                "latest": wage,
            },
            "us_nonfarm": {
                "label":  "미국 비농업 취업자",
                "unit":   "천명",
                "latest": us_nonfarm,
            },
            # ── 금융 ────────────────────────────────────────────────
            "kr_base_rate": {
                "label":  "한국 기준금리",
                "unit":   "%",
                "latest": fin_kr_base_rate,
            },
            "kr_cpi": {
                "label":  "소비자물가지수 (CPI)",
                "unit":   "지수",
                "latest": fin_kr_cpi,
            },
            "kr_house_price": {
                "label":  "주택매매가격지수",
                "unit":   "지수",
                "latest": fin_kr_house,
            },
            "kr_usd_krw": {
                "label":  "원/달러 환율",
                "unit":   "원",
                "latest": fin_kr_usd,
            },
            "us_fed_rate": {
                "label":  "미국 연방기금금리",
                "unit":   "%",
                "latest": fin_us_fed,
            },
        },
    }


def export_dashboard_data():
    logger.info("[Dashboard] 대시보드 데이터 생성 시작")
    try:
        data    = build_dashboard_data()
        gcs_uri = f"{GCS_BUCKET.rstrip('/')}/dashboard/data.json"
        _upload_gcs(data, gcs_uri)
        logger.info("[Dashboard] 완료")
    except Exception as e:
        logger.error(f"[Dashboard] 실패: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    export_dashboard_data()

"""
bq_setup_runner.py
BigQuery REST API를 gcloud 사용자 토큰으로 직접 호출하여
finance_stats 데이터셋 테이블 및 View를 생성합니다.
"""

import subprocess, json, requests, sys

PROJECT = "auto-report-489722"
DATASET = "finance_stats"


def get_token():
    # gcloud가 PATH에 있어야 함 (gcloud CLI 설치 후 쉘 재시작)
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def run_query(token: str, sql: str, description: str) -> bool:
    """BigQuery jobs.query REST API로 SQL을 실행합니다."""
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/jobs"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "configuration": {
            "query": {
                "query": sql,
                "useLegacySql": False,
            }
        }
    }
    resp = requests.post(url, headers=headers, json=body, timeout=60)
    data = resp.json()

    if resp.status_code not in (200, 201):
        print(f"[FAIL] {description}: {data.get('error', data)}")
        return False

    # 완료 대기
    job_id = data["jobReference"]["jobId"]
    location = data["jobReference"].get("location", "asia-northeast3")
    poll_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/jobs/{job_id}?location={location}"
    import time
    for _ in range(30):
        r2 = requests.get(poll_url, headers=headers, timeout=30)
        status = r2.json().get("status", {})
        if status.get("state") == "DONE":
            if "errorResult" in status:
                print(f"[FAIL] {description}: {status['errorResult']['message']}")
                return False
            print(f"[OK]   {description}")
            return True
        time.sleep(2)
    print(f"[TIMEOUT] {description}")
    return False


RAW_DDL = """
CREATE TABLE IF NOT EXISTS `auto-report-489722.finance_stats.finance_raw` (
  source          STRING    NOT NULL,
  period          STRING    NOT NULL,
  period_date     DATE      NOT NULL,
  variable_name   STRING    NOT NULL,
  category_key    STRING    NOT NULL,
  value           FLOAT64,
  adjustment_type STRING,
  ingested_at     TIMESTAMP,
  run_id          STRING,
  category_name   STRING,
  tbl_id          STRING
)
PARTITION BY DATE_TRUNC(period_date, MONTH)
CLUSTER BY source, variable_name, category_key
OPTIONS (require_partition_filter = false)
"""

STG_DDL = """
CREATE TABLE IF NOT EXISTS `auto-report-489722.finance_stats.finance_stg` (
  source          STRING    NOT NULL,
  period          STRING    NOT NULL,
  variable_name   STRING    NOT NULL,
  category_key    STRING    NOT NULL,
  value           FLOAT64,
  adjustment_type STRING,
  ingested_at     TIMESTAMP,
  run_id          STRING,
  category_name   STRING,
  tbl_id          STRING
)
"""

VIEW_DDL = """
CREATE OR REPLACE VIEW `auto-report-489722.finance_stats.finance_raw_dedup` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY source, period, variable_name, category_key
      ORDER BY ingested_at DESC
    ) AS rn
  FROM `auto-report-489722.finance_stats.finance_raw`
)
WHERE rn = 1
"""

LIST_DDL = "SELECT table_name, table_type FROM `auto-report-489722.finance_stats.INFORMATION_SCHEMA.TABLES` ORDER BY table_name"


if __name__ == "__main__":
    print("gcloud 토큰 취득 중...")
    token = get_token()
    print(f"토큰: {token[:20]}...")

    ok = True
    ok &= run_query(token, RAW_DDL,  "finance_raw 테이블 생성")
    ok &= run_query(token, STG_DDL,  "finance_stg 테이블 생성")
    ok &= run_query(token, VIEW_DDL, "finance_raw_dedup View 생성")

    # 결과 확인
    print("\n=== 생성된 객체 목록 ===")
    run_query(token, LIST_DDL, "INFORMATION_SCHEMA 조회")

    # INFORMATION_SCHEMA 결과 직접 출력
    list_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(list_url, headers=headers, json={
        "query": LIST_DDL, "useLegacySql": False, "timeoutMs": 15000
    }, timeout=30)
    rdata = r.json()
    rows = rdata.get("rows", [])
    if rows:
        print(f"{'table_name':<30} {'table_type'}")
        print("-" * 50)
        for row in rows:
            vals = [f["v"] for f in row["f"]]
            print(f"{vals[0]:<30} {vals[1]}")
    else:
        print(rdata.get("error", rdata))

    sys.exit(0 if ok else 1)

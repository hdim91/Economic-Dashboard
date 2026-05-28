# bq가 PATH에 있어야 함. 또는 절대 경로로 교체:
# $bq = 'C:\path\to\google-cloud-sdk\bin\bq.cmd'
$bq   = 'bq'
$proj = 'YOUR_GCP_PROJECT_ID'

Write-Host "=== finance_raw 테이블 생성 ==="
$raw_ddl = @"
CREATE TABLE IF NOT EXISTS ``auto-report-489722.finance_stats.finance_raw`` (
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
"@
& $bq query --project_id=$proj --use_legacy_sql=false "$raw_ddl"

Write-Host "=== finance_stg 테이블 생성 ==="
$stg_ddl = @"
CREATE TABLE IF NOT EXISTS ``auto-report-489722.finance_stats.finance_stg`` (
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
"@
& $bq query --project_id=$proj --use_legacy_sql=false "$stg_ddl"

Write-Host "=== finance_raw_dedup View 생성 ==="
$view_ddl = @"
CREATE OR REPLACE VIEW ``auto-report-489722.finance_stats.finance_raw_dedup`` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY source, period, variable_name, category_key
      ORDER BY ingested_at DESC
    ) AS rn
  FROM ``auto-report-489722.finance_stats.finance_raw``
)
WHERE rn = 1
"@
& $bq query --project_id=$proj --use_legacy_sql=false "$view_ddl"

Write-Host "=== 완료 — 테이블 목록 확인 ==="
& $bq ls --project_id=$proj finance_stats

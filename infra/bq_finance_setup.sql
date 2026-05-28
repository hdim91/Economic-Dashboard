-- finance_raw: 파티션 + 클러스터링
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
OPTIONS (require_partition_filter = false);

-- finance_stg: Staging (파티션 없음)
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
);

-- finance_raw_dedup: Dedup View
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
WHERE rn = 1;

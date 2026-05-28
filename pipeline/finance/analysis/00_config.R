# ============================================================================
# 00_config.R
# 금융동향 분석 파이프라인 — 공통 설정
# ============================================================================

suppressPackageStartupMessages({
  library(bigrquery)
  library(dplyr)
  library(lubridate)
  library(glue)
})

# ── BQ 연결 ─────────────────────────────────────────────────────────────────
BQ_PROJECT   <- Sys.getenv("GCP_PROJECT_ID",       "auto-report-489722")
BQ_DATASET   <- Sys.getenv("FINANCE_BQ_DATASET",   "finance_stats")
BQ_LOCATION  <- Sys.getenv("BQ_LOCATION",          "asia-northeast3")
DEDUP_VIEW   <- "finance_raw_dedup"

# ── 분석 결과 저장 데이터셋 (같은 데이터셋에 분석 테이블 적재) ───────────────
ANALYSIS_DATASET <- Sys.getenv("ANALYSIS_DATASET", BQ_DATASET)

# ── 분석 기간 ────────────────────────────────────────────────────────────────
PERIOD_START <- Sys.getenv(
  "PERIOD_START",
  format(floor_date(Sys.Date() %m-% years(5), "month"), "%Y-%m")
)
PERIOD_END <- Sys.getenv(
  "PERIOD_END",
  format(floor_date(Sys.Date() %m-% months(1), "month"), "%Y-%m")
)

# ── run_id: Python 파이프라인과 동일한 값 사용 ───────────────────────────────
RUN_ID <- Sys.getenv("PIPELINE_RUN_ID", format(Sys.time(), "%Y%m%dT%H%M%SZ"))

# ── BQ 클라이언트 인증 (ADC) ─────────────────────────────────────────────────
bq_auth(path = NULL)  # Cloud Run: ADC 자동 사용

# ── 헬퍼: BQ 쿼리 실행 → tibble ─────────────────────────────────────────────
bq_query <- function(sql) {
  tryCatch({
    job <- bq_project_query(BQ_PROJECT, sql)
    bq_table_download(job)
  }, error = function(e) {
    message("[BQ] 쿼리 실패: ", conditionMessage(e))
    stop(e)
  })
}

# ── 헬퍼: BQ 테이블에 run_id 기준 REPLACE 저장 ──────────────────────────────
bq_replace_run <- function(df, table_name, run_id) {
  tbl <- bq_table(BQ_PROJECT, ANALYSIS_DATASET, table_name)
  if (bq_table_exists(tbl)) {
    bq_perform_query(
      glue("DELETE FROM `{BQ_PROJECT}.{ANALYSIS_DATASET}.{table_name}` WHERE run_id = '{run_id}'"),
      billing = BQ_PROJECT
    )
  }
  bq_table_upload(
    tbl, df,
    create_disposition = "CREATE_IF_NEEDED",
    write_disposition  = "WRITE_APPEND"
  )
  message(glue("[BQ] {table_name}  run_id={run_id} → {nrow(df)}행 저장"))
}

message(glue(
  "[config] project={BQ_PROJECT}  dataset={BQ_DATASET}  ",
  "period={PERIOD_START}~{PERIOD_END}  run_id={RUN_ID}"
))

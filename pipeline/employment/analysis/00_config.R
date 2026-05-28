# ============================================================================
# 00_config.R
# 고용동향 분석 파이프라인 — 공통 설정
# ============================================================================
# 환경변수로 주입; 없으면 기본값 사용
# Cloud Run Job: --env-file .env 또는 Secret Manager 참조

suppressPackageStartupMessages({
  library(bigrquery)
  library(dplyr)
  library(lubridate)
  library(glue)
})

# ── BQ 연결 ─────────────────────────────────────────────────────────────────
BQ_PROJECT  <- Sys.getenv("GCP_PROJECT_ID",  "")
BQ_DATASET  <- Sys.getenv("BQ_DATASET",      "kosis_stats")
BQ_LOCATION <- Sys.getenv("BQ_LOCATION",     "US")

# ── 분석 결과 저장 데이터셋 (Raw와 동일 데이터셋에 저장) ─────────────────────
ANALYSIS_DATASET <- Sys.getenv("ANALYSIS_DATASET", BQ_DATASET)

# ── 분석 기간 ────────────────────────────────────────────────────────────────
# PERIOD_START/END 없으면 최근 5년
PERIOD_START <- Sys.getenv("PERIOD_START", format(floor_date(Sys.Date() %m-% years(5), "month"), "%Y-%m"))
PERIOD_END   <- Sys.getenv("PERIOD_END",   format(floor_date(Sys.Date() %m-% months(1), "month"), "%Y-%m"))

# ── STL 설정 ─────────────────────────────────────────────────────────────────
STL_S_WINDOW   <- as.integer(Sys.getenv("STL_S_WINDOW",   "13"))   # 계절 필터 폭 (홀수)
STL_ROBUST     <- as.logical(Sys.getenv("STL_ROBUST",     "TRUE")) # 이상치 강건 분해

# ── ARIMA 설정 ───────────────────────────────────────────────────────────────
ARIMA_HORIZON  <- as.integer(Sys.getenv("ARIMA_HORIZON",  "12"))   # 예측 개월 수
ARIMA_SEASONAL <- as.logical(Sys.getenv("ARIMA_SEASONAL", "TRUE")) # 계절성 포함

# ── Prophet 설정 ─────────────────────────────────────────────────────────────
PROPHET_HORIZON      <- as.integer(Sys.getenv("PROPHET_HORIZON",      "12"))
PROPHET_CHANGEPOINTS <- as.integer(Sys.getenv("PROPHET_CHANGEPOINTS", "25"))

# ── CCF 설정 ─────────────────────────────────────────────────────────────────
CCF_LAG_MAX    <- as.integer(Sys.getenv("CCF_LAG_MAX",    "12"))   # 최대 시차 (개월)

# ── 뉴스 피처 제외 목록 ──────────────────────────────────────────────────────
# 네이버 뉴스 API 미제공으로 분석 제외
NEWS_FEATURES <- c("kr_news_employment_value", "kr_news_layoff_value", "kr_news_startup_value")

# ── BQ 클라이언트 인증 (ADC) ─────────────────────────────────────────────────
bq_auth(path = NULL)  # Cloud Run: ADC 자동 사용. 로컬: gcloud auth application-default login

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

# ── 헬퍼: BQ 테이블에 결과 APPEND ────────────────────────────────────────────
bq_append <- function(df, table_name) {
  tbl <- bq_table(BQ_PROJECT, ANALYSIS_DATASET, table_name)
  bq_table_upload(tbl, df, create_disposition = "CREATE_IF_NEEDED",
                  write_disposition = "WRITE_APPEND")
  message(glue("[BQ] {table_name} {nrow(df)}행 저장 완료"))
}

# ── 헬퍼: BQ 테이블 전체 교체 (이번 run_id 기준) ────────────────────────────
bq_replace_run <- function(df, table_name, run_id) {
  tbl <- bq_table(BQ_PROJECT, ANALYSIS_DATASET, table_name)
  # 이전 run 결과 삭제 후 새 결과 삽입
  if (bq_table_exists(tbl)) {
    bq_perform_query(
      glue("DELETE FROM `{BQ_PROJECT}.{ANALYSIS_DATASET}.{table_name}` WHERE run_id = '{run_id}'"),
      billing = BQ_PROJECT
    )
  }
  bq_table_upload(tbl, df, create_disposition = "CREATE_IF_NEEDED",
                  write_disposition = "WRITE_APPEND")
  message(glue("[BQ] {table_name} run_id={run_id} → {nrow(df)}행 저장"))
}

# ── run_id: Python 파이프라인과 동일한 run_id를 환경변수로 전달 ───────────────
RUN_ID <- Sys.getenv("PIPELINE_RUN_ID", format(Sys.time(), "%Y%m%dT%H%M%SZ"))

message(glue(
  "[config] project={BQ_PROJECT}  dataset={BQ_DATASET}  ",
  "period={PERIOD_START}~{PERIOD_END}  run_id={RUN_ID}"
))

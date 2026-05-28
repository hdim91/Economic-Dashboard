# ============================================================================
# render_report.R
# RMarkdown 리포트 렌더링 오케스트레이터
#
# 실행:
#   Rscript render_report.R
#   Rscript render_report.R --run-id 20260312T090000Z
#   Rscript render_report.R --out-dir /tmp/reports --format html
#
# 환경변수:
#   GCP_PROJECT_ID      BigQuery 프로젝트 (필수)
#   BQ_DATASET          kosis_stats (기본)
#   PERIOD_START/END    분석 기간
#   PIPELINE_RUN_ID     run_id (없으면 최신 자동 탐색)
#   REPORT_OUT_DIR      출력 디렉토리 (기본: ./output)
#   REPORT_FORMAT       html | pdf (기본: html)
#   REPORT_UPLOAD_GCS   true/false — GCS 버킷 업로드 여부
#   GCS_BUCKET          gs://... 형태 버킷명
# ============================================================================

suppressPackageStartupMessages({
  library(rmarkdown)
  library(glue)
  library(lubridate)
  library(bigrquery)
  library(httr)
})

# ── Slack 알림 헬퍼 ───────────────────────────────────────────────────────
slack_notify <- function(text, color = "#36a64f") {
  url <- Sys.getenv("SLACK_WEBHOOK_URL", "")
  if (nchar(trimws(url)) == 0) return(invisible(NULL))
  tryCatch({
    httr::POST(
      url = trimws(url),
      httr::content_type_json(),
      body = jsonlite::toJSON(list(
        attachments = list(list(
          color = color,
          text  = text,
          footer = paste0("auto-report-489722 | ", format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC"))
        ))
      ), auto_unbox = TRUE)
    )
  }, error = function(e) {
    warning(glue("[render] Slack 알림 실패: {conditionMessage(e)}"))
  })
}

# ── CLI 인자 파싱 ─────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(flag, default = "") {
  idx <- which(args == flag)
  if (length(idx) == 0 || idx + 1 > length(args)) return(default)
  args[idx + 1]
}

cli_run_id  <- get_arg("--run-id")
cli_out_dir <- get_arg("--out-dir")
cli_format  <- get_arg("--format")

# ── 설정 ─────────────────────────────────────────────────────────────────
BQ_PROJECT   <- Sys.getenv("GCP_PROJECT_ID",  "")
BQ_DATASET   <- Sys.getenv("BQ_DATASET",      "kosis_stats")
PERIOD_START <- Sys.getenv("PERIOD_START",
  format(floor_date(Sys.Date() %m-% years(5), "month"), "%Y-%m"))
PERIOD_END   <- Sys.getenv("PERIOD_END",
  format(floor_date(Sys.Date() %m-% months(1), "month"), "%Y-%m"))

OUT_DIR     <- ifelse(nchar(cli_out_dir) > 0, cli_out_dir,
               Sys.getenv("REPORT_OUT_DIR", file.path(getwd(), "output")))
RPT_FORMAT  <- ifelse(nchar(cli_format) > 0, cli_format,
               Sys.getenv("REPORT_FORMAT", "html"))
UPLOAD_GCS  <- tolower(Sys.getenv("REPORT_UPLOAD_GCS", "false")) == "true"
GCS_BUCKET  <- Sys.getenv("GCS_BUCKET", "")

# ── run_id 결정 ───────────────────────────────────────────────────────────
if (nchar(cli_run_id) > 0) {
  RUN_ID <- cli_run_id
  message(glue("[render] run_id (CLI): {RUN_ID}"))
} else if (nchar(Sys.getenv("PIPELINE_RUN_ID")) > 0) {
  RUN_ID <- Sys.getenv("PIPELINE_RUN_ID")
  message(glue("[render] run_id (env): {RUN_ID}"))
} else {
  # BQ에서 최신 run_id 탐색
  message("[render] run_id 미지정 — BQ에서 최신 run_id 탐색")
  bq_auth(path = NULL)
  # analysis_forecast_arima 기준 최신 run_id 사용 (가장 마지막 완료 테이블)
  run_id_result <- tryCatch(
    bq_table_download(bq_perform_query(
      glue("SELECT run_id FROM `{BQ_PROJECT}.{BQ_DATASET}.analysis_forecast_arima`
            GROUP BY run_id ORDER BY MAX(analyzed_at) DESC LIMIT 1"),
      billing = BQ_PROJECT
    )),
    error = function(e) {
      # fallback: stl_components
      tryCatch(
        bq_table_download(bq_perform_query(
          glue("SELECT run_id FROM `{BQ_PROJECT}.{BQ_DATASET}.analysis_stl_components`
                GROUP BY run_id ORDER BY MAX(analyzed_at) DESC LIMIT 1"),
          billing = BQ_PROJECT
        )),
        error = function(e2) NULL
      )
    }
  )
  RUN_ID <- if (!is.null(run_id_result) && nrow(run_id_result) > 0)
    run_id_result$run_id[1]
  else
    format(Sys.time(), "%Y%m%dT%H%M%SZ")
  message(glue("[render] run_id (BQ latest): {RUN_ID}"))
}

# ── 출력 경로 설정 ────────────────────────────────────────────────────────
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

report_date  <- format(Sys.Date(), "%Y%m%d")
report_month <- gsub("-", "", PERIOD_END)  # 예: 202502
ext          <- if (RPT_FORMAT == "pdf") "pdf" else "html"
out_filename <- glue("employment_report_{report_month}.{ext}")
out_path     <- file.path(OUT_DIR, out_filename)

message(glue(
  "[render] 설정\n",
  "  project     : {BQ_PROJECT}\n",
  "  dataset     : {BQ_DATASET}\n",
  "  period      : {PERIOD_START} ~ {PERIOD_END}\n",
  "  run_id      : {RUN_ID}\n",
  "  format      : {RPT_FORMAT}\n",
  "  output      : {out_path}"
))

# ── 렌더링 ───────────────────────────────────────────────────────────────
# Rscript 실행 시 현재 디렉토리 기준으로 Rmd 파일 탐색
rmd_path <- file.path(getwd(), "employment_report.Rmd")

output_format <- switch(
  RPT_FORMAT,
  "pdf"  = rmarkdown::pdf_document(
    toc            = TRUE,
    toc_depth      = 3,
    number_sections = TRUE,
    latex_engine   = "xelatex"   # 한글 지원
  ),
  rmarkdown::html_document(
    theme          = "flatly",
    highlight      = "tango",
    toc            = TRUE,
    toc_float      = list(collapsed = FALSE, smooth_scroll = TRUE),
    toc_depth      = 3,
    number_sections = TRUE,
    fig_width      = 10,
    fig_height     = 5,
    df_print       = "paged",
    css            = "report_style.css",
    self_contained = TRUE   # 이미지·CSS 인라인 포함 → 단일 파일
  )
)

t_start <- proc.time()["elapsed"]
message("[render] 렌더링 시작...")

tryCatch({
  rmarkdown::render(
    input         = rmd_path,
    output_format = output_format,
    output_file   = out_path,
    params        = list(
      run_id       = RUN_ID,
      period_start = PERIOD_START,
      period_end   = PERIOD_END
    ),
    envir         = new.env(parent = globalenv()),
    quiet         = FALSE
  )

  elapsed <- round(proc.time()["elapsed"] - t_start, 1)
  file_mb <- round(file.size(out_path) / 1024 / 1024, 2)
  message(glue(
    "[render] 완료  파일: {out_path}  크기: {file_mb}MB  소요: {elapsed}초"
  ))

  # ── GCS 업로드 (GCS JSON REST API + ADC 메타데이터 토큰) ───────────────
  gcs_public_url <- NA_character_

  if (UPLOAD_GCS && nchar(GCS_BUCKET) > 0) {
    tryCatch({
      library(httr)

      bucket_name <- sub("^gs://", "", GCS_BUCKET)
      gcs_object  <- glue("reports/{out_filename}")
      gcs_uri     <- glue("gs://{bucket_name}/{gcs_object}")

      message(glue("[render] GCS 업로드 시작: {gcs_uri}"))

      # Cloud Run ADC 메타데이터 서버에서 액세스 토큰 취득
      token_resp <- httr::GET(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        httr::add_headers("Metadata-Flavor" = "Google")
      )
      token <- httr::content(token_resp)$access_token

      # GCS resumable upload (대용량 파일 대응)
      obj_encoded <- utils::URLencode(gcs_object, reserved = TRUE)
      init_url <- glue("https://storage.googleapis.com/upload/storage/v1/b/{bucket_name}/o?uploadType=resumable&name={obj_encoded}")

      # 1단계: 세션 URI 획득
      init_resp <- httr::POST(
        init_url,
        httr::add_headers(
          Authorization             = paste("Bearer", token),
          `Content-Type`            = "application/json",
          `X-Upload-Content-Type`   = "text/html",
          `X-Upload-Content-Length` = as.character(file.size(out_path))
        ),
        body = "{}"
      )
      if (httr::http_error(init_resp)) stop(httr::content(init_resp, "text", encoding = "UTF-8"))
      session_uri <- httr::headers(init_resp)$location

      # 2단계: 실제 업로드
      resp <- httr::PUT(
        session_uri,
        httr::add_headers(`Content-Type` = "text/html"),
        body = httr::upload_file(out_path, type = "text/html")
      )
      if (httr::http_error(resp)) stop(httr::content(resp, "text", encoding = "UTF-8"))

      gcs_public_url <- glue("https://storage.googleapis.com/{bucket_name}/{gcs_object}")
      message(glue("[render] GCS 업로드 완료: {gcs_public_url}"))

    }, error = function(e) {
      warning(glue("[render] GCS 업로드 실패: {conditionMessage(e)}"))
    })
  }

  # ── BigQuery 리포트 메타데이터 저장 ────────────────────────────────────
  tryCatch({
    report_meta <- tibble::tibble(
      run_id        = RUN_ID,
      period_start  = PERIOD_START,
      period_end    = PERIOD_END,
      report_format = RPT_FORMAT,
      filename      = out_filename,
      file_size_mb  = round(file.size(out_path) / 1024 / 1024, 2),
      gcs_url       = gcs_public_url,
      gcs_bucket    = ifelse(nchar(GCS_BUCKET) > 0, GCS_BUCKET, NA_character_),
      rendered_at   = as.character(Sys.time()),
      elapsed_sec   = elapsed
    )

    tbl <- bigrquery::bq_table(BQ_PROJECT, BQ_DATASET, "report_history")
    bigrquery::bq_table_upload(
      tbl, report_meta,
      create_disposition = "CREATE_IF_NEEDED",
      write_disposition  = "WRITE_APPEND"
    )
    message("[render] BQ 메타데이터 저장 완료 -> report_history")

  }, error = function(e) {
    warning(glue("[render] BQ 저장 실패: {conditionMessage(e)}"))
  })

  # ── Slack 성공 알림 ───────────────────────────────────────────────────
  slack_msg <- glue(
    "*[리포트 생성 완료]* :white_check_mark:\n",
    ">  기간: {PERIOD_START} ~ {PERIOD_END}\n",
    ">  파일: {out_filename}  ({file_mb}MB)\n",
    ">  소요: {elapsed}초\n",
    if (!is.na(gcs_public_url)) glue(">  URL: {gcs_public_url}") else ""
  )
  slack_notify(slack_msg, color = "#36a64f")

  # 성공 시 경로를 stdout으로 출력 (main.py subprocess 결과 파싱용)
  cat(out_path, "\n")
  quit(status = 0)

}, error = function(e) {
  message(glue("[render] 렌더링 실패: {conditionMessage(e)}"))
  slack_notify(
    glue("*[리포트 생성 실패]* :x:\n>  오류: {conditionMessage(e)}"),
    color = "#ff0000"
  )
  quit(status = 1)
})

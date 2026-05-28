#!/bin/bash
set -e

echo "[run_all] Step 1: R 분석 실행"
cd /app/analysis
Rscript run_analysis.R

# 분석 완료 후 최신 run_id를 BQ에서 조회해서 환경변수로 전달
echo "[run_all] 분석 run_id 조회 중..."
ANALYSIS_RUN_ID=$(Rscript -e "
suppressMessages({
  library(bigrquery)
  library(glue)
  bq_auth(path=NULL)
  proj <- Sys.getenv('GCP_PROJECT_ID','auto-report-489722')
  ds   <- Sys.getenv('BQ_DATASET','kosis_stats')
  res  <- tryCatch(
    bigrquery::bq_table_download(bigrquery::bq_project_query(
      proj,
      glue('SELECT run_id FROM \`{proj}.{ds}.analysis_forecast_arima\`
            GROUP BY run_id ORDER BY MAX(analyzed_at) DESC LIMIT 1')
    )),
    error=function(e) NULL
  )
  if (!is.null(res) && nrow(res)>0) cat(res\$run_id[1]) else cat('')
})" 2>/dev/null)

if [ -n "$ANALYSIS_RUN_ID" ]; then
  echo "[run_all] 분석 run_id: $ANALYSIS_RUN_ID"
  export PIPELINE_RUN_ID="$ANALYSIS_RUN_ID"
else
  echo "[run_all] run_id 조회 실패 — render_report.R이 자동 탐색"
fi

echo "[run_all] Step 2: 리포트 렌더링"
cd /app/report
Rscript render_report.R "$@"

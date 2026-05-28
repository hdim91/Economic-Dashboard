#!/bin/bash
# ============================================================================
# run_all.sh — 통합 분석·렌더링 진입점
# 고용동향 R 분석 → 금융동향 R 분석 → 고용동향 리포트 렌더링 순차 실행
# ============================================================================
set -e

echo "============================================================"
echo "[report-job] 통합 분석·렌더링 시작"
echo "============================================================"

# ── Step 1: 고용동향 R 분석 ─────────────────────────────────────────────────
echo ""
echo "[report-job] Step 1: 고용동향 R 분석"
echo "------------------------------------------------------------"
cd /app/analysis
Rscript run_analysis.R

# 고용동향 분석 완료 후 run_id 조회
echo ""
echo "[report-job] 고용동향 분석 run_id 조회 중..."
EMP_RUN_ID=$(Rscript -e "
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

if [ -n "$EMP_RUN_ID" ]; then
  echo "[report-job] 고용동향 run_id: $EMP_RUN_ID"
  export PIPELINE_RUN_ID="$EMP_RUN_ID"
else
  echo "[report-job] run_id 조회 실패 — render_report.R이 자동 탐색"
fi

# ── Step 2: 금융동향 R 분석 ─────────────────────────────────────────────────
echo ""
echo "[report-job] Step 2: 금융동향 R 분석"
echo "------------------------------------------------------------"
cd /app/finance_analysis

# 금융동향 전용 데이터셋 지정 (고용동향과 분리)
FINANCE_BQ_DATASET="${FINANCE_BQ_DATASET:-finance_stats}" \
  Rscript run_analysis.R

# ── Step 3: 고용동향 리포트 렌더링 ──────────────────────────────────────────
echo ""
echo "[report-job] Step 3: 고용동향 리포트 렌더링"
echo "------------------------------------------------------------"
cd /app/report
Rscript render_report.R "$@"

# ── Step 4: 금융동향 리포트 렌더링 ──────────────────────────────────────────
echo ""
echo "[report-job] Step 4: 금융동향 리포트 렌더링"
echo "------------------------------------------------------------"
cd /app/finance_report

# 금융동향 run_id: fin_analysis_trend_summary 최신값 조회
FIN_RUN_ID=$(Rscript -e "
suppressMessages({
  library(bigrquery)
  library(glue)
  bq_auth(path=NULL)
  proj <- Sys.getenv('GCP_PROJECT_ID','auto-report-489722')
  ds   <- Sys.getenv('FINANCE_BQ_DATASET','finance_stats')
  res  <- tryCatch(
    bigrquery::bq_table_download(bigrquery::bq_project_query(
      proj,
      glue('SELECT run_id FROM \`{proj}.{ds}.fin_analysis_trend_summary\`
            GROUP BY run_id ORDER BY MAX(analyzed_at) DESC LIMIT 1')
    )),
    error=function(e) NULL
  )
  if (!is.null(res) && nrow(res)>0) cat(res\$run_id[1]) else cat('')
})" 2>/dev/null)

if [ -n "$FIN_RUN_ID" ]; then
  echo "[report-job] 금융동향 run_id: $FIN_RUN_ID"
  FINANCE_BQ_DATASET="${FINANCE_BQ_DATASET:-finance_stats}" \
  REPORT_OUT_DIR=/app/finance_report/output \
  PIPELINE_RUN_ID="$FIN_RUN_ID" \
    Rscript render_finance_report.R "$@"
else
  echo "[report-job] 금융동향 분석 데이터 없음 — 렌더링 건너뜀"
fi

echo ""
echo "============================================================"
echo "[report-job] 통합 분석·렌더링 완료"
echo "============================================================"

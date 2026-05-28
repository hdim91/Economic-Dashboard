# ============================================================================
# Dockerfile — 통합 리포트 (고용동향 + 금융동향 분석·렌더링, Cloud Run Job)
# 빌드 컨텍스트: 프로젝트 루트
#   gcloud builds submit --config infra/cloudbuild.yaml .
# ============================================================================

FROM rocker/verse:4.4

# ── 시스템 패키지 + gcloud CLI ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl-dev libcurl4-openssl-dev libxml2-dev \
        libgit2-dev libfontconfig1-dev \
        libharfbuzz-dev libfribidi-dev \
        libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev \
        fonts-noto-cjk \
        curl python3 \
    && curl -sSL https://sdk.cloud.google.com | bash -s -- \
        --disable-prompts --install-dir=/usr/local \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/google-cloud-sdk/bin:${PATH}"

# ── R 패키지 설치 ─────────────────────────────────────────────────────────
# 고용동향 + 금융동향 분석에 필요한 패키지 통합
RUN Rscript -e "install.packages(c(\
  'bigrquery','googleCloudStorageR','httr','jsonlite',\
  'tibble','dplyr','tidyr','lubridate','glue','purrr',\
  'zoo','tsibble','moments','tseries','forecast','prophet',\
  'ggplot2','scales','patchwork','plotly',\
  'knitr','rmarkdown','kableExtra','stringr'\
  ), repos='https://cloud.r-project.org', Ncpus=4)"

WORKDIR /app

# ── 고용동향 분석 스크립트 ────────────────────────────────────────────────
COPY pipeline/employment/analysis/ analysis/

# ── 금융동향 분석 스크립트 ────────────────────────────────────────────────
COPY pipeline/finance/analysis/ finance_analysis/

# ── 고용동향 리포트 파일 ──────────────────────────────────────────────────
COPY pipeline/employment/report/employment_report.Rmd  report/
COPY pipeline/employment/report/report_style.css       report/
COPY pipeline/employment/report/render_report.R        report/

# ── 금융동향 리포트 파일 ──────────────────────────────────────────────────
COPY pipeline/finance/report/finance_report.Rmd        finance_report/
COPY pipeline/finance/report/render_finance_report.R   finance_report/

# ── 출력 디렉토리 ─────────────────────────────────────────────────────────
RUN mkdir -p /app/report/output /app/finance_report/output

# ── 진입점 스크립트 ───────────────────────────────────────────────────────
COPY infra/report-job/run_all.sh run_all.sh
RUN chmod +x run_all.sh

# ── 환경변수 기본값 ───────────────────────────────────────────────────────
ENV REPORT_OUT_DIR=/app/report/output \
    REPORT_FORMAT=html \
    REPORT_UPLOAD_GCS=false \
    FINANCE_BQ_DATASET=finance_stats

# ── 환경변수 설명 ─────────────────────────────────────────────────────────
# 필수:
#   GCP_PROJECT_ID
# 고용동향:
#   BQ_DATASET (기본: kosis_stats)
# 금융동향:
#   FINANCE_BQ_DATASET (기본: finance_stats)
# 공통:
#   GCS_BUCKET, REPORT_UPLOAD_GCS=true (운영 시 true로 설정)
#   PIPELINE_RUN_ID (없으면 자동 생성)

ENTRYPOINT ["/bin/bash", "run_all.sh"]

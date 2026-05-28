# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dockerfile — 통합 파이프라인 (고용동향 + 금융동향, Cloud Run Job)
# 빌드 컨텍스트: 프로젝트 루트
#   gcloud builds submit --config infra/cloudbuild.yaml .
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 빌드 스테이지 ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY infra/pipeline-job/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── 런타임 스테이지 ────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# ── 고용동향 모듈 ─────────────────────────────────────────────────────────────
COPY pipeline/employment/config.py                employment/
COPY pipeline/employment/validator.py             employment/
COPY pipeline/employment/transform.py             employment/
COPY pipeline/employment/kosis_fetcher.py         employment/
COPY pipeline/employment/naver_fetcher.py         employment/
COPY pipeline/employment/bls_fetcher.py           employment/
COPY pipeline/employment/google_trends_fetcher.py employment/
COPY pipeline/employment/bq_loader.py             employment/
COPY pipeline/employment/mart_loader.py           employment/
COPY pipeline/employment/feature_store.py         employment/
COPY pipeline/employment/notifier.py              employment/
COPY pipeline/employment/export_dashboard_data.py employment/
COPY pipeline/employment/main.py                  employment/

# ── 금융동향 모듈 ─────────────────────────────────────────────────────────────
COPY pipeline/finance/finance_config.py           finance/
COPY pipeline/finance/ecos_fetcher.py             finance/
COPY pipeline/finance/fred_fetcher.py             finance/
COPY pipeline/finance/finance_kosis_fetcher.py    finance/
COPY pipeline/finance/finance_bq_loader.py        finance/
COPY pipeline/finance/finance_main.py             finance/

# ── 진입점 스크립트 ───────────────────────────────────────────────────────────
COPY infra/pipeline-job/run_pipeline.sh run_pipeline.sh
RUN chmod +x run_pipeline.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── 환경변수 설명 ─────────────────────────────────────────────────────────────
# 고용동향 필수:
#   KOSIS_API_KEY, GCP_PROJECT_ID
# 금융동향 필수:
#   GCP_PROJECT_ID  (동일)
# 금융동향 선택:
#   ECOS_API_KEY, FRED_API_KEY, KOSIS_API_KEY (금융 KOSIS 공유)
#   FINANCE_BQ_DATASET (기본: finance_stats)
# 공통 선택:
#   PERIOD_START, PERIOD_END, SLACK_WEBHOOK_URL
#   ANALYSIS_ENABLED=false (pipeline-job에서는 R 분석 비활성화 권장)

ENTRYPOINT ["/bin/bash", "run_pipeline.sh"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dockerfile — 고용동향 파이프라인 (Cloud Run Job)
# Multi-stage build: builder → runtime
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── 빌드 스테이지 ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# 빌드 의존성 (numpy/pandas C 확장 컴파일용)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── 런타임 스테이지 ────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# 빌드 스테이지에서 설치된 패키지 복사
COPY --from=builder /install /usr/local

# ── 소스 코드 복사 ────────────────────────────────────────────────────────────
COPY config.py            .
COPY validator.py         .
COPY transform.py         .
COPY kosis_fetcher.py     .
COPY naver_fetcher.py     .
COPY bls_fetcher.py       .
COPY google_trends_fetcher.py .
COPY bq_loader.py         .
COPY mart_loader.py       .
COPY feature_store.py     .
COPY notifier.py          .
COPY export_dashboard_data.py .
COPY main.py              .

# ── 환경 변수 기본값 ──────────────────────────────────────────────────────────
# 실행 환경(Cloud Run Job / Secret Manager)에서 아래 변수를 주입하세요.
#
#   필수:
#     KOSIS_API_KEY          KOSIS Open API 인증키
#     GCP_PROJECT_ID         BigQuery 프로젝트 ID
#
#   선택:
#     NAVER_CLIENT_ID        NAVER 검색·DataLab API Client ID
#     NAVER_CLIENT_SECRET    NAVER API Client Secret
#     BLS_API_KEY            BLS Public Data API 키
#     SLACK_WEBHOOK_URL      Slack Incoming Webhook URL (알림 비활성화 시 생략)
#
#   수집 기간 (없으면 실행일 기준 자동 산출):
#     PERIOD_START           예) 2021-03
#     PERIOD_END             예) 2026-02
#
#   Transform 제어:
#     TRANSFORM_ENABLED      true(기본) | false  — 파생 지표 계산 여부
#     TRANSFORM_IQR_K        3.0(기본)           — 이상치 판정 배수
#
#   BigQuery 테이블 (기본값 사용 가능):
#     BQ_DATASET             kosis_stats
#     BQ_RAW_TABLE           employment_raw
#     BQ_STG_TABLE           employment_stg

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── 헬스체크 (Cloud Run Job은 선택사항) ──────────────────────────────────────
# HEALTHCHECK 없음 — Job 형태이므로 프로세스 종료 코드로 성공/실패 판단

ENTRYPOINT ["python", "main.py"]

# ── 실행 예시 ─────────────────────────────────────────────────────────────────
# 기본 실행 (전체 소스, 자동 기간):
#   docker run --env-file .env pipeline
#
# 특정 소스만:
#   docker run --env-file .env pipeline --source kosis naver
#
# Dry-run (수집+변환만, BQ 적재 없음):
#   docker run --env-file .env pipeline --dry-run
#
# 기간 직접 지정:
#   docker run --env-file .env pipeline --period 2024-01 2025-12
#
# 변환 스킵:
#   docker run --env-file .env pipeline --no-transform
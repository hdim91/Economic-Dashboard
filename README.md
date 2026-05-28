# Economic Dashboard

> 고용·금융동향 경제 지표를 자동 수집·분석·시각화하는 GCP 기반 완전 자동화 파이프라인

**라이브 대시보드 →** https://storage.googleapis.com/auto-report-489722-reports/index.html

---

## 개요

공공 API(KOSIS, ECOS, BLS, FRED)와 민간 API(Naver DataLab, Google Trends)에서  
경제 지표를 매월 자동 수집하여 BigQuery에 적재하고, R로 통계 분석 후  
HTML 리포트를 생성해 GCS에 정적 호스팅하는 end-to-end 데이터 파이프라인입니다.

```
[API 수집] → [BigQuery 적재] → [R 분석] → [HTML 리포트] → [GCS 호스팅]
     ↑                                                            ↓
  Cloud Run Job (월 1회 자동 실행)                        정적 대시보드
```

---

## 주요 기능

### 고용동향 파이프라인
- **KOSIS** — 성별·연령별·산업별·계절조정 취업자, 월평균 임금, 구직급여
- **BLS** — 미국 비농업 취업자(NFP), JOLTS 구인·이직
- **Naver DataLab** — 취업·구직 검색트렌드
- **Google Trends** — 구직·실업 검색량

### 금융동향 파이프라인
- **ECOS(한국은행)** — 기준금리, 국고채, CD, 가계신용, M2, 환율, CPI, PPI
- **FRED(미국 연준)** — 연방기금금리, 미국 국채, 실업률, CPI
- **KOSIS** — 소비자물가지수, 생산자물가지수, 주택매매가격지수

### 분석 · 리포트
- **R 통계 분석** — STL 분해, ARIMA/Prophet 예측, 한미 금리 스프레드
- **자동 HTML 리포트** — R Markdown → Self-contained HTML → GCS 업로드
- **대시보드** — 실시간 데이터 바인딩, SVG 차트, 반응형 UI

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 데이터 수집 | Python 3.12 (requests, httpx) |
| 데이터 웨어하우스 | BigQuery (MERGE Upsert, Dedup View, Partition/Cluster) |
| 통계 분석 | R 4.4 (forecast, prophet, tsibble, ggplot2) |
| 리포트 렌더링 | R Markdown → Pandoc → Self-contained HTML |
| 컨테이너 | Docker (python:3.12-slim, rocker/verse:4.4) |
| 실행 환경 | Cloud Run Jobs |
| 스케줄링 | Cloud Scheduler (매월 15일 KST) |
| 스토리지 | GCS (정적 호스팅, 리포트 아카이브) |
| 빌드 | Cloud Build (병렬 빌드, Artifact Registry) |
| 보안 | Secret Manager (API Key 관리) |
| 프론트엔드 | Vanilla JS, SVG 차트, GCS 정적 호스팅 |

---

## 프로젝트 구조

```
.
├── pipeline/
│   ├── employment/          # 고용동향 수집·적재·분석·리포트
│   │   ├── main.py          # 오케스트레이터
│   │   ├── kosis_fetcher.py
│   │   ├── bls_fetcher.py
│   │   ├── ecos_fetcher.py  # (금융동향과 공유)
│   │   ├── bq_loader.py     # MERGE Upsert
│   │   ├── feature_store.py # BQ Mart View 생성
│   │   ├── analysis/        # R 분석 모듈 (00~07)
│   │   └── report/          # employment_report.Rmd
│   └── finance/             # 금융동향 수집·적재·분석·리포트
│       ├── finance_main.py
│       ├── ecos_fetcher.py
│       ├── fred_fetcher.py
│       ├── finance_kosis_fetcher.py
│       ├── finance_bq_loader.py
│       ├── analysis/        # R 분석 모듈 (trend, kr_us_spread)
│       └── report/          # finance_report.Rmd
├── frontend/
│   ├── index.html           # 메인 대시보드 (Vanilla JS)
│   └── export_dashboard_data.py  # BQ → data.json 생성
├── infra/
│   ├── cloudbuild.yaml      # 병렬 빌드 + Cloud Run 배포
│   ├── pipeline-job/        # 수집·적재 Cloud Run Job
│   └── report-job/          # 분석·렌더링 Cloud Run Job
└── docs/
    ├── architecture.md
    ├── api-inventory.md
    └── decisions.md
```

---

## 아키텍처

```
┌─────────────────────────────────────────────┐
│  Cloud Scheduler (매월 15일 07:00 KST)       │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │   pipeline-job      │  Cloud Run Job
         │  (Python 3.12)      │
         │  ┌──────────────┐   │
         │  │ KOSIS/BLS    │   │
         │  │ ECOS/FRED    │   │  ──→  BigQuery
         │  │ Naver/GTrend │   │       (kosis_stats)
         │  └──────────────┘   │       (finance_stats)
         └────────────────────┘
                   │ 완료 후 08:30
         ┌─────────▼──────────┐
         │    report-job       │  Cloud Run Job
         │   (rocker/verse)    │
         │  ┌──────────────┐   │
         │  │ R 분석       │   │
         │  │ Rmd 렌더링   │   │  ──→  GCS
         │  └──────────────┘   │       (리포트 HTML)
         └────────────────────┘
                   │
         ┌─────────▼──────────┐
         │   GCS 정적 호스팅   │
         │  index.html         │
         │  dashboard/data.json│
         └────────────────────┘
```

---

## 로컬 실행

### 사전 요구사항
- Python 3.12+, R 4.4+
- gcloud CLI (인증 완료)
- BigQuery 데이터셋 생성

### 환경변수 설정

```bash
cp .env.example .env
# .env 파일에 실제 API Key 값 입력
```

### 고용동향 파이프라인 실행

```bash
cd pipeline/employment
pip install -r requirements.txt
python main.py --dry-run           # 실제 적재 없이 테스트
python main.py                     # 전체 실행
```

### 금융동향 파이프라인 실행

```bash
cd pipeline/finance
python finance_main.py --dry-run
python finance_main.py
```

### Docker로 실행

```bash
# pipeline-job
docker build -f infra/pipeline-job/Dockerfile -t pipeline-job .
docker run --env-file .env pipeline-job

# report-job
docker build -f infra/report-job/Dockerfile -t report-job .
docker run --env-file .env report-job
```

---

## Cloud Run 배포

```bash
# Cloud Build로 이미지 빌드 + 배포
gcloud builds submit --config infra/cloudbuild.yaml . \
  --project YOUR_GCP_PROJECT_ID \
  --substitutions SHORT_SHA=$(git rev-parse --short HEAD)

# 수동 실행
gcloud run jobs execute pipeline-job --region asia-northeast3 --wait
gcloud run jobs execute report-job   --region asia-northeast3 --wait
```

---

## BigQuery 스키마

### 고용동향 (`kosis_stats`)
| 테이블 | 설명 |
|---|---|
| `employment_raw` | 원본 수집 데이터 (파티션: period_date) |
| `employment_raw_dedup` | Dedup View (최신 ingested_at 기준) |
| `fs_long` | Feature Store Long Format |
| `fs_wide` | Feature Store Wide Format |
| `analysis_forecast_arima` | ARIMA 예측 결과 |
| `analysis_forecast_models` | Prophet 모델 메타데이터 |
| `report_history` | 리포트 발행 이력 |

### 금융동향 (`finance_stats`)
| 테이블 | 설명 |
|---|---|
| `finance_raw` | 원본 수집 데이터 |
| `finance_raw_dedup` | Dedup View |
| `fin_analysis_trend_series` | 변수별 MoM/YoY/Streak 시계열 |
| `fin_analysis_trend_summary` | 최신 스냅샷 요약 |
| `fin_analysis_kr_us_spread` | 한미 금리·CPI 스프레드 시계열 |
| `fin_analysis_kr_us_summary` | 스프레드 최신 스냅샷 |
| `finance_report_history` | 금융 리포트 발행 이력 |

---

## 라이선스

MIT License — 자유롭게 사용, 수정, 배포 가능합니다.

# API Dashboard Project — Antigravity 지침

> 이 문서를 읽고 지시에 따라 작업한다.
> 세션 시작 시 반드시 이 파일을 먼저 읽는다.

---

## 프로젝트 개요

GCS에 호스팅된 정적 HTML 대시보드 프로젝트.

- **GCS 페이지 URL**: https://storage.googleapis.com/auto-report-489722-reports/index.html
- **데이터 JSON URL**: https://storage.googleapis.com/auto-report-489722-reports/dashboard/data.json
- **수정 파일**: frontend/index.html (이 파일만 담당)
- **GCS CORS**: 설정 완료 — 모든 origin에서 GET 허용

---

## 담당 영역

| 담당 | 경로 |
|---|---|
| 메인 대시보드 페이지 | frontend/index.html |

### 절대 수정 금지
- reference_dont_use/
- pipeline/
- infra/
- frontend/export_dashboard_data.py
- CLAUDE.md, AGENTS.md, ANTIGRAVITY.md 자체

---

## 세션 프로토콜

### 시작 시 (순서 엄수)

1. cat agent-lock.json
2. cat feature_list.json
3. git log --oneline -5

---

## 현재 태스크: 브라우저 최종 검증

### 배경

fe-03 (금융동향 스프레드 차트), fe-05 (금융 리포트 카드 활성화) 코드 구현은
이미 완료되어 frontend/index.html에 반영되어 있다.

오늘(2026-04-22) pipeline-job + report-job이 모두 성공적으로 실행되어
실제 금융 데이터가 BQ와 GCS에 적재되었다:

- data.json: 금융 KPI, finance_spread, finance_spread_summary, latest_finance_report 포함
- GCS 리포트: https://storage.googleapis.com/auto-report-489722-reports/reports/finance_report_202603.html

### 검증 체크리스트

GCS URL로 직접 열어 아래 항목을 순서대로 확인한다:

    https://storage.googleapis.com/auto-report-489722-reports/index.html

**fe-05 — 금융 리포트 카드**

- [ ] 금융동향 리포트 카드가 흐리지 않고 정상 표시 (opacity 1.0)
- [ ] "리포트 열기" 버튼이 클릭 가능한 상태
- [ ] "최신 발행" 옆에 날짜가 표시됨 (예: 2026년 4월 22일)
- [ ] 버튼 클릭 시 finance_report_202603.html 새 탭 열림

**fe-03 — 금융동향 스프레드 차트**

- [ ] 금융 KPI 섹션 아래에 "금융동향 — 한미 금리 스프레드" 섹션 표시
- [ ] SVG 라인차트 3개 렌더링:
  - 한미 기준금리 스프레드 (KR−US)
  - 한미 국채 10년 스프레드 (KR−US)
  - 한미 CPI 갭 (YoY% 기준)
- [ ] 각 차트 하단에 최신값(%p)과 기준 기간 표시
- [ ] 0선(점선) 표시 — 스프레드가 양/음 구간을 통과하는 경우
- [ ] 요약 테이블 표시:
  - 방향 "확대" → 빨간색
  - 방향 "축소" → 파란색

**금융 KPI 섹션**

- [ ] 한국 기준금리, 미국 연방기금금리, 원달러 환율, CPI, 주택매매가격지수 값 표시

---

### 이상 발견 시 수정 절차

문제가 있으면 frontend/index.html을 수정하고 아래 명령으로 GCS 배포:

    gcloud storage cp frontend/index.html gs://auto-report-489722-reports/index.html --content-type=text/html --project=auto-report-489722

수정 없이 검증만 완료한 경우에도 아래 커밋으로 세션 종료:

    git add frontend/index.html
    git commit -m "[antigravity] chore: 브라우저 검증 완료 (fe-03, fe-05)"

---

## GCS 배포 명령어

    gcloud storage cp frontend/index.html gs://auto-report-489722-reports/index.html --content-type=text/html --project=auto-report-489722

## 참고 URL

| 항목 | URL |
|---|---|
| 대시보드 | https://storage.googleapis.com/auto-report-489722-reports/index.html |
| data.json | https://storage.googleapis.com/auto-report-489722-reports/dashboard/data.json |
| 금융동향 리포트 | https://storage.googleapis.com/auto-report-489722-reports/reports/finance_report_202603.html |
| 고용동향 리포트 | https://storage.googleapis.com/auto-report-489722-reports/reports/employment_report_202603.html |

---

## 절대 규칙

1. reference_dont_use/ 수정 금지
2. pipeline/ 수정 금지 (export_dashboard_data.py 포함)
3. feature_list.json 은 passes, owner 필드만 수정
4. 브라우저 검증은 GCS URL 직접 사용 (로컬 프리뷰 금지)

# API Dashboard Project — Claude 지침

> 이 문서는 Claude Code가 이 프로젝트 작업 시 반드시 읽어야 하는 컨텍스트입니다.
> 레퍼런스: `reference_dont_use/` 폴더에 이전 작업물 보존 (직접 수정 금지)

---

## 프로젝트 개요

공공데이터 API 및 민간 API를 수집·가공하여 웹 대시보드로 시각화하는 프로젝트.
이전 "AUTO REPORT" 프로젝트를 기반으로 완성도를 높이는 작업.

**GCP 프로젝트**: `auto-report-489722` / 리전: `asia-northeast3` (서울)

---

## 현재 파이프라인 구조

```
[데이터 수집] → [BigQuery 적재] → [R 분석] → [HTML 리포트 렌더링] → [GCS 호스팅]
```

### Cloud Run Jobs
| Job | 스케줄 | 역할 |
|---|---|---|
| `pipeline-job` | 매월 15일 07:00 KST | 데이터 수집·BQ 적재 |
| `report-job` | 매월 15일 08:30 KST | R 분석·리포트 렌더링 |

---

## 파이프라인 현황

### 고용동향 (완성)
- KOSIS (성별/연령/산업/계절조정 취업자, 임금, 구직급여)
- BLS (미국 비농업 취업자, JOLTS)
- Naver DataLab (취업·구직 검색트렌드)
- Google Trends (구직·실업 검색량)

### 금융동향 (진행 중)
- ECOS (기준금리, 국고채, CD, 가계신용, M2, 환율, CPI, PPI) — fetcher 완성
- FRED (미국 연방기금금리, 국채, 실업률 등) — 코드 완성, 미테스트
- KOSIS 금융 (CPI·PPI·주택가격) — 코드 완성, 미테스트

---

## 멀티에이전트 협업 구조

이 프로젝트는 **Claude Code**, **Codex (OpenAI)**, **Antigravity** 세 에이전트가
`feature_list.json`과 `agent-lock.json`을 공유 상태로 협업한다.

### 에이전트별 담당 영역

| 에이전트 | 담당 |
|---|---|
| **Claude Code** (나) | 설계 결정, BigQuery 스키마, R 분석, Secret Manager, harness 관리 |
| **Codex** | `pipeline/finance/` 테스트 코드, boilerplate, PR 생성 |
| **Antigravity** | `frontend/` UI 구현, GCS 배포 결과물 브라우저 검증 |

### 충돌 방지 원칙
- 작업 시작 전 반드시 `agent-lock.json` 확인
- 다른 에이전트가 잠근 파일은 절대 수정하지 않음
- `feature_list.json` 항목은 `passes`와 `owner` 필드만 수정 (삭제·추가 금지)
- PR은 Codex가 생성하고, Claude Code가 리뷰 후 머지 결정

---

## 세션 프로토콜

### 세션 시작 시 반드시 수행 (순서 엄수)

```
1. bash init.sh
2. cat agent-lock.json          # 잠긴 파일 확인
3. cat claude-progress.txt      # 직전 세션 인수인계 확인
4. cat feature_list.json        # passes: false + owner: null 항목 파악
5. git log --oneline -10        # 최근 커밋 흐름 확인
```

`depends_on` 항목이 모두 `passes: true`인 태스크만 선택한다.
선택 후 즉시 해당 항목의 `owner`를 `"claude"`로 변경하고 커밋한다.

```bash
# owner 설정 커밋 예시
git add feature_list.json
git commit -m "[claude] chore: claim feature <id>"
```

### 세션 종료 시 반드시 수행

```
1. 테스트 통과 확인
2. feature_list.json → passes: true (owner는 유지)
3. agent-lock.json → 본인 잠금 항목 제거
4. claude-progress.txt 업데이트 (아래 포맷 준수)
5. git commit -m "[claude] feat/fix: <작업 요약> (<feature id>)"
```

### claude-progress.txt 업데이트 포맷

```
[YYYY-MM-DD Session N]
완료: <feature id> — <한 줄 요약>
  - 주요 변경 파일: <파일 경로>
  - 특이사항: <다음 에이전트가 알아야 할 것>

다음 세션 할 일:
  - <feature id>: <설명>

알려진 이슈:
  - <이슈 내용> (해당 없으면 "없음")
```

---

## Claude Code 전용 규칙

### BigQuery / 데이터 정합성
- `category_key_filter`는 BQ `employment_raw_dedup`의 실제 값과 정확히 일치해야 함
- 스키마 변경 전 반드시 기존 쿼리 영향 범위 확인
- 파티션 키·클러스터링 변경은 `docs/decisions.md`에 기록 후 진행

### R 분석
- `run_id`는 `run_analysis.R` 시작 시 한 번만 생성 후 환경변수로 전달
- R 모듈 수정 시 `report-job` Docker 이미지 재빌드 필요 여부 확인

### Secret Manager (보안)
- `echo` 사용 금지 — BOM/개행문자 오염 방지
- 반드시 Python으로 파일 생성 후 등록:

```bash
python -c "open('key.txt','wb').write(b'실제키값')"
gcloud secrets versions add SECRET_NAME --data-file=key.txt --project=auto-report-489722
del key.txt
```

### Codex PR 리뷰 기준
Codex가 생성한 PR을 머지하기 전 다음을 확인한다:
- [ ] BQ 스키마 정합성 (category_key_filter 등)
- [ ] `.env` 커밋 여부 (있으면 즉시 반려)
- [ ] `reference_dont_use/` 수정 여부 (있으면 즉시 반려)
- [ ] 테스트 실행 결과 첨부 여부

---

## 절대 규칙 (모든 에이전트 공통)

1. `reference_dont_use/` 폴더 — 읽기 전용, 절대 수정 금지
2. `.env` 파일 — 커밋 금지, `.env.example`만 유지
3. `feature_list.json` — `passes`, `owner` 필드만 수정 가능
4. `CLAUDE.md` 자체 — 수정 금지 (변경 필요 시 `docs/decisions.md`에 기록)
5. 잠긴 파일(`agent-lock.json` 참조) — 수정 금지

---

## Claude 할당량 절약 원칙 (최우선)

**Claude의 주간 사용량 낭비 최소화가 최상위 운영 원칙이다.**

### 위임 우선 규칙

`feature_list.json`의 `assigned_to` 필드가 `"antigravity"` 또는 `"codex"`인 태스크는
사용자의 **별도 명시적 지시 없이 Claude가 직접 실행하는 것을 금지**한다.

| 태스크 유형 | 담당 에이전트 | Claude 직접 실행 |
|---|---|---|
| `frontend/` UI·CSS·GCS 배포 검증 | Antigravity | ❌ 금지 |
| `pipeline/finance/` 테스트·boilerplate | Codex | ❌ 금지 |
| BQ 스키마·R 분석·인프라·설계 | Claude | ✅ 허용 |

### 판단 기준

태스크 실행 전 다음을 확인한다:
1. `feature_list.json`에서 해당 태스크의 `assigned_to` 확인
2. Antigravity/Codex 담당이면 → **실행 중단, 사용자에게 보고**
3. 사용자가 "직접 해줘" 또는 "네가 해줘"라고 명시한 경우에만 실행

### 대신 해야 할 일

Antigravity/Codex 담당 태스크가 필요할 때 Claude가 할 일:
- 해당 에이전트의 지침 파일(ANTIGRAVITY.md / AGENTS.md) 작성·업데이트
- 실행에 필요한 컨텍스트(API 스펙, 데이터 구조, 배포 명령어 등) 정리해서 파일에 기록
- 사용자에게 해당 에이전트에 위임할 것을 안내

---

## 디렉토리 구조

```
Project - Api Dashboard/
├── CLAUDE.md                   # Claude Code 지침 (이 파일)
├── AGENTS.md                   # Codex 지침
├── README.md
├── claude-progress.txt         # 세션 간 인수인계 (사람이 읽는 요약)
├── feature_list.json           # 기계가 읽는 진행 추적 (에이전트 공유)
├── agent-lock.json             # 파일 잠금 (에이전트 공유)
├── init.sh                     # 환경 초기화 스크립트
├── .env.example
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── api-inventory.md
│   └── decisions.md            # 설계 결정 이력
├── frontend/                   # Antigravity 담당
├── pipeline/
│   ├── employment/             # 완성
│   └── finance/                # Claude + Codex 담당
└── infra/
```

---

## 공유 상태 파일 스펙

### feature_list.json 구조

```json
[
  {
    "id": "fin-01",
    "module": "finance",
    "description": "FRED fetcher 테스트 및 BQ 적재 검증",
    "owner": null,
    "passes": false,
    "assigned_to": "codex",
    "depends_on": []
  }
]
```

- `owner`: 현재 작업 중인 에이전트 ID (`"claude"` / `"codex"` / `"antigravity"` / `null`)
- `assigned_to`: 이 태스크를 담당하도록 권고된 에이전트 (참고용, 강제 아님)
- `passes`: 완료 여부 — 테스트 통과 시에만 `true`로 변경

### agent-lock.json 구조

```json
{
  "locked_files": [
    {
      "path": "pipeline/finance/fred_fetcher.py",
      "locked_by": "codex",
      "since": "2026-04-16T10:30:00+09:00",
      "task_id": "fin-01"
    }
  ]
}
```

---

## 자주 쓰는 명령어

### Cloud Run Job 수동 실행
```bash
gcloud run jobs execute pipeline-job --region asia-northeast3 --project auto-report-489722 --wait
gcloud run jobs execute report-job   --region asia-northeast3 --project auto-report-489722 --wait
```

### GCS 파일 관리
```bash
gcloud storage ls gs://auto-report-489722-reports/reports/
gcloud storage cp index.html gs://auto-report-489722-reports/index.html --content-type="text/html"
```

### feature_list.json owner 설정 원라이너
```bash
# Python으로 안전하게 owner 업데이트
python -c "
import json
with open('feature_list.json') as f: items = json.load(f)
for item in items:
    if item['id'] == 'fin-01' and item['owner'] is None:
        item['owner'] = 'claude'
with open('feature_list.json', 'w') as f: json.dump(items, f, ensure_ascii=False, indent=2)
"
```

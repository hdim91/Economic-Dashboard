# API Dashboard Project — Codex 지침

> 이 문서는 Codex(OpenAI)가 이 프로젝트 작업 시 반드시 읽어야 하는 컨텍스트입니다.
> 프로젝트 전체 구조는 README.md 참고.

---

## 프로젝트 개요

공공데이터 API 및 민간 API를 수집·가공하여 웹 대시보드로 시각화하는 프로젝트.

**GCP 프로젝트**: `auto-report-489722` / 리전: `asia-northeast3` (서울)

---

## Codex 담당 영역

Codex는 다음 영역만 담당한다. 범위 밖 파일은 수정하지 않는다.

| 담당 | 경로 |
|---|---|
| 금융동향 fetcher 테스트 코드 | `pipeline/finance/tests/` |
| 금융동향 fetcher boilerplate | `pipeline/finance/` |
| 환경변수 템플릿 동기화 | `.env.example` |

### 절대 건드리지 않는 파일/폴더
- `reference_dont_use/` — 읽기 전용 참고 자료
- `pipeline/employment/` — 완성된 모듈, 수정 금지
- `infra/` — Claude Code 담당
- `frontend/` — Antigravity 담당
- `CLAUDE.md`, `AGENTS.md` 자체
- `run_analysis.R` 및 R 관련 파일 전체

---

## 세션 프로토콜

### 세션 시작 시 반드시 수행 (순서 엄수)

```
1. cat agent-lock.json          # 잠긴 파일 확인
2. cat claude-progress.txt      # 직전 세션 인수인계 확인 (파일명: claude-progress.txt)
3. cat feature_list.json        # assigned_to: "codex" + passes: false + owner: null 항목 파악
4. git log --oneline -10
```

`depends_on` 항목이 모두 `passes: true`인 태스크만 선택한다.
선택 후 즉시 해당 항목의 `owner`를 `"codex"`로 변경하고 커밋한다.

```bash
python -c "
import json
with open('feature_list.json') as f: items = json.load(f)
for item in items:
    if item['id'] == 'TARGET_ID' and item['owner'] is None:
        item['owner'] = 'codex'
with open('feature_list.json', 'w') as f: json.dump(items, f, ensure_ascii=False, indent=2)
"
git add feature_list.json
git commit -m "[codex] chore: claim feature <id>"
```

### 세션 종료 시 반드시 수행

```
1. 테스트 실행 및 결과 확인
2. feature_list.json → passes: true (테스트 통과 시에만)
3. agent-lock.json → 본인 잠금 항목 제거
4. PR 생성 (직접 main 머지 금지)
5. git commit -m "[codex] feat/test: <작업 요약> (<feature id>)"
```

---

## 코드 작성 규칙

### 테스트 코드 패턴
금융동향 fetcher 테스트는 `pipeline/finance/tests/` 폴더에 작성한다.
`pipeline/employment/` 각 fetcher의 구조(try/except, 로깅, 반환값 검증)를 참고 패턴으로 사용한다.
`pipeline/employment/tests/` 폴더는 현재 미존재 — 참고 시 직접 fetcher 코드를 읽을 것.

### 환경변수
- `.env` 파일을 직접 생성하거나 커밋하지 않는다
- 새로운 API 키가 필요한 경우 `.env.example`에 키 이름만 추가한다

```bash
# .env.example 추가 예시 (값은 절대 기입 금지)
FRED_API_KEY=your_fred_api_key_here
```

### PR 생성 규칙
- 브랜치명: `codex/<feature-id>-<간단한-설명>`
- PR 제목: `[codex] <feature id>: <작업 요약>`
- PR 본문에 반드시 포함:
  - 테스트 실행 결과 (통과/실패 로그)
  - 변경된 파일 목록
  - 리뷰어: Claude Code (담당자에게 리뷰 요청)

---

## 절대 규칙

1. `reference_dont_use/` — 읽기 전용, 절대 수정 금지
2. `.env` — 커밋 금지
3. `feature_list.json` — `passes`, `owner` 필드만 수정
4. 잠긴 파일(`agent-lock.json` 참조) — 수정 금지
5. main 브랜치 직접 머지 금지 — PR → Claude Code 리뷰 후 머지
6. Secret Manager 작업 금지 — Claude Code 전담

---

## 현재 태스크: fin-10 — pipeline-job E2E 검증 스크립트

### 개요

Cloud Run pipeline-job을 실행한 뒤, BigQuery `finance_stats` 데이터셋의 테이블에
데이터가 정상 적재됐는지 자동으로 확인하는 E2E 검증 스크립트를 작성한다.

- **GCP 프로젝트**: `auto-report-489722`
- **Cloud Run Job**: `pipeline-job` / 리전: `asia-northeast3`
- **BigQuery 데이터셋**: `finance_stats`
- **확인 대상 테이블**: `finance_raw` (원본 적재 테이블)
- **확인 대상 뷰**: `finance_raw_dedup` (중복 제거 뷰)

### 작성할 파일

    pipeline/finance/tests/e2e_pipeline_verify.py

### 스크립트 동작 순서

1. `gcloud run jobs execute pipeline-job --region asia-northeast3 --project auto-report-489722 --wait` 실행
2. 실행 완료 후 BigQuery 쿼리로 아래 항목 확인:
   - `finance_stats.finance_raw` 전체 row count
   - `finance_stats.finance_raw_dedup` 전체 row count
   - 최신 `period` 값 (각 variable_name 별 MAX period)
   - `ingested_at` 최신값 (방금 실행된 run_id의 데이터 포함 여부)
3. 결과를 콘솔에 출력하고, row count가 0이면 non-zero exit code 반환

### 스크립트 템플릿

아래 구조를 따라 작성한다:

    #!/usr/bin/env python3
    """fin-10: pipeline-job E2E 검증 스크립트"""

    import subprocess, sys
    from google.cloud import bigquery

    PROJECT = "auto-report-489722"
    REGION  = "asia-northeast3"
    JOB     = "pipeline-job"
    DATASET = "finance_stats"

    def run_pipeline():
        print(f"[1/3] Cloud Run Job '{JOB}' 실행 중...")
        result = subprocess.run(
            ["gcloud", "run", "jobs", "execute", JOB,
             "--region", REGION, "--project", PROJECT, "--wait"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("FAIL:", result.stderr)
            sys.exit(1)
        print("    → 완료")

    def verify_bq():
        print("[2/3] BigQuery 적재 검증...")
        client = bigquery.Client(project=PROJECT)

        # row count 확인
        for table in ["finance_raw", "finance_raw_dedup"]:
            row = next(client.query(
                f"SELECT COUNT(*) AS cnt FROM `{PROJECT}.{DATASET}.{table}`"
            ).result())
            cnt = row.cnt
            status = "OK" if cnt > 0 else "FAIL (0건)"
            print(f"    {table}: {cnt:,} rows — {status}")
            if cnt == 0:
                sys.exit(1)

        # 최신 period 확인
        rows = client.query(f"""
            SELECT variable_name, MAX(period) AS latest_period, COUNT(*) AS cnt
            FROM `{PROJECT}.{DATASET}.finance_raw_dedup`
            GROUP BY variable_name
            ORDER BY variable_name
        """).result()

        print("[3/3] 지표별 최신 period:")
        for r in rows:
            print(f"    {r.variable_name:<30} latest={r.latest_period}  rows={r.cnt}")

    if __name__ == "__main__":
        run_pipeline()
        verify_bq()
        print("\n✅ E2E 검증 완료")

### 실행 방법

    python pipeline/finance/tests/e2e_pipeline_verify.py

### 의존 패키지

`google-cloud-bigquery` 가 설치돼 있어야 한다.
Cloud Run Job 컨테이너 환경이 아닌 **로컬 환경**에서 실행한다.
ADC(Application Default Credentials) 필요:

    gcloud auth application-default login

### 완료 처리

1. feature_list.json fin-10 passes: true 변경:

    python -c "
    import json
    with open('feature_list.json', encoding='utf-8') as f: items = json.load(f)
    for item in items:
        if item['id'] == 'fin-10':
            item['passes'] = True
            item['owner'] = 'codex'
    with open('feature_list.json', 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    "

2. agent-lock.json 본인 항목 제거:

    python -c "
    import json
    with open('agent-lock.json', encoding='utf-8') as f: lock = json.load(f)
    lock['locked_files'] = [x for x in lock['locked_files'] if x['locked_by'] != 'codex']
    with open('agent-lock.json', 'w', encoding='utf-8') as f:
        json.dump(lock, f, ensure_ascii=False, indent=2)
    "

3. 커밋 및 PR 생성:

    git add pipeline/finance/tests/e2e_pipeline_verify.py feature_list.json agent-lock.json
    git commit -m "[codex] feat: pipeline-job E2E 검증 스크립트 작성 (fin-10)"
    git push origin codex/fin-10-e2e-verify
    gh pr create --title "[codex] fin-10: pipeline-job E2E 검증 스크립트" --body "fin-10 완료. e2e_pipeline_verify.py 작성, 실행 결과 첨부."

---

## 완료된 태스크 (참고)

| feature id | 내용 | 상태 |
|---|---|---|
| `fin-01` | FRED fetcher 테스트 작성 및 BQ 적재 검증 | ✅ 완료 |
| `fin-02` | KOSIS 금융 fetcher 테스트 작성 및 BQ 적재 검증 | ✅ 완료 |
| `fin-03` | ECOS fetcher 통합 테스트 | ✅ 완료 |
| `fin-09` | finance_feature_store.py 작성 | ✅ 완료 |

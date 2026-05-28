# 설계 결정 이력

---

## [2026-04-17] emp-01: Google Trends Cloud Run IP 차단 문제

**결정자**: Claude Code
**관련 feature**: emp-01

### 문제
`pipeline-job` (Cloud Run) 실행 시 `google_trends_count: 0` 반환.
예외 없이 빈 DataFrame 반환 → pytrends가 조용히 실패하는 패턴.

### 원인 분석
Google이 GCP 데이터센터 IP 대역(Cloud Run 포함)을 자동화 요청으로 인식하여 차단.
차단 시 pytrends는 HTTP 예외 대신 빈 DataFrame을 반환하므로 코드 레벨에서 감지 어려움.

부수 문제: 5년 기간(period) 설정 시 pytrends가 주별(weekly) 데이터를 반환하는데,
기존 코드는 월별 집계 없이 바로 `YYYY-MM` 변환 → 같은 달에 중복 레코드 발생 가능.

### 조치 (google_trends_fetcher.py 개선)
1. `TrendReq`에 브라우저 User-Agent 헤더 추가 → 일부 환경에서 차단 완화 가능
2. `retries=3, backoff_factor=0.5` 설정 → 간헐적 rate limit 대응
3. 주별→월별 집계 로직 추가: `df.index.to_period("M")` + `groupby().mean()`
4. 상세 로깅 추가: `shape`, `columns`, 원인 메시지 기록

### 근본적 해결 방안 (우선순위 순)
| 방법 | 비용 | 복잡도 | 효과 |
|---|---|---|---|
| Cloud NAT + 고정 IP + Google 허용 | 낮음 | 높음 | 높음 |
| 주거용 프록시 (BrightData 등) | 중간 | 중간 | 높음 |
| SerpAPI / DataForSEO Google Trends | 중간 | 낮음 | 높음 |
| **Naver DataLab으로 대체** (현재 권장) | 없음 | 낮음 | 중간 |

### 현재 결정
코드 개선 후 배포 테스트. IP 차단이 확인되면 Naver DataLab을 국내 검색트렌드
주 지표로 확정하고 Google Trends는 옵션으로 유지.

---

## [2026-04-17] emp-02: ECOS M2·환율 item_code 확정

**결정자**: Claude Code
**관련 feature**: emp-02

### 환율 (kr_usd_rate) — 변경

| 항목 | 이전 | 이후 |
|---|---|---|
| stat_code | 731Y003 | **731Y001** |
| cycle | M | **D** |
| item_code1 | 0000003 | **0000001** |
| 집계 | 없음 | **needs_agg_m=True (일별→월평균)** |

**근거**:
- `731Y003`은 일별(D) OHLC 데이터만 존재. M 주기 조회 시 INFO-200.
- `731Y001/D/0000001` (원/달러 매매기준율) → 데이터 수신 확인 (1289.4원/2024-01-02)
- `_aggregate_daily_to_monthly()` 함수 추가하여 월별 평균 자동 산출

### M2 (kr_m2) — 유지

| 항목 | 값 |
|---|---|
| stat_code | 101Y004 |
| cycle | M |
| item_code1 | BBHA00 |

**근거**:
- `StatisticItemList`에서 BBHA00 (M2 평잔 원계열, M 주기) 존재 확인
- 데모 API 키(search_ecos_stats.py)로 조회 시 INFO-200 → 키 권한 제한으로 판단
- 운영 키(GCP Secret Manager ECOS_API_KEY)로 재확인 예정 (fin-03에서)
- 코드 유지, 다음 파이프라인 실행 시 실제 데이터 수신 여부 검증

### 코드 변경 내역 (ecos_fetcher.py)
1. `EcosDataset`에 `needs_agg_m: bool = False` 필드 추가
2. `_to_ecos_period()`: D 주기 지원 (is_start 파라미터, YYYYMMDD 포맷)
3. `_from_ecos_period()`: D 주기 변환 추가 ("20240115" → "2024-01")
4. `_aggregate_daily_to_monthly()` 함수 신규 추가
5. `fetch_ecos_dataset()`: needs_agg_m=True 시 일별→월별 집계 호출

---

## [2026-04-17] 멀티에이전트 협업 구조 확립

**결정자**: Claude Code

### 결정 내용
- `feature_list.json`: 에이전트 간 공유 태스크 추적 파일
- `agent-lock.json`: 파일 단위 잠금으로 충돌 방지
- `claude-progress.txt`: 세션 간 인수인계 (Claude 전용)
- `AGENTS.md`: Codex 지침 / `ANTIGRAVITY.md`: Antigravity 지침

### 인코딩 주의사항
`feature_list.json`은 UTF-8로 저장되어야 함.
Windows 환경에서 Python 스크립트로 수정 시 `encoding='utf-8'` 명시 필수.
인코딩 깨짐 발생 시 Claude Code가 직접 재작성.

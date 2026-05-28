# API 목록 및 코드표

## 고용동향

### KOSIS (국가통계포털)
- **키**: Secret Manager `KOSIS_API_KEY` (v2 — v1 BOM 오염으로 사용 금지)
- **Base URL**: `https://kosis.kr/openapi/`

| variable_name | 내용 | 비고 |
|---|---|---|
| seasonal_employed | 성별/연령/산업별 취업자 (계절조정) | kosis_emp |
| raw_wage_and_hours_by_industry_size | 임금·근로시간 | kosis_wage |
| raw_job_seeker_trend | 구직급여 신청·지급 | kosis_benefit |

### BLS (미국 노동통계국)
- **키**: Secret Manager `BLS_API_KEY`
- **Base URL**: `https://api.bls.gov/publicAPI/v2/`

| Series ID | 내용 |
|---|---|
| CES0000000001 | 비농업 취업자 (총계) |
| JTS00000000JOR | JOLTS 채용률 |

### Naver DataLab
- **키**: Secret Manager `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`

### Google Trends
- 키 불필요 (pytrends 라이브러리)
- ⚠️ 현재 0건 수집 이슈 있음

---

## 금융동향

### ECOS (한국은행 경제통계시스템)
- **키**: Secret Manager `ECOS_API_KEY`
- **Base URL**: `https://ecos.bok.or.kr/api/`
- **명세서**: `reference_dont_use/ecos/API개발명세서_통계세부항목목록.xls`

| variable_name | stat_code | cycle | item_code | 상태 |
|---|---|---|---|---|
| kr_base_rate | 722Y001 | M | 0101000 | ✅ 확정 |
| kr_govbond_3y | 721Y001 | M | 5020000 | ✅ 확정 |
| kr_govbond_10y | 721Y001 | M | 5050000 | ✅ 확정 |
| kr_cd_91d | 721Y001 | M | 2010000 | ✅ 확정 |
| kr_household_credit | 151Y001 | Q | 1000000 | ✅ 확정 |
| kr_household_loan | 151Y001 | Q | 1100000 | ✅ 확정 |
| kr_m2 | 101Y004 | M | BBHA00 | ⚠️ item_code 재확인 필요 |
| kr_usd_rate | 731Y003 | M | 0000003 | ⚠️ item_code 재확인 필요 |
| kr_cpi | 901Y009 | M | 0 | ✅ 확정 |
| kr_ppi | 404Y014 | M | *AA | ✅ 확정 |

### FRED (미국 연방준비은행)
- **키**: Secret Manager `FRED_API_KEY`
- **Base URL**: `https://api.stlouisfed.org/fred/`
- **상태**: 코드 완성, 테스트 미완료

---

## Feature Store category_key_filter 확정값

> BQ `employment_raw_dedup`의 실제 `category_key`와 `=` 완전일치 필요

| feature_name | source | variable_name_filter | category_key_filter | adjustment_type_filter |
|---|---|---|---|---|
| kr_employed_total_sa_value | kosis_emp | seasonal_employed | 00\|T30 | seasonal |
| kr_employed_male_total_value | kosis_emp | seasonal_employed | 10\|T30 | seasonal |
| kr_employed_female_total_value | kosis_emp | seasonal_employed | 20\|T30 | seasonal |
| kr_emprate_total_value | kosis_emp | seasonal_employed | 00\|T90 | seasonal |
| kr_jobseeker_new_value | kosis_benefit | raw_job_seeker_trend | DATA\|T001 | raw |
| kr_jobseeker_ongoing_value | kosis_benefit | raw_job_seeker_trend | DATA\|T002 | raw |
| kr_wage_total_value | kosis_wage | raw_wage_and_hours_by_industry_size | 190326INDUSTRY_10S0\|size01\|13103110311MD_12 | raw |
| kr_hours_total_value | kosis_wage | raw_wage_and_hours_by_industry_size | 190326INDUSTRY_10S0\|size01\|13103110311MD_15 | raw |

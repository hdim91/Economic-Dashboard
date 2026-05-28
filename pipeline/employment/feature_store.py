"""
feature_store.py
─────────────────────────────────────────────────────────────────────────────
FEATURE STORE — BigQuery View DDL 생성 및 관리

━━━ Mart vs Feature Store ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Mart         : 소스별 정리 · 리포팅용
  Feature Store: cross-source 조합 · 분석/모델 직접 투입용

━━━ 구성 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  View 이름                설명
  ──────────────────────   ────────────────────────────────────────────────
  fs_feature_catalog       피처 카탈로그 — feature_name 정의 및 메타데이터
  fs_long                  Long 형태 피처 테이블 (period, feature_name, value)
  fs_wide                  Wide 형태 피처 테이블 (period 1행 × 피처 ~50개 컬럼)

━━━ 피처 명명 규칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  {소스그룹}_{지표}_{분류}_{metric}

  예)
    kr_employed_total_sa_value        한국 전체 취업자(계절조정) 원값
    kr_employed_total_sa_mom_pct      한국 전체 취업자(계절조정) 전월비
    kr_employed_total_sa_yoy_pct      한국 전체 취업자(계절조정) 전년비
    kr_employed_mfg_raw_value         한국 제조업 취업자(원계열) 원값
    us_nonfarm_total_sa_value         미국 비농업 취업자(계절조정) 원값
    us_job_openings_total_sa_value    미국 채용공고(계절조정) 원값
    kr_search_employment_f_all_value  네이버 취업 검색(여성/전체연령) 원값
    kr_gtrend_employment_value        구글 취업 검색 트렌드 원값

━━━ 피처 그룹 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  그룹                피처 수   소스
  ─────────────────   ───────   ───────────────────────────────────────────
  KR 고용 핵심            12    kosis_emp (계절조정 전체+산업별)
  KR 고용 연령/성별        6    kosis_emp (원계열 성×연령)
  KR 임금·근로시간         6    kosis_wage
  KR 구직급여              4    kosis_benefit
  US 비농업 취업자         6    bls CES
  US 노동시장 지표         8    bls JOLTS + CPS
  KR 검색 트렌드           6    naver_datalab (키워드별 합산)
  KR 구인공고              3    naver_jobpost
  KR 뉴스 언급량           3    naver_news
  KR 구글 트렌드           4    google_trends
  ─────────────────   ───────
  총계                    58
"""

import logging

from google.cloud import bigquery
from config import GCP_PROJECT_ID, BQ_DATASET

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 참조 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DEDUP = f"`{GCP_PROJECT_ID}.{BQ_DATASET}.employment_raw_dedup`"


def _view(name: str) -> str:
    return f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{name}`"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 피처 카탈로그 DDL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# fs_long에서 각 feature_name이 어느 소스/변수에서 왔는지 추적하기 위한 메타
# CREATE OR REPLACE VIEW로 관리 — 피처 추가 시 이 SQL만 수정
FS_CATALOG_SQL = f"""
CREATE OR REPLACE VIEW {_view('fs_feature_catalog')} AS
-- 피처 카탈로그: fs_long/fs_wide 에 포함된 모든 피처의 메타데이터
-- feature_name 기준으로 fs_long과 JOIN 가능
SELECT
  feature_name,
  feature_group,
  source,
  variable_name_filter,
  category_key_filter,
  adjustment_type_filter,
  metric_filter,
  unit,
  description_ko
FROM UNNEST([
  -- ── KR 고용 핵심 (계절조정) ─────────────────────────────────────────
  STRUCT('kr_emprate_total_value'           AS feature_name, 'KR고용핵심' AS feature_group, 'kosis_emp' AS source, 'seasonal_employed'              AS variable_name_filter, '00|T90' AS category_key_filter, 'seasonal'    AS adjustment_type_filter, 'value'   AS metric_filter, '%'     AS unit, '한국 전체 고용률 (계절조정)'            AS description_ko),
  STRUCT('kr_emprate_male_value'            AS feature_name, 'KR고용핵심' AS feature_group, 'kosis_emp' AS source, 'seasonal_employed'              AS variable_name_filter, '10|T90' AS category_key_filter, 'seasonal'    AS adjustment_type_filter, 'value'   AS metric_filter, '%'     AS unit, '한국 남성 고용률 (계절조정)'            AS description_ko),
  STRUCT('kr_emprate_female_value'          AS feature_name, 'KR고용핵심' AS feature_group, 'kosis_emp' AS source, 'seasonal_employed'              AS variable_name_filter, '20|T90' AS category_key_filter, 'seasonal'    AS adjustment_type_filter, 'value'   AS metric_filter, '%'     AS unit, '한국 여성 고용률 (계절조정)'            AS description_ko),
  STRUCT('kr_employed_total_sa_value'       AS feature_name, 'KR고용핵심' AS feature_group, 'kosis_emp' AS source, 'seasonal_employed'              AS variable_name_filter, '00|T30' AS category_key_filter, 'seasonal'    AS adjustment_type_filter, 'value'   AS metric_filter, '만명'  AS unit, '한국 전체 취업자 수 (계절조정)'        AS description_ko),
  STRUCT('kr_employed_total_sa_mom_pct'     , 'KR고용핵심', 'kosis_emp', 'seasonal_employed',              '00|T30','derived_pct','mom_pct', '%',    '한국 전체 취업자 수 (계절조정) 전월비'),
  STRUCT('kr_employed_total_sa_yoy_pct'     , 'KR고용핵심', 'kosis_emp', 'seasonal_employed',              '00|T30','derived_pct','yoy_pct', '%',    '한국 전체 취업자 수 (계절조정) 전년비'),
  STRUCT('kr_employed_total_sa_yoy'         , 'KR고용핵심', 'kosis_emp', 'seasonal_employed',              '00|T30','derived_chg','yoy',     '만명', '한국 전체 취업자 수 (계절조정) 전년동월차'),
  STRUCT('kr_employed_mfg_sa_value'         , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '10|T30','seasonal',    'value',   '만명', '한국 제조업 취업자 수 (계절조정)'),
  STRUCT('kr_employed_mfg_sa_yoy_pct'       , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '10|T30','derived_pct','yoy_pct', '%',    '한국 제조업 취업자 수 (계절조정) 전년비'),
  STRUCT('kr_employed_mfg_sa_yoy'           , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '10|T30','derived_chg','yoy',     '만명', '한국 제조업 취업자 수 (계절조정) 전년동월차'),
  STRUCT('kr_employed_service_sa_value'     , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '45|T30','seasonal',    'value',   '만명', '한국 서비스업 취업자 수 (계절조정)'),
  STRUCT('kr_employed_service_sa_yoy_pct'   , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '45|T30','derived_pct','yoy_pct', '%',    '한국 서비스업 취업자 수 (계절조정) 전년비'),
  STRUCT('kr_employed_service_sa_yoy'       , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '45|T30','derived_chg','yoy',     '만명', '한국 서비스업 취업자 수 (계절조정) 전년동월차'),
  STRUCT('kr_employed_mfg_raw_value'        , 'KR고용핵심', 'kosis_emp', 'raw_employed_by_industry',       '10|T30','raw',         'value',   '만명', '한국 제조업 취업자 수 (원계열)'),
  STRUCT('kr_employed_mfg_raw_yoy_pct'      , 'KR고용핵심', 'kosis_emp', 'raw_employed_by_industry',       '10|T30','derived_pct','yoy_pct', '%',    '한국 제조업 취업자 수 (원계열) 전년비'),
  STRUCT('kr_employed_mfg_raw_yoy'          , 'KR고용핵심', 'kosis_emp', 'raw_employed_by_industry',       '10|T30','derived_chg','yoy',     '만명', '한국 제조업 취업자 수 (원계열) 전년동월차'),
  STRUCT('kr_employed_construction_sa_value', 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '41|T30','seasonal',    'value',   '만명', '한국 건설업 취업자 수 (계절조정)'),
  STRUCT('kr_employed_it_sa_value'          , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '58|T30','seasonal',    'value',   '만명', '한국 정보통신업 취업자 수 (계절조정)'),
  STRUCT('kr_employed_finance_sa_value'     , 'KR고용핵심', 'kosis_emp', 'seasonal_employed_by_industry',  '64|T30','seasonal',    'value',   '만명', '한국 금융보험업 취업자 수 (계절조정)'),
  -- ── KR 고용 연령/성별 (원계열) ─────────────────────────────────────
  STRUCT('kr_employed_male_total_value'     , 'KR고용연령성별', 'kosis_emp', 'seasonal_employed',       '10|T30',  'seasonal', 'value', '만명', '한국 남성 전체 취업자 수'),
  STRUCT('kr_employed_female_total_value'   , 'KR고용연령성별', 'kosis_emp', 'seasonal_employed',       '20|T30',  'seasonal', 'value', '만명', '한국 여성 전체 취업자 수'),
  STRUCT('kr_employed_youth_value'          , 'KR고용연령성별', 'kosis_emp', 'raw_employed_by_sex_age', '0|75|T30','raw', 'value', '만명', '한국 청년층(15~29세) 취업자 수'),
  STRUCT('kr_employed_youth_yoy_pct'        , 'KR고용연령성별', 'kosis_emp', 'raw_employed_by_sex_age', '0|75|T30','derived_pct','yoy_pct', '%', '한국 청년층 취업자 수 전년비'),
  STRUCT('kr_employed_youth_yoy'            , 'KR고용연령성별', 'kosis_emp', 'raw_employed_by_sex_age', '0|75|T30','derived_chg','yoy',    '만명', '한국 청년층 취업자 수 전년동월차'),
  STRUCT('kr_employed_senior_value'         , 'KR고용연령성별', 'kosis_emp', 'raw_employed_by_sex_age', '0|60|T30','raw', 'value', '만명', '한국 고령층(60세 이상) 취업자 수'),
  STRUCT('kr_employed_prime_value'          , 'KR고용연령성별', 'kosis_emp', 'raw_employed_by_sex_age', '0|40|T30','raw', 'value', '만명', '한국 핵심노동연령(30~54세) 취업자 수'),
  -- ── KR 임금·근로시간 ────────────────────────────────────────────────
  STRUCT('kr_wage_total_value'              , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10S0|size01|13103110311MD_12', 'raw', 'value', '원',   '한국 월평균 임금 (전체 산업)'),
  STRUCT('kr_wage_total_yoy_pct'            , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10S0|size01|13103110311MD_12', 'derived_pct', 'yoy_pct', '%', '한국 월평균 임금 전년비'),
  STRUCT('kr_wage_total_yoy'                , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10S0|size01|13103110311MD_12', 'derived_chg', 'yoy',     '원',   '한국 월평균 임금 전년동월차'),
  STRUCT('kr_wage_mfg_value'               , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10SD|size01|13103110311MD_12', 'raw', 'value', '원',   '한국 제조업 월평균 임금'),
  STRUCT('kr_hours_total_value'            , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10S0|size01|13103110311MD_15', 'raw', 'value', '시간', '한국 월평균 근로시간 (전체 산업)'),
  STRUCT('kr_hours_total_yoy_pct'          , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10S0|size01|13103110311MD_15', 'derived_pct', 'yoy_pct', '%', '한국 월평균 근로시간 전년비'),
  STRUCT('kr_hours_mfg_value'             , 'KR임금근로', 'kosis_wage', 'raw_wage_and_hours_by_industry_size', '190326INDUSTRY_10SD|size01|13103110311MD_15', 'raw', 'value', '시간', '한국 제조업 월평균 근로시간'),
  -- ── KR 구직급여 ─────────────────────────────────────────────────────
  STRUCT('kr_jobseeker_new_value'           , 'KR구직급여', 'kosis_benefit', 'raw_job_seeker_trend', 'DATA|T001', 'raw', 'value',   '명',  '구직급여 신규 신청자 수'),
  STRUCT('kr_jobseeker_new_yoy_pct'         , 'KR구직급여', 'kosis_benefit', 'raw_job_seeker_trend', 'DATA|T001', 'derived_pct', 'yoy_pct', '%',   '구직급여 신규 신청자 수 전년비'),
  STRUCT('kr_jobseeker_new_yoy'             , 'KR구직급여', 'kosis_benefit', 'raw_job_seeker_trend', 'DATA|T001', 'derived_chg', 'yoy',     '명',   '구직급여 신규 신청자 수 전년동월차'),
  STRUCT('kr_jobseeker_ongoing_value'       , 'KR구직급여', 'kosis_benefit', 'raw_job_seeker_trend', 'DATA|T002', 'raw', 'value',   '명',  '구직급여 지속 수급자 수'),
  STRUCT('kr_jobseeker_benefit_amount_value', 'KR구직급여', 'kosis_benefit', 'raw_job_seeker_trend', 'DATA|T004', 'raw', 'value',   '백만원','구직급여 지급액'),
  -- ── US 비농업 취업자 (BLS CES) ──────────────────────────────────────
  STRUCT('us_nonfarm_total_sa_value'        , 'US고용', 'bls', 'seasonal_us_nonfarm_employment',              'total',          'seasonal', 'value',   '천명', '미국 비농업 취업자 수 (계절조정)'),
  STRUCT('us_nonfarm_total_sa_mom_pct'      , 'US고용', 'bls', 'seasonal_us_nonfarm_employment',              'total',          'derived_pct', 'mom_pct', '%',    '미국 비농업 취업자 수 전월비'),
  STRUCT('us_nonfarm_total_sa_yoy_pct'      , 'US고용', 'bls', 'seasonal_us_nonfarm_employment',              'total',          'derived_pct', 'yoy_pct', '%',    '미국 비농업 취업자 수 전년비'),
  STRUCT('us_nonfarm_total_sa_yoy'          , 'US고용', 'bls', 'seasonal_us_nonfarm_employment',              'total',          'derived_chg', 'yoy',     '천명', '미국 비농업 취업자 수 전년동월차'),
  STRUCT('us_nonfarm_mfg_sa_value'          , 'US고용', 'bls', 'seasonal_us_nonfarm_employment_by_industry',  'manufacturing',  'seasonal', 'value',   '천명', '미국 제조업 취업자 수 (계절조정)'),
  STRUCT('us_nonfarm_professional_sa_value' , 'US고용', 'bls', 'seasonal_us_nonfarm_employment_by_industry',  'professional',   'seasonal', 'value',   '천명', '미국 전문직 취업자 수 (계절조정)'),
  STRUCT('us_nonfarm_leisure_sa_value'      , 'US고용', 'bls', 'seasonal_us_nonfarm_employment_by_industry',  'leisure',        'seasonal', 'value',   '천명', '미국 레저/접객업 취업자 수 (계절조정)'),
  -- ── US 노동시장 지표 (BLS JOLTS + CES 임금) ─────────────────────────
  STRUCT('us_job_openings_sa_value'         , 'US노동지표', 'bls', 'seasonal_us_job_openings',       'total',         'seasonal', 'value',   '천건', '미국 채용공고 수 (계절조정, JOLTS)'),
  STRUCT('us_job_openings_sa_yoy_pct'       , 'US노동지표', 'bls', 'seasonal_us_job_openings',       'total',         'derived_pct', 'yoy_pct', '%',    '미국 채용공고 수 전년비'),
  STRUCT('us_quits_sa_value'                , 'US노동지표', 'bls', 'seasonal_us_quits',              'total',         'seasonal', 'value',   '천명', '미국 자발적 이직자 수 (계절조정, JOLTS)'),
  STRUCT('us_layoffs_sa_value'              , 'US노동지표', 'bls', 'seasonal_us_layoffs_discharges', 'total',         'seasonal', 'value',   '천명', '미국 정리해고 수 (계절조정, JOLTS)'),
  STRUCT('us_hourly_wage_sa_value'          , 'US노동지표', 'bls', 'seasonal_us_hourly_wage',        'total_private', 'seasonal', 'value',   'USD',  '미국 시간당 평균 임금 (계절조정)'),
  STRUCT('us_hourly_wage_sa_yoy_pct'        , 'US노동지표', 'bls', 'seasonal_us_hourly_wage',        'total_private', 'derived_pct', 'yoy_pct', '%',    '미국 시간당 평균 임금 전년비'),
  STRUCT('us_weekly_hours_sa_value'         , 'US노동지표', 'bls', 'seasonal_us_weekly_hours',       'total_private', 'seasonal', 'value',   '시간', '미국 주당 평균 근로시간 (계절조정)'),
  STRUCT('us_unemp_rate_youth_value'        , 'US노동지표', 'bls', 'raw_us_unemployment_rate_by_age','16_19',         'raw',      'value',   '%',    '미국 청년층(16-19세) 실업률 (원계열)'),
  -- ── KR 검색 트렌드 (Naver DataLab) ──────────────────────────────────
  STRUCT('kr_search_employment_value'       , 'KR검색트렌드', 'naver_datalab', 'search_trend_employment_all_all', 'all|all', 'index', 'value', '지수', '네이버 취업 키워드 검색 지수 (전체)'),
  STRUCT('kr_search_jobchange_value'        , 'KR검색트렌드', 'naver_datalab', 'search_trend_jobchange_all_all',  'all|all', 'index', 'value', '지수', '네이버 이직 키워드 검색 지수 (전체)'),
  STRUCT('kr_search_hiring_value'           , 'KR검색트렌드', 'naver_datalab', 'search_trend_hiring_all_all',     'all|all', 'index', 'value', '지수', '네이버 채용 키워드 검색 지수 (전체)'),
  STRUCT('kr_search_employment_youth_value' , 'KR검색트렌드', 'naver_datalab', 'search_trend_employment_all_20s', 'all|20s', 'index', 'value', '지수', '네이버 취업 키워드 검색 지수 (20대)'),
  STRUCT('kr_search_employment_f_value'     , 'KR검색트렌드', 'naver_datalab', 'search_trend_employment_f_all',   'f|all',   'index', 'value', '지수', '네이버 취업 키워드 검색 지수 (여성)'),
  STRUCT('kr_search_employment_m_value'     , 'KR검색트렌드', 'naver_datalab', 'search_trend_employment_m_all',   'm|all',   'index', 'value', '지수', '네이버 취업 키워드 검색 지수 (남성)'),
  -- ── KR 구인공고 (Naver) ─────────────────────────────────────────────
  STRUCT('kr_jobpost_total_value'           , 'KR구인공고', 'naver_jobpost', 'job_posting_count_total',  'total',  'index', 'value', '건', '네이버 전체 구인공고 건수'),
  STRUCT('kr_jobpost_it_value'              , 'KR구인공고', 'naver_jobpost', 'job_posting_count_it',     'it',     'index', 'value', '건', '네이버 IT 분야 구인공고 건수'),
  STRUCT('kr_jobpost_mfg_value'             , 'KR구인공고', 'naver_jobpost', 'job_posting_count_mfg',    'mfg',    'index', 'value', '건', '네이버 제조업 분야 구인공고 건수'),
  -- ── KR 뉴스 언급량 (Naver) ──────────────────────────────────────────
  STRUCT('kr_news_employment_value'         , 'KR뉴스', 'naver_news', 'news_count_employment',  'employment',  'index', 'value', '건', '취업/고용 관련 뉴스 기사 수'),
  STRUCT('kr_news_layoff_value'             , 'KR뉴스', 'naver_news', 'news_count_layoff',      'layoff',      'index', 'value', '건', '정리해고/구조조정 관련 뉴스 기사 수'),
  STRUCT('kr_news_startup_value'            , 'KR뉴스', 'naver_news', 'news_count_startup',     'startup',     'index', 'value', '건', '창업/스타트업 관련 뉴스 기사 수'),
  -- ── KR 구글 트렌드 ──────────────────────────────────────────────────
  STRUCT('kr_gtrend_employment_value'       , 'KR구글트렌드', 'google_trends', 'gtrend_employment',  'employment',  'index', 'value', '지수', '구글 취업 검색 트렌드 지수'),
  STRUCT('kr_gtrend_jobchange_value'        , 'KR구글트렌드', 'google_trends', 'gtrend_jobchange',   'jobchange',   'index', 'value', '지수', '구글 이직 검색 트렌드 지수'),
  STRUCT('kr_gtrend_hiring_value'           , 'KR구글트렌드', 'google_trends', 'gtrend_hiring',      'hiring',      'index', 'value', '지수', '구글 채용 검색 트렌드 지수'),
  STRUCT('kr_gtrend_unemployment_value'     , 'KR구글트렌드', 'google_trends', 'gtrend_unemployment','unemployment','index', 'value', '지수', '구글 실업/실직 검색 트렌드 지수')
]) AS t
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# fs_long DDL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dedup View에서 카탈로그 정의 기준으로 JOIN → 피처 추출
# 카탈로그 메타와 LEFT JOIN해서 feature_name을 부여
FS_LONG_SQL = f"""
CREATE OR REPLACE VIEW {_view('fs_long')} AS
-- Feature Store (Long 형태)
-- (period, feature_name) 기준으로 one value per row
-- 사용: R tidyr::pivot_wider(), pandas pivot_table()
-- 피처 카탈로그(fs_feature_catalog)와 feature_name으로 JOIN 가능
--
-- 피처 매핑 방식:
--   Raw Dedup의 (source, variable_name, category_key, adjustment_type, metric)을
--   카탈로그의 *_filter 컬럼과 CASE WHEN으로 매핑 → feature_name 부여
--
-- NOTE: variable_name_filter는 Raw의 variable_name(= adj_type + '_' + base)과 매핑
--       metric은 Raw variable_name의 '__' suffix 이후 부분(없으면 'value')
WITH parsed AS (
  SELECT
    period,
    DATE(CONCAT(period, '-01'))  AS period_date,
    source,
    variable_name,
    -- metric suffix 파싱
    CASE
      WHEN STRPOS(variable_name, '__') > 0
        THEN SUBSTR(variable_name, STRPOS(variable_name, '__') + 2)
      ELSE 'value'
    END AS metric,
    -- base variable_name
    CASE
      WHEN STRPOS(variable_name, '__') > 0
        THEN SUBSTR(variable_name, 1, STRPOS(variable_name, '__') - 1)
      ELSE variable_name
    END AS base_variable_name,
    category_key,
    adjustment_type,
    value,
    ingested_at
  FROM {_DEDUP}
  WHERE value IS NOT NULL
),
mapped AS (
  -- 카탈로그와 JOIN하여 feature_name 부여
  SELECT
    p.period,
    p.period_date,
    c.feature_name,
    c.feature_group,
    c.unit,
    c.description_ko,
    p.value,
    p.ingested_at
  FROM parsed p
  INNER JOIN {_view('fs_feature_catalog')} c
    ON  p.source            = c.source
    AND p.base_variable_name = c.variable_name_filter
    AND p.category_key       = c.category_key_filter
    AND p.adjustment_type    = c.adjustment_type_filter
    AND p.metric             = c.metric_filter
)
SELECT
  period,
  period_date,
  feature_name,
  feature_group,
  unit,
  description_ko,
  value,
  ingested_at
FROM mapped
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# fs_wide DDL — PIVOT (MAX(IF(...)))
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BigQuery 정적 PIVOT (PIVOT 절 미사용, MAX(IF) 패턴)
# period 1행 × 피처 58개 컬럼
# 사용: R read_bq() → ts() / xts() 직접 변환, sklearn / statsmodels 투입

def _wide_col(feature_name: str) -> str:
    """MAX(IF(feature_name = '...', value, NULL)) AS feature_name"""
    return (
        f"  MAX(IF(feature_name = '{feature_name}', value, NULL))"
        f" AS {feature_name}"
    )


# 전체 피처 이름 목록 (카탈로그와 동기화)
_ALL_FEATURES: list[str] = [
    # KR 고용 핵심
    "kr_emprate_total_value",
    "kr_emprate_male_value",
    "kr_emprate_female_value",
    "kr_employed_total_sa_value",
    "kr_employed_total_sa_mom_pct",
    "kr_employed_total_sa_yoy_pct",
    "kr_employed_total_sa_yoy",
    "kr_employed_mfg_sa_value",
    "kr_employed_mfg_sa_yoy_pct",
    "kr_employed_mfg_sa_yoy",
    "kr_employed_service_sa_value",
    "kr_employed_service_sa_yoy_pct",
    "kr_employed_service_sa_yoy",
    "kr_employed_mfg_raw_value",
    "kr_employed_mfg_raw_yoy_pct",
    "kr_employed_mfg_raw_yoy",
    "kr_employed_construction_sa_value",
    "kr_employed_it_sa_value",
    "kr_employed_finance_sa_value",
    # KR 고용 연령/성별
    "kr_employed_male_total_value",
    "kr_employed_female_total_value",
    "kr_employed_youth_value",
    "kr_employed_youth_yoy_pct",
    "kr_employed_youth_yoy",
    "kr_employed_senior_value",
    "kr_employed_prime_value",
    # KR 임금·근로시간
    "kr_wage_total_value",
    "kr_wage_total_yoy_pct",
    "kr_wage_total_yoy",
    "kr_wage_mfg_value",
    "kr_hours_total_value",
    "kr_hours_total_yoy_pct",
    "kr_hours_mfg_value",
    # KR 구직급여
    "kr_jobseeker_new_value",
    "kr_jobseeker_new_yoy_pct",
    "kr_jobseeker_new_yoy",
    "kr_jobseeker_ongoing_value",
    "kr_jobseeker_benefit_amount_value",
    # US 비농업 취업자
    "us_nonfarm_total_sa_value",
    "us_nonfarm_total_sa_mom_pct",
    "us_nonfarm_total_sa_yoy_pct",
    "us_nonfarm_total_sa_yoy",
    "us_nonfarm_mfg_sa_value",
    "us_nonfarm_professional_sa_value",
    "us_nonfarm_leisure_sa_value",
    # US 노동시장 지표
    "us_job_openings_sa_value",
    "us_job_openings_sa_yoy_pct",
    "us_quits_sa_value",
    "us_layoffs_sa_value",
    "us_hourly_wage_sa_value",
    "us_hourly_wage_sa_yoy_pct",
    "us_weekly_hours_sa_value",
    "us_unemp_rate_youth_value",
    # KR 검색 트렌드
    "kr_search_employment_value",
    "kr_search_jobchange_value",
    "kr_search_hiring_value",
    "kr_search_employment_youth_value",
    "kr_search_employment_f_value",
    "kr_search_employment_m_value",
    # KR 구인공고
    "kr_jobpost_total_value",
    "kr_jobpost_it_value",
    "kr_jobpost_mfg_value",
    # KR 뉴스 언급량
    "kr_news_employment_value",
    "kr_news_layoff_value",
    "kr_news_startup_value",
    # KR 구글 트렌드
    "kr_gtrend_employment_value",
    "kr_gtrend_jobchange_value",
    "kr_gtrend_hiring_value",
    "kr_gtrend_unemployment_value",
]

_WIDE_COLS = "\n".join(f"{_wide_col(f)}," for f in _ALL_FEATURES)
# 마지막 컬럼의 trailing comma 제거
_WIDE_COLS = _WIDE_COLS.rstrip(",\n")

FS_WIDE_SQL = f"""
CREATE OR REPLACE VIEW {_view('fs_wide')} AS
-- Feature Store (Wide 형태)
-- period 1행 × 피처 {len(_ALL_FEATURES)}개 컬럼
-- 사용: R read_bq() → xts/ts 변환, sklearn/statsmodels/Prophet 직접 투입
-- 결측(NULL): 해당 period에 해당 피처 데이터 없음
SELECT
  period,
  period_date,
{_WIDE_COLS}
FROM {_view('fs_long')}
GROUP BY period, period_date
ORDER BY period
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Feature Store View 목록 — 의존성 순서
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FS_VIEWS: list[tuple[str, str]] = [
    # 1. 카탈로그 (fs_long/fs_wide가 의존)
    ("fs_feature_catalog", FS_CATALOG_SQL),
    # 2. Long (fs_wide가 의존)
    ("fs_long",            FS_LONG_SQL),
    # 3. Wide (마지막)
    ("fs_wide",            FS_WIDE_SQL),
]

FS_VIEW_NAMES: list[str] = [name for name, _ in FS_VIEWS]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 실행 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def create_fs_views(
    client:     bigquery.Client,
    view_names: list[str] | None = None,
) -> dict:
    """
    Feature Store View 생성/갱신 (CREATE OR REPLACE VIEW).

    의존성 순서: fs_feature_catalog → fs_long → fs_wide

    Parameters
    ----------
    client     : BigQuery 클라이언트
    view_names : 특정 View만 대상으로 할 경우 이름 목록 (None이면 전체).

    Returns
    -------
    dict : {"created": [...], "failed": {...}}
    """
    target = [
        (n, s) for n, s in FS_VIEWS
        if view_names is None or n in view_names
    ]

    created: list[str]      = []
    failed:  dict[str, str] = {}

    for name, sql in target:
        try:
            client.query(sql).result()
            logger.info(f"[FS] ✓ {BQ_DATASET}.{name}")
            created.append(name)
        except Exception as exc:
            logger.error(f"[FS] ✗ {name}: {exc}")
            failed[name] = str(exc)

    logger.info(
        f"[FS] 완료 — 성공 {len(created)}/{len(target)}개"
        + (f"  실패: {list(failed.keys())}" if failed else "")
    )
    return {"created": created, "failed": failed}


def refresh_all_fs(client: bigquery.Client | None = None) -> dict:
    """
    모든 Feature Store View 갱신.
    client 미전달 시 ADC로 자동 생성.
    """
    if client is None:
        from bq_loader import get_bq_client
        client = get_bq_client()
    return create_fs_views(client)


def get_feature_count() -> int:
    return len(_ALL_FEATURES)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단독 실행:  python feature_store.py [--view view_name ...]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import argparse, json, sys, logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="Feature Store View 생성/갱신")
    p.add_argument(
        "--view", nargs="*",
        choices=FS_VIEW_NAMES,
        metavar="VIEW",
        help=f"갱신할 View 지정 (기본: 전체). 선택: {FS_VIEW_NAMES}",
    )
    p.add_argument(
        "--list-features",
        action="store_true",
        help="정의된 피처 목록 출력 후 종료",
    )
    args = p.parse_args()

    if args.list_features:
        for i, f in enumerate(_ALL_FEATURES, 1):
            print(f"  {i:>3}. {f}")
        print(f"\n총 {len(_ALL_FEATURES)}개 피처")
        sys.exit(0)

    result = refresh_all_fs() if not args.view else create_fs_views(
        client     = __import__("bq_loader").get_bq_client(),
        view_names = args.view,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["failed"] else 0)

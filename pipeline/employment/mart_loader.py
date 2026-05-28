"""
mart_loader.py
─────────────────────────────────────────────────────────────────────────────
MART & FEATURE STORE — BigQuery View DDL 생성 및 관리

━━━ 설계 원칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  모든 Mart는 employment_raw_dedup View 위에 CREATE OR REPLACE VIEW 로 생성.
  물리 테이블을 만들지 않으므로 Raw 적재만으로 Mart가 자동 갱신된다.

  파생 지표(MoM%/YoY%/MA*)는 Raw에 이미 별도 variable_name(__suffix)으로
  적재돼 있으므로, Mart에서는 variable_name과 metric을 분리해 노출한다.

━━━ variable_name 규칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Raw 원본:   {adj_type}_{base}            예) raw_employed_by_sex_age
  Raw 파생:   {adj_type}_{base}__{metric}  예) raw_employed__mom_pct

  Mart 노출:
    variable_name = base 부분만  (adj_type prefix 포함, metric suffix 제거)
    metric        = value(원본) | mom_pct | yoy_pct | ma3 | ma6 | ma12

━━━ Mart 목록 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  소스           View 이름                           설명
  ─────────────  ──────────────────────────────────  ─────────────────────
  kosis_emp      mart_kr_employment                  한국 경제활동인구
  kosis_wage     mart_kr_wage_hours                  임금·근로시간
  kosis_benefit  mart_kr_job_seeker_benefits         구직급여 신청
  bls            mart_us_nonfarm_employment          미국 비농업 취업자
                 mart_us_labor_indicators            미국 임금·JOLTS·CPS
  naver_datalab  mart_kr_search_trend                네이버 검색 트렌드
  naver_jobpost  mart_kr_job_posting                 구인공고 건수
  naver_news     mart_kr_news_volume                 뉴스 언급량
  google_trends  mart_kr_google_trend                구글 검색 트렌드
  (통합)         mart_monthly_indicators             전체 지표 UNION ALL

━━━ 공통 컬럼 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  period          STRING     YYYY-MM
  period_date     DATE       YYYY-MM-01  (파티션 프루닝용)
  source          STRING     수집 소스 코드
  variable_name   STRING     지표 기본명 (metric suffix 제거)
  metric          STRING     value / mom_pct / yoy_pct / ma3 / ma6 / ma12
  category_key    STRING     분류 코드
  category_name   STRING     분류 한글명
  value           FLOAT64    지표값
  adjustment_type STRING     raw / seasonal / index / derived_pct / derived_ma
  ingested_at     TIMESTAMP  적재 시각
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
# 공통 CTE 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _base_cte(
    source_filter: str,
    variable_like: list[str] | None = None,
) -> str:
    """
    Dedup View에서 소스/variable_name 기준으로 raw 데이터를 가져오는 CTE.

    variable_name 파싱을 서브쿼리로 처리해 WHERE에서 alias 참조 문제를 회피.
      - 외부 쿼리: base_variable_name / metric alias 사용
      - 내부 쿼리: variable_name 원문 그대로 LIKE 필터

    Parameters
    ----------
    source_filter  : BigQuery WHERE 조건 (예: "source = 'kosis_emp'")
    variable_like  : variable_name LIKE 패턴 목록. None이면 소스 전체.
    """
    # variable_name LIKE 필터 — 서브쿼리 내부에서 원문 컬럼에 적용
    var_clause = ""
    if variable_like:
        pats = " OR ".join(f"variable_name LIKE '{p}'" for p in variable_like)
        var_clause = f"AND ({pats})"

    return f"""
  WITH _raw AS (
    SELECT
      period,
      DATE(CONCAT(period, '-01'))                          AS period_date,
      source,
      variable_name,
      -- metric suffix 추출: '__' 이후 문자열, 없으면 'value'
      CASE
        WHEN STRPOS(variable_name, '__') > 0
          THEN SUBSTR(variable_name, STRPOS(variable_name, '__') + 2)
        ELSE 'value'
      END                                                  AS metric,
      -- base variable_name: '__' 이전, 없으면 그대로
      CASE
        WHEN STRPOS(variable_name, '__') > 0
          THEN SUBSTR(variable_name, 1, STRPOS(variable_name, '__') - 1)
        ELSE variable_name
      END                                                  AS base_variable_name,
      category_key,
      category_name,
      value,
      adjustment_type,
      ingested_at
    FROM {_DEDUP}
    WHERE {source_filter}
      {var_clause}
  )
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mart DDL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 1. 한국 경제활동인구 (kosis_emp) ─────────────────────────────────────────
def _mart_kr_employment() -> str:
    cte = _base_cte("source = 'kosis_emp'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_employment')} AS
-- 한국 경제활동인구조사 (KOSIS kosis_emp)
-- 원본: employed_by_sex_age / employed / employed_by_industry /
--       employed_by_sex_industry / employed_by_work_status 등
-- 파생: __mom_pct / __yoy_pct / __ma3 / __ma6 / __ma12
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 2. 임금·근로시간 (kosis_wage) ────────────────────────────────────────────
def _mart_kr_wage_hours() -> str:
    cte = _base_cte("source = 'kosis_wage'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_wage_hours')} AS
-- 사업체노동력조사 임금·근로시간 (KOSIS kosis_wage)
-- variable_name: wage_and_hours_by_industry_size
-- category_key : 산업코드|규모코드|항목코드(임금 / 근로시간)
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 3. 구직급여 신청 동향 (kosis_benefit) ────────────────────────────────────
def _mart_kr_job_seeker_benefits() -> str:
    cte = _base_cte("source = 'kosis_benefit'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_job_seeker_benefits')} AS
-- 고용행정통계 구직급여 신청 동향 (KOSIS kosis_benefit)
-- variable_name: job_seeker_trend
-- category_key : T001(신규신청) / T002(지속수급) / T003(수급종료) / T004(지급액)
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 4. 미국 비농업 취업자 (BLS CES) ─────────────────────────────────────────
def _mart_us_nonfarm_employment() -> str:
    cte = _base_cte(
        "source = 'bls'",
        ["%us_nonfarm_employment%"],
    )
    return f"""
CREATE OR REPLACE VIEW {_view('mart_us_nonfarm_employment')} AS
-- BLS CES — 미국 비농업 취업자 수 (전체 + 산업별)
-- variable_name: us_nonfarm_employment / us_nonfarm_employment_by_industry
-- category_key : total / mining / construction / manufacturing / ...
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 5. 미국 노동 지표 — 임금·JOLTS·CPS (BLS) ─────────────────────────────────
def _mart_us_labor_indicators() -> str:
    cte = _base_cte(
        "source = 'bls'",
        [
            "%us_hourly_wage%",
            "%us_weekly_hours%",
            "%us_weekly_earnings%",
            "%us_job_openings%",
            "%us_hires%",
            "%us_total_separations%",
            "%us_quits%",
            "%us_layoffs_discharges%",
            "%us_employment_by_age%",
            "%us_unemployment_rate_by_age%",
        ],
    )
    return f"""
CREATE OR REPLACE VIEW {_view('mart_us_labor_indicators')} AS
-- BLS — 미국 임금·근로시간·JOLTS·CPS 연령별 고용/실업률
-- CES: us_hourly_wage / us_weekly_hours / us_weekly_earnings
-- JOLTS: us_job_openings / us_hires / us_total_separations / us_quits / us_layoffs_discharges
-- CPS: us_employment_by_age / us_unemployment_rate_by_age
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 6. 네이버 DataLab 검색 트렌드 ───────────────────────────────────────────
def _mart_kr_search_trend() -> str:
    cte = _base_cte("source = 'naver_datalab'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_search_trend')} AS
-- 네이버 DataLab 검색 트렌드 (취업·이직·채용 키워드 × 성/연령)
-- variable_name: search_trend_{{keyword}}_{{gender}}_{{age}}
-- category_key : {{gender}}|{{age}}
-- value        : 네이버 검색 상대 지수 (0~100)
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 7. 구인공고 건수 (Naver) ─────────────────────────────────────────────────
def _mart_kr_job_posting() -> str:
    cte = _base_cte("source = 'naver_jobpost'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_job_posting')} AS
-- 네이버 채용공고 건수 (업종별)
-- variable_name: job_posting_count_{{sector}}
-- category_key : sector 코드
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 8. 뉴스 언급량 (Naver) ───────────────────────────────────────────────────
def _mart_kr_news_volume() -> str:
    cte = _base_cte("source = 'naver_news'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_news_volume')} AS
-- 네이버 뉴스 고용 관련 기사 건수 (토픽별)
-- variable_name: news_count_{{topic}}
-- category_key : topic 코드
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 9. 구글 검색 트렌드 ──────────────────────────────────────────────────────
def _mart_kr_google_trend() -> str:
    cte = _base_cte("source = 'google_trends'")
    return f"""
CREATE OR REPLACE VIEW {_view('mart_kr_google_trend')} AS
-- Google Trends 검색빈도 (취업·이직·채용 키워드)
-- variable_name: gtrend_{{keyword}}
-- category_key : keyword 코드
-- value        : 구글 검색 상대 지수 (0~100)
{cte}
SELECT
  period,
  period_date,
  source,
  base_variable_name  AS variable_name,
  metric,
  category_key,
  category_name,
  value,
  adjustment_type,
  ingested_at
FROM _raw
"""


# ── 10. 통합 월별 지표 ────────────────────────────────────────────────────────
def _mart_monthly_indicators() -> str:
    return f"""
CREATE OR REPLACE VIEW {_view('mart_monthly_indicators')} AS
-- 전체 소스 통합 월별 지표 — 분석/리포팅 레이어 기본 소스
-- 공통 컬럼: period / period_date / source / variable_name / metric /
--            category_key / category_name / value / adjustment_type / ingested_at
SELECT * FROM {_view('mart_kr_employment')}
UNION ALL
SELECT * FROM {_view('mart_kr_wage_hours')}
UNION ALL
SELECT * FROM {_view('mart_kr_job_seeker_benefits')}
UNION ALL
SELECT * FROM {_view('mart_us_nonfarm_employment')}
UNION ALL
SELECT * FROM {_view('mart_us_labor_indicators')}
UNION ALL
SELECT * FROM {_view('mart_kr_search_trend')}
UNION ALL
SELECT * FROM {_view('mart_kr_job_posting')}
UNION ALL
SELECT * FROM {_view('mart_kr_news_volume')}
UNION ALL
SELECT * FROM {_view('mart_kr_google_trend')}
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mart 목록 — 의존성 순서 (개별 View → 통합 View 순)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MART_VIEWS: list[tuple[str, str]] = [
    ("mart_kr_employment",          _mart_kr_employment()),
    ("mart_kr_wage_hours",          _mart_kr_wage_hours()),
    ("mart_kr_job_seeker_benefits", _mart_kr_job_seeker_benefits()),
    ("mart_us_nonfarm_employment",  _mart_us_nonfarm_employment()),
    ("mart_us_labor_indicators",    _mart_us_labor_indicators()),
    ("mart_kr_search_trend",        _mart_kr_search_trend()),
    ("mart_kr_job_posting",         _mart_kr_job_posting()),
    ("mart_kr_news_volume",         _mart_kr_news_volume()),
    ("mart_kr_google_trend",        _mart_kr_google_trend()),
    # 통합 View는 반드시 마지막 (개별 View 의존)
    ("mart_monthly_indicators",     _mart_monthly_indicators()),
]

MART_VIEW_NAMES: list[str] = [name for name, _ in MART_VIEWS]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 실행 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def create_mart_views(
    client:     bigquery.Client,
    view_names: list[str] | None = None,
) -> dict:
    """
    Mart View 생성/갱신 (CREATE OR REPLACE VIEW).

    Parameters
    ----------
    client     : BigQuery 클라이언트
    view_names : 특정 View만 대상으로 할 경우 이름 목록 (None이면 전체).
                 mart_monthly_indicators 는 개별 View 의존성 때문에
                 항상 목록 마지막에 실행된다.

    Returns
    -------
    dict : {
        "created": [view_name, ...],
        "failed":  {view_name: error_message},
    }
    """
    # 요청한 view_names만 필터, 순서는 MART_VIEWS 정의 순(의존성 보장) 유지
    target = [
        (n, s) for n, s in MART_VIEWS
        if view_names is None or n in view_names
    ]

    created: list[str]      = []
    failed:  dict[str, str] = {}

    for name, sql in target:
        try:
            client.query(sql).result()
            logger.info(f"[Mart] ✓ {BQ_DATASET}.{name}")
            created.append(name)
        except Exception as exc:
            logger.error(f"[Mart] ✗ {name}: {exc}")
            failed[name] = str(exc)

    logger.info(
        f"[Mart] 완료 — 성공 {len(created)}/{len(target)}개"
        + (f"  실패: {list(failed.keys())}" if failed else "")
    )
    return {"created": created, "failed": failed}


def refresh_all_marts(client: bigquery.Client | None = None) -> dict:
    """
    모든 Mart View 갱신. main.py 파이프라인 또는 단독 실행 모두 지원.
    client 미전달 시 ADC로 자동 생성.
    """
    if client is None:
        from bq_loader import get_bq_client
        client = get_bq_client()
    return create_mart_views(client)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단독 실행:  python mart_loader.py [--view view_name ...]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import argparse, json, sys, logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="Mart View 생성/갱신")
    p.add_argument(
        "--view", nargs="*",
        choices=MART_VIEW_NAMES,
        metavar="VIEW",
        help=f"갱신할 View 지정 (기본: 전체). 선택: {MART_VIEW_NAMES}",
    )
    args = p.parse_args()

    result = refresh_all_marts() if not args.view else create_mart_views(
        client     = __import__("bq_loader").get_bq_client(),
        view_names = args.view,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["failed"] else 0)

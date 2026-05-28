# ============================================================================
# 05_ccf.R
# 교차상관 분석 (CCF — Cross-Correlation Function)
#
# 목적:
#   피처 간 선행/후행 관계 파악 → 경기 선행지표 발굴
#   예) 구직급여 신청↑ → N개월 후 취업자 수↓
#       구인공고↑ → N개월 후 취업자 수↑
#
# 분석 쌍:
#   타겟(y): 한국 주요 고용지표 4개 (취업자 수, 임금, 구직급여, 청년취업)
#   선행 후보(x): 검색트렌드, 구인공고, 구글트렌드, 미국 지표, 구직급여
#
# 분석 항목:
#   - lag -CCF_LAG_MAX ~ +CCF_LAG_MAX의 교차상관계수
#   - 최대 상관 lag 및 방향 (x가 y에 선행/동행/후행)
#   - 유의성 판단 (95% CI: ±1.96/√n)
#
# BQ 저장:
#   analysis_ccf_values   : lag별 교차상관계수
#   analysis_ccf_summary  : 피처쌍별 최대 상관 lag 요약
# ============================================================================

source("00_config.R")
source("01_load_features.R")

suppressPackageStartupMessages({
  library(purrr)
})

message("[05] CCF 분석 시작")

# ── 분석 대상 정의 ───────────────────────────────────────────────────────

# 타겟 지표 (y): 한국 핵심 고용 결과 지표
CCF_TARGETS <- c(
  "kr_employed_total_sa_value",    # 전체 취업자 수 (계절조정)
  "kr_employed_youth_value",       # 청년 취업자 수
  "kr_wage_total_value",           # 월평균 임금
  "kr_jobseeker_new_value"         # 구직급여 신규 신청 (실직 지표)
)

# 선행지표 후보 (x): 실물·심리·온라인 지표
CCF_PREDICTORS <- c(
  # 온라인 선행지표
  "kr_search_employment_value",
  "kr_search_jobchange_value",
  "kr_search_hiring_value",
  "kr_jobpost_total_value",
  "kr_jobpost_it_value",
  "kr_gtrend_employment_value",
  "kr_gtrend_jobchange_value",
  "kr_gtrend_unemployment_value",
  # 미국 선행지표 (글로벌 경기)
  "us_nonfarm_total_sa_value",
  "us_job_openings_sa_value",
  "us_quits_sa_value",
  "us_layoffs_sa_value",
  "us_hourly_wage_sa_value",
  # 국내 고용 파생
  "kr_jobseeker_new_yoy_pct",
  "kr_employed_total_sa_yoy_pct"
)

# target과 predictor 중 실제 존재하는 것만 필터
CCF_TARGETS    <- intersect(CCF_TARGETS,    numeric_cols)
CCF_PREDICTORS <- intersect(CCF_PREDICTORS, numeric_cols)

message(glue(
  "[05] 분석 쌍: {length(CCF_TARGETS)} targets × {length(CCF_PREDICTORS)} predictors ",
  "= {length(CCF_TARGETS) * length(CCF_PREDICTORS)}쌍"
))

# ── CCF 계산 함수 ────────────────────────────────────────────────────────
run_ccf <- function(x_name, y_name) {
  if (x_name == y_name) return(list(values = NULL, summary = NULL))

  x <- ts_list[[x_name]]
  y <- ts_list[[y_name]]

  # 공통 유효 구간 확인
  n_valid <- sum(!is.na(x) & !is.na(y))
  if (n_valid < CCF_LAG_MAX * 2 + 4) return(list(values = NULL, summary = NULL))

  # 결측 보간
  x <- zoo::na.approx(zoo::na.locf(zoo::na.locf(x, na.rm=FALSE), fromLast=TRUE, na.rm=FALSE), na.rm=FALSE)
  y <- zoo::na.approx(zoo::na.locf(zoo::na.locf(y, na.rm=FALSE), fromLast=TRUE, na.rm=FALSE), na.rm=FALSE)
  if (any(is.na(x)) || any(is.na(y))) return(list(values = NULL, summary = NULL))

  ccf_res  <- ccf(x, y, lag.max = CCF_LAG_MAX, plot = FALSE)
  ci_bound <- 1.96 / sqrt(n_valid)
  lags     <- as.integer(ccf_res$lag)
  acf_vals <- as.numeric(ccf_res$acf)

  values_df <- tibble(
    x_feature    = x_name,
    y_feature    = y_name,
    lag          = lags,        # 양수: x가 y에 선행, 음수: x가 y에 후행
    ccf          = acf_vals,
    ci_upper     =  ci_bound,
    ci_lower     = -ci_bound,
    is_sig       = abs(acf_vals) > ci_bound,
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )

  # 최대 절댓값 lag 찾기
  max_idx     <- which.max(abs(acf_vals))
  best_lag    <- lags[max_idx]
  best_ccf    <- acf_vals[max_idx]

  # 선행/동행/후행 판단
  # CCF(lag=k): y[t] ~ x[t-k] 즉 lag>0이면 x가 y에 선행
  relationship <- case_when(
    best_lag > 0  ~ glue("x가 y에 {best_lag}개월 선행"),
    best_lag < 0  ~ glue("x가 y에 {abs(best_lag)}개월 후행"),
    TRUE          ~ "동행"
  )

  n_sig <- sum(abs(acf_vals) > ci_bound)

  summary_df <- tibble(
    x_feature        = x_name,
    y_feature        = y_name,
    best_lag         = best_lag,
    best_ccf         = best_ccf,
    best_ccf_abs     = abs(best_ccf),
    relationship     = as.character(relationship),
    direction        = ifelse(best_ccf > 0, "정방향", "역방향"),
    n_sig_lags       = n_sig,
    n_obs            = n_valid,
    run_id           = RUN_ID,
    analyzed_at      = Sys.time()
  )

  list(values = values_df, summary = summary_df)
}

# ── 전체 쌍 실행 ─────────────────────────────────────────────────────────
pairs_list <- expand.grid(
  x_name = CCF_PREDICTORS,
  y_name = CCF_TARGETS,
  stringsAsFactors = FALSE
)

results <- pmap(pairs_list, ~ run_ccf(..1, ..2))

ccf_values  <- map_dfr(results, ~ .x$values)
ccf_summary <- map_dfr(results, ~ .x$summary)

# 결과 없을 때 빈 데이터프레임 보정
if (nrow(ccf_summary) == 0 || !("best_ccf_abs" %in% names(ccf_summary))) {
  ccf_summary <- tibble::tibble(
    target=character(), predictor=character(), best_lag=integer(),
    best_ccf=double(), best_ccf_abs=double(), n_obs=integer(), run_id=character()
  )
}

# 강한 선행지표 요약 로그 (|CCF| >= 0.5, lag > 0)
strong_lead <- ccf_summary |>
  filter(best_ccf_abs >= 0.5, best_lag > 0) |>
  arrange(desc(best_ccf_abs))

message(glue(
  "[05] CCF 완료: {nrow(ccf_summary)}쌍 분석  ",
  "| 강한 선행지표 (|r|≥0.5, lag>0): {nrow(strong_lead)}쌍"
))

if (nrow(strong_lead) > 0) {
  top5 <- head(strong_lead, 5)
  message("[05] Top 선행지표 (|CCF| 기준):")
  walk(seq_len(nrow(top5)), function(i) {
    r <- top5[i, ]
    message(glue(
      "       {r$x_feature} → {r$y_feature}  ",
      "lag={r$best_lag}  r={round(r$best_ccf, 3)}"
    ))
  })
}

# 빈 결과일 때 BQ 업로드 건너뜀 (스키마 없음 오류 방지)
if (nrow(ccf_values) > 0) {
  bq_replace_run(ccf_values,  "analysis_ccf_values",  RUN_ID)
} else {
  message("[05] ccf_values 결과 없음 — BQ 저장 건너뜀")
}

if (nrow(ccf_summary) > 0) {
  bq_replace_run(ccf_summary, "analysis_ccf_summary", RUN_ID)
} else {
  message("[05] ccf_summary 결과 없음 — BQ 저장 건너뜀")
}

message("[05] CCF 분석 완료")

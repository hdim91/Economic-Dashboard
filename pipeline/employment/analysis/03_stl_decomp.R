# ============================================================================
# 03_stl_decomp.R
# STL 시계열 분해 (Seasonal-Trend decomposition using LOESS)
#
# 분석 항목:
#   - 추세(trend), 계절성(seasonal), 잔차(remainder) 분해
#   - 계절 강도 (Fs): var(remainder) / var(seasonal + remainder)
#   - 추세 강도 (Ft): var(remainder) / var(trend + remainder)
#   - 잔차 이상치 (|remainder| > 3 × IQR(remainder))
#
# 대상: 24개월 이상 유효 데이터를 가진 피처만
#
# BQ 저장:
#   analysis_stl_components   : 월별 분해 성분 (trend/seasonal/remainder)
#   analysis_stl_strength     : 피처별 계절/추세 강도 요약
# ============================================================================

source("00_config.R")
source("01_load_features.R")

suppressPackageStartupMessages({
  library(purrr)
})

message("[03] STL 분해 시작")

MIN_OBS <- 24  # STL 최소 관측수

# ── STL 실행 함수 ────────────────────────────────────────────────────────
run_stl <- function(col_name) {
  x <- ts_list[[col_name]]

  # 유효 관측수 확인
  n_valid <- sum(!is.na(x))
  if (n_valid < MIN_OBS) {
    message(glue("  [skip] {col_name}: 유효 관측 {n_valid}개 < {MIN_OBS}"))
    return(list(components = NULL, strength = NULL))
  }

  # NA가 있으면 선형 보간 후 STL 실행
  x_interp <- zoo::na.approx(x, na.rm = FALSE)
  if (any(is.na(x_interp))) {
    # 앞뒤 끝단 NA는 locf/nocb
    x_interp <- zoo::na.locf(zoo::na.locf(x_interp, na.rm = FALSE),
                              fromLast = TRUE, na.rm = FALSE)
  }
  if (any(is.na(x_interp))) return(list(components = NULL, strength = NULL))

  stl_fit <- tryCatch(
    stl(x_interp, s.window = STL_S_WINDOW, robust = STL_ROBUST),
    error = function(e) {
      message(glue("  [error] {col_name} STL 실패: {conditionMessage(e)}"))
      return(NULL)
    }
  )
  if (is.null(stl_fit)) return(list(components = NULL, strength = NULL))

  comp <- stl_fit$time.series  # trend, seasonal, remainder

  # period 컬럼 생성
  time_idx <- time(x_interp)
  yr  <- floor(time_idx)
  mo  <- round((time_idx - yr) * 12) + 1
  periods <- sprintf("%04d-%02d", yr, mo)

  # 강도 지표
  var_rem  <- var(comp[, "remainder"],  na.rm = TRUE)
  var_seas <- var(comp[, "seasonal"],   na.rm = TRUE)
  var_trd  <- var(comp[, "trend"],      na.rm = TRUE)

  Fs <- max(0, 1 - var_rem / (var_seas + var_rem))  # 계절 강도
  Ft <- max(0, 1 - var_rem / (var_trd  + var_rem))  # 추세 강도

  # 잔차 이상치 플래그
  rem_iqr   <- IQR(comp[, "remainder"], na.rm = TRUE)
  rem_thresh <- 3 * rem_iqr
  is_anom   <- abs(comp[, "remainder"]) > rem_thresh

  components_df <- tibble(
    feature_name  = col_name,
    period        = periods,
    observed      = as.numeric(x_interp),
    trend         = as.numeric(comp[, "trend"]),
    seasonal      = as.numeric(comp[, "seasonal"]),
    remainder     = as.numeric(comp[, "remainder"]),
    remainder_anom = is_anom,
    run_id        = RUN_ID,
    analyzed_at   = Sys.time()
  )

  strength_df <- tibble(
    feature_name       = col_name,
    n_obs              = n_valid,
    seasonal_strength  = Fs,
    trend_strength     = Ft,
    seasonal_label     = case_when(
      Fs >= 0.64 ~ "강함",
      Fs >= 0.36 ~ "중간",
      TRUE       ~ "약함"
    ),
    trend_label        = case_when(
      Ft >= 0.64 ~ "강함",
      Ft >= 0.36 ~ "중간",
      TRUE       ~ "약함"
    ),
    n_remainder_anom   = sum(is_anom),
    run_id             = RUN_ID,
    analyzed_at        = Sys.time()
  )

  list(components = components_df, strength = strength_df)
}

# ── 전체 피처 실행 ────────────────────────────────────────────────────────
results <- map(numeric_cols, run_stl)
names(results) <- numeric_cols

stl_components <- map_dfr(results, ~ .x$components)
stl_strength   <- map_dfr(results, ~ .x$strength)

message(glue(
  "[03] STL 완료: {length(unique(stl_components$feature_name))}개 피처 분해  ",
  "/ {nrow(stl_components)}행"
))

# 강도 요약 로그
if (nrow(stl_strength) > 0) {
  strong_seasonal <- stl_strength |> filter(seasonal_label == "강함") |> nrow()
  strong_trend    <- stl_strength |> filter(trend_label    == "강함") |> nrow()
  message(glue(
    "[03] 계절성 강함: {strong_seasonal}개 피처  /  추세 강함: {strong_trend}개 피처"
  ))
}

# ── BQ 저장 ──────────────────────────────────────────────────────────────
bq_replace_run(stl_components, "analysis_stl_components", RUN_ID)
bq_replace_run(stl_strength,   "analysis_stl_strength",   RUN_ID)

message("[03] STL 분해 완료")

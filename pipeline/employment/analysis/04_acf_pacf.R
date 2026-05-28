# ============================================================================
# 04_acf_pacf.R
# 자기상관 분석 (ACF / PACF)
#
# 분석 항목:
#   - ACF(자기상관함수): lag 1~24 자기상관계수 및 유의성 (95% CI)
#   - PACF(편자기상관함수): lag 1~24 편자기상관계수 및 유의성
#   - Ljung-Box 검정: 백색소음 여부 (lag=12 기준)
#   - 단위근 검정: ADF (Augmented Dickey-Fuller) → 정상성 판단
#   - 권장 차분 횟수: ndiffs() (KPSS 기반)
#
# 대상: STL 잔차 기준 (추세·계절성 제거 후 순수 자기상관 구조 파악)
#       + 원계열 ACF (계절형 자기상관 확인)
#
# BQ 저장:
#   analysis_acf_values   : lag별 ACF/PACF 계수
#   analysis_acf_summary  : 피처별 정상성·백색소음 검정 요약
# ============================================================================

source("00_config.R")
source("01_load_features.R")
source("03_stl_decomp.R")   # stl_components 사용

suppressPackageStartupMessages({
  library(purrr)
  library(tseries)   # adf.test
  library(forecast)  # ndiffs
})

message("[04] ACF/PACF 분석 시작")

ACF_LAG_MAX <- 24

# ── ACF/PACF 계산 함수 ───────────────────────────────────────────────────
run_acf <- function(col_name) {
  x <- ts_list[[col_name]]
  n_valid <- sum(!is.na(x))
  if (n_valid < ACF_LAG_MAX + 2) return(list(values = NULL, summary = NULL))

  # 원계열 보간 (STL과 동일 처리)
  x_interp <- zoo::na.approx(x, na.rm = FALSE)
  x_interp <- zoo::na.locf(zoo::na.locf(x_interp, na.rm = FALSE),
                            fromLast = TRUE, na.rm = FALSE)
  if (any(is.na(x_interp))) return(list(values = NULL, summary = NULL))

  # STL 잔차 추출 (이미 03에서 계산됨)
  rem_vec <- stl_components |>
    filter(feature_name == col_name) |>
    pull(remainder)

  has_remainder <- length(rem_vec) == length(x_interp)

  # 95% CI 경계 (±1.96/√n)
  ci_bound <- 1.96 / sqrt(length(x_interp))

  # ── 원계열 ACF/PACF ───
  acf_raw  <- acf( x_interp, lag.max = ACF_LAG_MAX, plot = FALSE)
  pacf_raw <- pacf(x_interp, lag.max = ACF_LAG_MAX, plot = FALSE)

  df_orig <- tibble(
    feature_name = col_name,
    series_type  = "original",
    lag          = 1:ACF_LAG_MAX,
    acf          = as.numeric(acf_raw$acf[ 2:(ACF_LAG_MAX+1)]),
    pacf         = as.numeric(pacf_raw$acf[1:ACF_LAG_MAX]),
    ci_upper     =  ci_bound,
    ci_lower     = -ci_bound,
    acf_sig      = abs(as.numeric(acf_raw$acf[ 2:(ACF_LAG_MAX+1)])) > ci_bound,
    pacf_sig     = abs(as.numeric(pacf_raw$acf[1:ACF_LAG_MAX]))      > ci_bound,
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )

  # ── STL 잔차 ACF/PACF ─
  df_rem <- NULL
  if (has_remainder && length(rem_vec) >= ACF_LAG_MAX + 2) {
    rem_ts   <- ts(rem_vec, start = start(x_interp), frequency = 12)
    acf_rem  <- acf( rem_ts, lag.max = ACF_LAG_MAX, plot = FALSE)
    pacf_rem <- pacf(rem_ts, lag.max = ACF_LAG_MAX, plot = FALSE)
    ci_rem   <- 1.96 / sqrt(length(rem_vec))

    df_rem <- tibble(
      feature_name = col_name,
      series_type  = "remainder",
      lag          = 1:ACF_LAG_MAX,
      acf          = as.numeric(acf_rem$acf[ 2:(ACF_LAG_MAX+1)]),
      pacf         = as.numeric(pacf_rem$acf[1:ACF_LAG_MAX]),
      ci_upper     =  ci_rem,
      ci_lower     = -ci_rem,
      acf_sig      = abs(as.numeric(acf_rem$acf[ 2:(ACF_LAG_MAX+1)])) > ci_rem,
      pacf_sig     = abs(as.numeric(pacf_rem$acf[1:ACF_LAG_MAX]))      > ci_rem,
      run_id       = RUN_ID,
      analyzed_at  = Sys.time()
    )
  }

  values_df <- bind_rows(df_orig, df_rem)

  # ── 요약: 정상성 + 백색소음 ─
  lb_p  <- tryCatch(Box.test(x_interp, lag = 12, type = "Ljung-Box")$p.value,
                    error = function(e) NA_real_)
  adf_p <- tryCatch(tseries::adf.test(x_interp)$p.value,
                    error = function(e) NA_real_)
  n_d   <- tryCatch(forecast::ndiffs(x_interp, test = "kpss"),
                    error = function(e) NA_integer_)

  summary_df <- tibble(
    feature_name       = col_name,
    n_obs              = length(x_interp),
    ljung_box_p        = lb_p,
    is_white_noise     = !is.na(lb_p) && lb_p >= 0.05,
    adf_p              = adf_p,
    is_stationary      = !is.na(adf_p) && adf_p < 0.05,
    recommended_diffs  = n_d,
    n_sig_acf_lags     = sum(df_orig$acf_sig,  na.rm = TRUE),
    n_sig_pacf_lags    = sum(df_orig$pacf_sig, na.rm = TRUE),
    run_id             = RUN_ID,
    analyzed_at        = Sys.time()
  )

  list(values = values_df, summary = summary_df)
}

results <- map(numeric_cols, run_acf)
names(results) <- numeric_cols

acf_values  <- map_dfr(results, ~ .x$values)
acf_summary <- map_dfr(results, ~ .x$summary)

stationary_n    <- sum(acf_summary$is_stationary,  na.rm = TRUE)
white_noise_n   <- sum(acf_summary$is_white_noise,  na.rm = TRUE)
message(glue(
  "[04] ACF/PACF 완료: {nrow(acf_values)}행  ",
  "| 정상 시계열: {stationary_n}개  | 백색소음: {white_noise_n}개"
))

bq_replace_run(acf_values,  "analysis_acf_values",  RUN_ID)
bq_replace_run(acf_summary, "analysis_acf_summary",  RUN_ID)

message("[04] ACF/PACF 분석 완료")

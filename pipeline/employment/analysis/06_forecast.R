# ============================================================================
# 06_forecast.R
# 예측 모델: ARIMA + Prophet
#
# 분석 항목:
#   ARIMA:
#     - auto.arima()로 최적 (p,d,q)(P,D,Q)[12] 자동 탐색
#     - 향후 ARIMA_HORIZON개월 예측 + 95%/80% 신뢰구간
#     - 모델 진단: AIC, BIC, 잔차 Ljung-Box p-value
#
#   Prophet:
#     - 추세 + 계절성(연간) + 한국 공휴일 regressors
#     - 향후 PROPHET_HORIZON개월 예측 + 불확실성 구간
#     - 추세 변화점 감지
#
# 대상: 핵심 고용지표 6개 (계산 시간 절약)
#   kr_employed_total_sa_value
#   kr_employed_youth_value
#   kr_wage_total_value
#   kr_jobseeker_new_value
#   us_nonfarm_total_sa_value
#   us_job_openings_sa_value
#
# BQ 저장:
#   analysis_forecast_arima    : ARIMA 예측값 및 신뢰구간
#   analysis_forecast_prophet  : Prophet 예측값 및 불확실성 구간
#   analysis_forecast_models   : 모델 메타 (파라미터, AIC, 진단)
# ============================================================================

source("00_config.R")
source("01_load_features.R")

suppressPackageStartupMessages({
  library(forecast)
  library(prophet)
  library(purrr)
})

message("[06] 예측 모델 시작")

# ── 예측 대상 피처 ───────────────────────────────────────────────────────
FORECAST_TARGETS <- c(
  "kr_employed_total_sa_value",
  "kr_employed_youth_value",
  "kr_wage_total_value",
  "kr_jobseeker_new_value",
  "us_nonfarm_total_sa_value",
  "us_job_openings_sa_value"
)
FORECAST_TARGETS <- intersect(FORECAST_TARGETS, numeric_cols)

# ── 예측 기간 라벨 생성 (PERIOD_END 이후 N개월) ──────────────────────────
future_periods <- function(n) {
  end_date <- as.Date(paste0(PERIOD_END, "-01"))
  seq.Date(end_date %m+% months(1),
           end_date %m+% months(n),
           by = "month") |>
    format("%Y-%m")
}

arima_future  <- future_periods(ARIMA_HORIZON)
prophet_future <- future_periods(PROPHET_HORIZON)

# ────────────────────────────────────────────────────────────────────────────
# ARIMA
# ────────────────────────────────────────────────────────────────────────────
run_arima <- function(col_name) {
  x <- ts_list[[col_name]]
  n_valid <- sum(!is.na(x))
  if (n_valid < 24) return(list(forecast = NULL, model = NULL))

  x_interp <- zoo::na.approx(zoo::na.locf(zoo::na.locf(x, na.rm=FALSE),
                                           fromLast=TRUE, na.rm=FALSE), na.rm=FALSE)
  if (any(is.na(x_interp))) return(list(forecast = NULL, model = NULL))

  fit <- tryCatch(
    forecast::auto.arima(
      x_interp,
      seasonal      = ARIMA_SEASONAL,
      stepwise      = FALSE,   # 전체 탐색 (정확도 우선)
      approximation = FALSE,
      trace         = FALSE
    ),
    error = function(e) {
      message(glue("  [ARIMA error] {col_name}: {conditionMessage(e)}"))
      return(NULL)
    }
  )
  if (is.null(fit)) return(list(forecast = NULL, model = NULL))

  fc <- forecast(fit, h = ARIMA_HORIZON, level = c(80, 95))

  forecast_df <- tibble(
    feature_name  = col_name,
    model_type    = "ARIMA",
    period        = arima_future,
    forecast      = as.numeric(fc$mean),
    lo_80         = as.numeric(fc$lower[, 1]),
    hi_80         = as.numeric(fc$upper[, 1]),
    lo_95         = as.numeric(fc$lower[, 2]),
    hi_95         = as.numeric(fc$upper[, 2]),
    run_id        = RUN_ID,
    analyzed_at   = Sys.time()
  )

  # Ljung-Box 잔차 진단
  lb_p <- tryCatch(
    Box.test(residuals(fit), lag = 12, type = "Ljung-Box")$p.value,
    error = function(e) NA_real_
  )

  arima_order <- arimaorder(fit)
  model_df <- tibble(
    feature_name   = col_name,
    model_type     = "ARIMA",
    arima_p        = arima_order["p"],
    arima_d        = arima_order["d"],
    arima_q_ns     = arima_order["q"],
    arima_sp       = arima_order["P"],
    arima_sd       = arima_order["D"],
    arima_sq       = arima_order["Q"],
    arima_period   = arima_order["Frequency"],
    model_str      = as.character(fit),
    aic            = AIC(fit),
    bic            = BIC(fit),
    ljung_box_p    = lb_p,
    residual_ok    = !is.na(lb_p) && lb_p >= 0.05,
    run_id         = RUN_ID,
    analyzed_at    = Sys.time()
  )

  message(glue(
    "  [ARIMA] {col_name}: {model_df$model_str}  AIC={round(model_df$aic,1)}  ",
    "LB_p={round(lb_p,3)}"
  ))

  list(forecast = forecast_df, model = model_df)
}

# ────────────────────────────────────────────────────────────────────────────
# Prophet
# ────────────────────────────────────────────────────────────────────────────
run_prophet <- function(col_name) {
  x <- ts_list[[col_name]]
  n_valid <- sum(!is.na(x))
  if (n_valid < 24) return(list(forecast = NULL, model = NULL))

  x_interp <- zoo::na.approx(zoo::na.locf(zoo::na.locf(x, na.rm=FALSE),
                                           fromLast=TRUE, na.rm=FALSE), na.rm=FALSE)
  if (any(is.na(x_interp))) return(list(forecast = NULL, model = NULL))

  # Prophet 입력 형식: ds(Date), y(numeric)
  time_idx <- time(x_interp)
  yr  <- floor(time_idx)
  mo  <- round((time_idx - yr) * 12) + 1
  ds  <- as.Date(sprintf("%04d-%02d-01", yr, mo))

  df_prophet <- tibble(ds = ds, y = as.numeric(x_interp))

  # 한국 공휴일 포함 Prophet 모델 생성 → 피팅
  # fix-01: prophet()에 df를 직접 넘기면 즉시 피팅돼 add_country_holidays를
  #         적용할 수 없음. 모델 스펙 생성(df 없음) → 공휴일 추가 → fit.prophet()
  #         순서로 변경. country_code → country_name (prophet R 패키지 실제 파라미터명).
  m <- tryCatch({
    m_spec <- prophet::prophet(
      yearly.seasonality  = TRUE,
      weekly.seasonality  = FALSE,
      daily.seasonality   = FALSE,
      n.changepoints      = PROPHET_CHANGEPOINTS,
      interval.width      = 0.95,
      uncertainty.samples = 1000
    )
    m_spec <- tryCatch(
      prophet::add_country_holidays(m_spec, country_name = "KR"),
      error = function(e) {
        message(glue("  [Prophet] {col_name}: 공휴일 추가 실패, 스킵 ({conditionMessage(e)})"))
        m_spec  # 공휴일 없이 계속
      }
    )
    prophet::fit.prophet(m_spec, df_prophet)
  }, error = function(e) {
    message(glue("  [Prophet error] {col_name}: {conditionMessage(e)}"))
    return(NULL)
  })
  if (is.null(m)) return(list(forecast = NULL, model = NULL))

  future_df <- prophet::make_future_dataframe(m, periods = PROPHET_HORIZON,
                                               freq = "month")
  fc <- predict(m, future_df)

  # 예측 구간만 추출
  fc_future <- fc |>
    filter(ds > max(df_prophet$ds)) |>
    mutate(period = format(as.Date(ds), "%Y-%m"))

  forecast_df <- tibble(
    feature_name = col_name,
    model_type   = "Prophet",
    period       = fc_future$period,
    forecast     = fc_future$yhat,
    lo_95        = fc_future$yhat_lower,
    hi_95        = fc_future$yhat_upper,
    lo_80        = fc_future$yhat_lower + 0.4375 * (fc_future$yhat - fc_future$yhat_lower),
    hi_80        = fc_future$yhat_upper - 0.4375 * (fc_future$yhat_upper - fc_future$yhat),
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )

  # 변화점 정보
  cp_dates <- m$changepoints
  n_cp     <- length(cp_dates)

  model_df <- tibble(
    feature_name        = col_name,
    model_type          = "Prophet",
    n_changepoints_fit  = n_cp,
    n_changepoints_cfg  = PROPHET_CHANGEPOINTS,
    trend_type          = "linear",
    yearly_seasonality  = TRUE,
    holidays            = "KR",
    run_id              = RUN_ID,
    analyzed_at         = Sys.time()
  )

  message(glue(
    "  [Prophet] {col_name}: 변화점 {n_cp}개  ",
    "예측범위 {min(fc_future$period)}~{max(fc_future$period)}"
  ))

  list(forecast = forecast_df, model = model_df)
}

# ── 전체 실행 ─────────────────────────────────────────────────────────────
message("[06] ARIMA 실행 중...")
arima_results  <- map(FORECAST_TARGETS, run_arima)
names(arima_results) <- FORECAST_TARGETS

message("[06] Prophet 실행 중...")
prophet_results <- map(FORECAST_TARGETS, run_prophet)
names(prophet_results) <- FORECAST_TARGETS

arima_forecast   <- map_dfr(arima_results,   ~ .x$forecast)
arima_models     <- map_dfr(arima_results,   ~ .x$model)
prophet_forecast <- map_dfr(prophet_results, ~ .x$forecast)
prophet_models   <- map_dfr(prophet_results, ~ .x$model)

all_models <- bind_rows(
  arima_models   |> mutate(across(everything(), as.character)),
  prophet_models |> mutate(across(everything(), as.character))
)

message(glue(
  "[06] 예측 완료  ARIMA: {nrow(arima_forecast)}행  ",
  "Prophet: {nrow(prophet_forecast)}행"
))

if (nrow(arima_forecast) > 0) {
  bq_replace_run(arima_forecast, "analysis_forecast_arima", RUN_ID)
} else {
  message("[06] arima_forecast 결과 없음 — BQ 저장 건너뜀")
}

if (nrow(prophet_forecast) > 0) {
  bq_replace_run(prophet_forecast, "analysis_forecast_prophet", RUN_ID)
} else {
  message("[06] prophet_forecast 결과 없음 — BQ 저장 건너뜀")
}

if (nrow(all_models) > 0) {
  bq_replace_run(all_models, "analysis_forecast_models", RUN_ID)
} else {
  message("[06] all_models 결과 없음 — BQ 저장 건너뜀")
}

message("[06] 예측 모델 완료")

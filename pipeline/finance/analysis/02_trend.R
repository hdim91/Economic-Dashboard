# ============================================================================
# 02_trend.R
# 금융동향 핵심 지표 추세 분석
#
# 분석 항목:
#   - MoM (전월 대비), YoY (전년동월 대비) — 절대변화 + 변화율(%)
#   - 방향성 (연속 상승/하락 개월 수)
#   - 금리·물가·환율·유동성 그룹별 최신 스냅샷
#
# BQ 저장:
#   fin_analysis_trend_series   : 전체 시계열 (MoM/YoY 포함, 차트용)
#   fin_analysis_trend_summary  : 지표별 최신 스냅샷 (대시보드 KPI용)
# ============================================================================

source("00_config.R")
source("01_load_data.R")

suppressPackageStartupMessages({
  library(purrr)
})

message("[02] 추세 분석 시작")

# ── 분석 대상 지표 정의 ────────────────────────────────────────────────────
TREND_VARS <- list(
  # variable_name,            label,                        group,      unit
  list(v="kr_base_rate",       l="한국 기준금리",             g="금리",  u="%"),
  list(v="kr_govbond_3y",      l="한국 국고채 3년",           g="금리",  u="%"),
  list(v="kr_govbond_10y",     l="한국 국고채 10년",          g="금리",  u="%"),
  list(v="kr_cd_91d",          l="CD 91일",                  g="금리",  u="%"),
  list(v="us_fed_funds_rate",  l="미국 연방기금금리",         g="금리",  u="%"),
  list(v="us_treasury_10y",    l="미국 국채 10년",            g="금리",  u="%"),
  list(v="us_treasury_2y",     l="미국 국채 2년",             g="금리",  u="%"),
  list(v="us_yield_spread",    l="미국 장단기 스프레드",      g="금리",  u="%"),
  list(v="kr_cpi",             l="한국 CPI",                 g="물가",  u="지수"),
  list(v="kr_ppi",             l="한국 PPI",                 g="물가",  u="지수"),
  list(v="us_cpi",             l="미국 CPI",                 g="물가",  u="지수"),
  list(v="us_pce",             l="미국 PCE",                 g="물가",  u="지수"),
  list(v="kr_usd_rate",        l="원달러 환율",              g="환율",  u="원/달러"),
  list(v="kr_m2",              l="한국 M2 통화량",            g="유동성", u="십억원"),
  list(v="kr_household_credit",l="가계신용 잔액",             g="유동성", u="십억원"),
  list(v="kr_house_price_sale",l="한국 주택매매가격지수",     g="부동산", u="지수"),
  list(v="us_cs_hpi",          l="미국 케이스실러 HPI",       g="부동산", u="지수"),
  list(v="us_mortgage_30y",    l="미국 모기지금리 30년",      g="금리",  u="%")
)

# variable_name → 메타 조회용 named list
var_meta <- setNames(
  lapply(TREND_VARS, function(x) list(label=x$l, group=x$g, unit=x$u)),
  sapply(TREND_VARS, `[[`, "v")
)

# ── 방향성: 연속 증가/감소 개월 수 ────────────────────────────────────────
direction_streak <- function(vals) {
  vals <- vals[!is.na(vals)]
  if (length(vals) == 0) return(0L)
  last_sign <- sign(tail(vals, 1))
  streak <- 0L
  for (v in rev(vals)) {
    if (sign(v) == last_sign) streak <- streak + 1L
    else break
  }
  streak * last_sign  # 양수=증가, 음수=감소
}

# ── 시계열 계산 함수 ───────────────────────────────────────────────────────
compute_trend <- function(var_name) {
  if (!var_name %in% names(fin_wide)) return(NULL)

  meta <- var_meta[[var_name]]
  df <- fin_wide |>
    select(period, period_date, value = all_of(var_name)) |>
    arrange(period_date) |>
    mutate(
      mom_chg  = value - lag(value, 1),
      mom_pct  = (value / lag(value, 1) - 1) * 100,
      yoy_chg  = value - lag(value, 12),
      yoy_pct  = (value / lag(value, 12) - 1) * 100,
      variable_name = var_name,
      label    = meta$label,
      group    = meta$group,
      unit     = meta$unit
    ) |>
    filter(!is.na(value))

  df
}

# ── 1. 시계열 계산 ─────────────────────────────────────────────────────────
series_list <- map(names(var_meta), compute_trend)
trend_series <- bind_rows(compact(series_list)) |>
  mutate(run_id = RUN_ID, analyzed_at = Sys.time())

n_vars <- n_distinct(trend_series$variable_name)
message(glue("[02] 시계열 계산: {nrow(trend_series)}행 ({n_vars}개 지표)"))

# ── 2. 최신 스냅샷 요약 ────────────────────────────────────────────────────
trend_summary <- trend_series |>
  arrange(variable_name, period_date) |>
  group_by(variable_name, label, group, unit) |>
  summarise(
    latest_period   = max(period),
    latest_value    = last(value[!is.na(value)]),
    mom_chg         = last(mom_chg[!is.na(mom_chg)]),
    mom_pct         = last(mom_pct[!is.na(mom_pct)]),
    yoy_chg         = last(yoy_chg[!is.na(yoy_chg)]),
    yoy_pct         = last(yoy_pct[!is.na(yoy_pct)]),
    streak_months   = direction_streak(mom_chg),
    n_positive_12m  = sum(mom_chg > 0, na.rm = TRUE),
    .groups = "drop"
  ) |>
  mutate(
    direction    = case_when(
      mom_chg > 0 ~ "상승",
      mom_chg < 0 ~ "하락",
      TRUE        ~ "보합"
    ),
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )

message(glue("[02] 요약 스냅샷: {nrow(trend_summary)}개 지표"))

# ── 3. BQ 저장 ─────────────────────────────────────────────────────────────
bq_replace_run(
  trend_series |> select(
    period, period_date, variable_name, label, group, unit,
    value, mom_chg, mom_pct, yoy_chg, yoy_pct,
    run_id, analyzed_at
  ),
  "fin_analysis_trend_series", RUN_ID
)

bq_replace_run(trend_summary, "fin_analysis_trend_summary", RUN_ID)

message("[02] 추세 분석 완료")

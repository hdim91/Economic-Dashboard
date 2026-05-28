# ============================================================================
# 03_kr_us_spread.R
# 한미 금리·물가 스프레드 분석
#
# 분석 항목:
#   - 한미 기준금리 스프레드 (KR 기준금리 - US 연방기금금리)
#   - 한미 국채 10년 스프레드
#   - 한미 CPI 갭
#   - 환율-금리 연관성 (스프레드 vs 원달러 환율)
#
# BQ 저장:
#   fin_analysis_kr_us_spread   : 스프레드 시계열
#   fin_analysis_kr_us_summary  : 최신 스프레드 스냅샷
# ============================================================================

source("00_config.R")
source("01_load_data.R")

message("[03] 한미 스프레드 분석 시작")

# ── 스프레드 정의 ──────────────────────────────────────────────────────────
SPREADS <- list(
  list(
    name   = "spread_policy_rate",
    kr_var = "kr_base_rate",
    us_var = "us_fed_funds_rate",
    label  = "한미 기준금리 스프레드 (KR-US)",
    unit   = "%p"
  ),
  list(
    name   = "spread_bond_10y",
    kr_var = "kr_govbond_10y",
    us_var = "us_treasury_10y",
    label  = "한미 국채 10년 스프레드 (KR-US)",
    unit   = "%p"
  ),
  list(
    name   = "spread_cpi",
    kr_var = "kr_cpi",
    us_var = "us_cpi",
    label  = "한미 CPI 갭 (YoY% 기준)",
    unit   = "%p"
  )
)

# ── 스프레드 계산 ──────────────────────────────────────────────────────────
compute_spread <- function(sp_def) {
  kr <- sp_def$kr_var
  us <- sp_def$us_var

  if (!kr %in% names(fin_wide) || !us %in% names(fin_wide)) {
    message(glue("[03] {sp_def$name}: 데이터 없음 ({kr} or {us}) — 건너뜀"))
    return(NULL)
  }

  df <- fin_wide |>
    select(period, period_date, kr_val = all_of(kr), us_val = all_of(us)) |>
    filter(!is.na(kr_val), !is.na(us_val)) |>
    mutate(
      # CPI는 YoY% 기준 스프레드 계산
      kr_use = if (sp_def$name == "spread_cpi") {
        (kr_val / lag(kr_val, 12) - 1) * 100
      } else kr_val,
      us_use = if (sp_def$name == "spread_cpi") {
        (us_val / lag(us_val, 12) - 1) * 100
      } else us_val,
      spread = kr_use - us_use,
      spread_name = sp_def$name,
      label       = sp_def$label,
      unit        = sp_def$unit
    ) |>
    filter(!is.na(spread)) |>
    arrange(period_date)

  df
}

spread_series_list <- lapply(SPREADS, compute_spread)
spread_series <- bind_rows(Filter(Negate(is.null), spread_series_list))

# ── 환율 컬럼 추가 (스프레드-환율 연관 분석용) ──────────────────────────────
if ("kr_usd_rate" %in% names(fin_wide)) {
  usd_df <- fin_wide |> select(period, period_date, kr_usd_rate)
  spread_series <- spread_series |>
    left_join(usd_df, by = c("period", "period_date"))
} else {
  spread_series <- spread_series |> mutate(kr_usd_rate = NA_real_)
}

spread_series <- spread_series |>
  mutate(run_id = RUN_ID, analyzed_at = Sys.time())

message(glue(
  "[03] 스프레드 시계열: {nrow(spread_series)}행 ",
  "({n_distinct(spread_series$spread_name)}개 시리즈)"
))

# ── 최신 스냅샷 요약 ─────────────────────────────────────────────────────
spread_summary <- spread_series |>
  arrange(spread_name, period_date) |>
  group_by(spread_name, label, unit) |>
  summarise(
    latest_period  = max(period),
    latest_spread  = last(spread[!is.na(spread)]),
    prev_spread    = nth(spread[!is.na(spread)], -2),
    spread_12m_avg = mean(tail(spread[!is.na(spread)], 12), na.rm = TRUE),
    spread_12m_min = min(tail(spread[!is.na(spread)], 12), na.rm = TRUE),
    spread_12m_max = max(tail(spread[!is.na(spread)], 12), na.rm = TRUE),
    latest_usd     = last(kr_usd_rate[!is.na(kr_usd_rate)]),
    .groups = "drop"
  ) |>
  mutate(
    spread_direction = case_when(
      latest_spread > prev_spread ~ "확대",
      latest_spread < prev_spread ~ "축소",
      TRUE                        ~ "보합"
    ),
    run_id      = RUN_ID,
    analyzed_at = Sys.time()
  )

message(glue("[03] 스프레드 요약: {nrow(spread_summary)}개"))

# ── BQ 저장 ───────────────────────────────────────────────────────────────
bq_replace_run(
  spread_series |> select(
    period, period_date, spread_name, label, unit,
    kr_val, us_val, spread, kr_usd_rate,
    run_id, analyzed_at
  ),
  "fin_analysis_kr_us_spread", RUN_ID
)

bq_replace_run(spread_summary, "fin_analysis_kr_us_summary", RUN_ID)

message("[03] 한미 스프레드 분석 완료")

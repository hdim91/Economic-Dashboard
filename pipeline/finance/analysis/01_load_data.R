# ============================================================================
# 01_load_data.R
# finance_raw_dedup View에서 분석용 데이터 로드 및 전처리
#
# 출력:
#   fin_long   : long 형식 tibble (source / variable_name / period / value)
#   fin_wide   : wide 형식 tibble (period_date × variable_name 피벗)
#   ts_list    : variable_name별 ts 객체 (monthly, frequency=12)
# ============================================================================

source("00_config.R")

suppressPackageStartupMessages({
  library(tidyr)
  library(zoo)
})

message("[01] 금융동향 데이터 로드 시작")

# ── 1. finance_raw_dedup에서 long 형식으로 로드 ──────────────────────────────
fin_long_raw <- bq_query(glue(
  "SELECT
     source, period, period_date, variable_name,
     category_key, value, adjustment_type, tbl_id
   FROM `{BQ_PROJECT}.{BQ_DATASET}.{DEDUP_VIEW}`
   WHERE period >= '{PERIOD_START}'
     AND period <= '{PERIOD_END}'
     AND value IS NOT NULL
   ORDER BY variable_name, period"
))

message(glue("[01] 로드: {nrow(fin_long_raw)}행 ({n_distinct(fin_long_raw$variable_name)}개 시계열)"))

# period_date → Date
fin_long <- fin_long_raw |>
  mutate(
    period_date = as.Date(period_date),
    period      = as.character(period)
  ) |>
  arrange(variable_name, period_date)

# ── 2. wide 형식으로 피벗 ────────────────────────────────────────────────────
# variable_name별로 월 기준 평균 (분기 데이터도 period 기준으로 맞춤)
fin_wide_raw <- fin_long |>
  group_by(period, period_date, variable_name) |>
  summarise(value = mean(value, na.rm = TRUE), .groups = "drop") |>
  pivot_wider(
    names_from  = variable_name,
    values_from = value
  ) |>
  arrange(period_date)

# 결측 선형 보간 (최대 2개월 이내)
numeric_cols <- setdiff(names(fin_wide_raw), c("period", "period_date"))
fin_wide <- fin_wide_raw |>
  mutate(across(
    all_of(numeric_cols),
    ~ zoo::na.approx(.x, maxgap = 2, na.rm = FALSE)
  ))

n_cols   <- length(numeric_cols)
n_rows   <- nrow(fin_wide)
n_na_bf  <- sum(is.na(select(fin_wide_raw, all_of(numeric_cols))))
n_na_af  <- sum(is.na(select(fin_wide,     all_of(numeric_cols))))

message(glue(
  "[01] wide 완료: {n_rows}행 × {n_cols}개 시계열  ",
  "결측 보간: {n_na_bf}→{n_na_af}개"
))

# ── 3. ts 객체 목록 생성 ─────────────────────────────────────────────────────
start_ym <- as.integer(strsplit(
  format(min(fin_wide$period_date), "%Y-%m"), "-"
)[[1]])

ts_list <- lapply(numeric_cols, function(col) {
  ts(fin_wide[[col]], start = start_ym, frequency = 12)
})
names(ts_list) <- numeric_cols

n_complete <- sum(sapply(ts_list, function(x) sum(!is.na(x)) >= 24))
message(glue(
  "[01] ts 생성: {length(ts_list)}개  (24개월 이상 유효: {n_complete}개)"
))

message("[01] 데이터 로드 완료")

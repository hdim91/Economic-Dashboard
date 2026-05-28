# ============================================================================
# 01_load_features.R
# Feature Store에서 분석용 데이터 로드 및 전처리
#
# 출력:
#   fs_wide_clean  : 분석용 wide tibble (period_date 기준 정렬, 결측 보간)
#   fs_long_clean  : 분석용 long tibble (뉴스 피처 제외)
#   feature_meta   : 피처 메타데이터 (카탈로그)
#   ts_list        : 피처별 ts 객체 목록 (STL/ARIMA 입력용)
# ============================================================================

source("00_config.R")

suppressPackageStartupMessages({
  library(tidyr)
  library(zoo)
  library(tsibble)
})

message("[01] Feature Store 로드 시작")

# ── 1. 피처 카탈로그 로드 ──────────────────────────────────────────────────
feature_meta <- bq_query(glue(
  "SELECT feature_name, feature_group, unit, description_ko
   FROM `{BQ_PROJECT}.{BQ_DATASET}.fs_feature_catalog`
   ORDER BY feature_group, feature_name"
)) |>
  filter(!feature_name %in% NEWS_FEATURES)   # 뉴스 피처 제외

message(glue("[01] 카탈로그 로드: {nrow(feature_meta)}개 피처"))

# ── 2. fs_long 로드 (분석 기간 필터) ─────────────────────────────────────
fs_long_raw <- bq_query(glue(
  "SELECT period, period_date, feature_name, feature_group, unit, value
   FROM `{BQ_PROJECT}.{BQ_DATASET}.fs_long`
   WHERE period >= '{PERIOD_START}'
     AND period <= '{PERIOD_END}'
     AND feature_name NOT IN ({paste0(\"'\", NEWS_FEATURES, \"'\", collapse=',')})
   ORDER BY feature_name, period"
))

message(glue("[01] fs_long 로드: {nrow(fs_long_raw)}행"))

# ── 3. period_date 변환 및 정렬 ──────────────────────────────────────────
fs_long_clean <- fs_long_raw |>
  mutate(
    period_date = as.Date(period_date),
    period      = as.character(period)
  ) |>
  arrange(feature_name, period_date)

# ── 4. fs_wide 로드 및 결측 보간 ─────────────────────────────────────────
# news 컬럼은 SELECT 에서 제외
news_cols_sql <- paste0(NEWS_FEATURES, collapse = ", ")

# 동적으로 뉴스 컬럼 제외한 fs_wide 쿼리
fs_wide_raw <- bq_query(glue(
  "SELECT * EXCEPT ({news_cols_sql})
   FROM `{BQ_PROJECT}.{BQ_DATASET}.fs_wide`
   WHERE period >= '{PERIOD_START}'
     AND period <= '{PERIOD_END}'
   ORDER BY period"
))

message(glue("[01] fs_wide 로드: {nrow(fs_wide_raw)}행 × {ncol(fs_wide_raw)}열"))

# period_date → Date
fs_wide_clean <- fs_wide_raw |>
  mutate(period_date = as.Date(period_date)) |>
  arrange(period_date)

# 결측값 처리: 선형 보간 (na.approx) — 최대 2개월 이내만 보간
numeric_cols <- setdiff(names(fs_wide_clean), c("period", "period_date"))

fs_wide_clean <- fs_wide_clean |>
  mutate(across(
    all_of(numeric_cols),
    ~ zoo::na.approx(.x, maxgap = 2, na.rm = FALSE)
  ))

n_missing_before <- sum(is.na(fs_wide_raw |> select(all_of(numeric_cols))))
n_missing_after  <- sum(is.na(fs_wide_clean |> select(all_of(numeric_cols))))
message(glue(
  "[01] 결측 보간: {n_missing_before}개 → {n_missing_after}개 ",
  "(2개월 초과 공백은 NA 유지)"
))

# ── 5. ts 객체 목록 생성 ─────────────────────────────────────────────────
# 각 피처를 monthly ts 객체로 변환 (STL, ARIMA, ACF 입력용)
start_ym <- as.integer(strsplit(PERIOD_START, "-")[[1]])

ts_list <- lapply(numeric_cols, function(col) {
  vec <- fs_wide_clean[[col]]
  ts(vec, start = start_ym, frequency = 12)
})
names(ts_list) <- numeric_cols

n_complete <- sum(sapply(ts_list, function(x) sum(!is.na(x)) >= 24))
message(glue(
  "[01] ts 객체 생성: {length(ts_list)}개 피처 ",
  "(24개월 이상 유효: {n_complete}개)"
))

message("[01] Feature Store 로드 완료")

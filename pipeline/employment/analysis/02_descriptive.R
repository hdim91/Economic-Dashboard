# ============================================================================
# 02_descriptive.R
# 기술통계 분석
#
# 분석 항목:
#   - 피처별: 평균, 중앙값, 표준편차, 왜도, 첨도, 최솟값, 최댓값, IQR
#   - 분포: Shapiro-Wilk 정규성 검정 (n ≤ 50)
#   - 이상치: IQR × 1.5 기준 이상치 개월 수 및 해당 period
#
# BQ 저장:
#   analysis_descriptive_stats  : 피처별 요약 통계
#   analysis_outlier_periods    : 이상치 탐지 결과
# ============================================================================

source("00_config.R")
source("01_load_features.R")

suppressPackageStartupMessages({
  library(moments)   # skewness, kurtosis
  library(purrr)
})

message("[02] 기술통계 분석 시작")

# ── 1. 피처별 기술통계 ───────────────────────────────────────────────────
compute_stats <- function(col_name) {
  x <- fs_wide_clean[[col_name]]
  x_clean <- x[!is.na(x)]
  n <- length(x_clean)
  if (n < 3) return(NULL)

  q <- quantile(x_clean, probs = c(0.25, 0.75))
  iqr_val <- IQR(x_clean)

  # 정규성 검정 (n ≤ 50만 수행, 초과 시 NA)
  sw_p <- if (n <= 50) {
    tryCatch(shapiro.test(x_clean)$p.value, error = function(e) NA_real_)
  } else NA_real_

  tibble(
    feature_name = col_name,
    n            = n,
    n_missing    = sum(is.na(x)),
    mean         = mean(x_clean),
    median       = median(x_clean),
    sd           = sd(x_clean),
    cv           = sd(x_clean) / abs(mean(x_clean)),   # 변동계수
    skewness     = moments::skewness(x_clean),
    kurtosis     = moments::kurtosis(x_clean),
    min          = min(x_clean),
    max          = max(x_clean),
    q25          = q[1],
    q75          = q[2],
    iqr          = iqr_val,
    shapiro_p    = sw_p,
    is_normal    = if (!is.na(sw_p)) sw_p >= 0.05 else NA,
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )
}

desc_stats <- map_dfr(numeric_cols, compute_stats)

# 피처 메타 JOIN
desc_stats <- desc_stats |>
  left_join(feature_meta |> select(feature_name, feature_group, unit, description_ko),
            by = "feature_name")

message(glue("[02] 기술통계 완료: {nrow(desc_stats)}개 피처"))

# ── 2. 이상치 탐지 (IQR × 1.5) ───────────────────────────────────────────
detect_outliers <- function(col_name) {
  x     <- fs_wide_clean[[col_name]]
  dates <- fs_wide_clean$period

  x_clean <- x[!is.na(x)]
  if (length(x_clean) < 4) return(NULL)

  q    <- quantile(x_clean, c(0.25, 0.75))
  lo   <- q[1] - 1.5 * IQR(x_clean)
  hi   <- q[2] + 1.5 * IQR(x_clean)

  is_out <- !is.na(x) & (x < lo | x > hi)

  if (!any(is_out)) return(NULL)

  tibble(
    feature_name   = col_name,
    period         = dates[is_out],
    value          = x[is_out],
    lower_fence    = lo,
    upper_fence    = hi,
    direction      = ifelse(x[is_out] < lo, "low", "high"),
    run_id         = RUN_ID,
    analyzed_at    = Sys.time()
  )
}

outlier_periods <- map_dfr(numeric_cols, detect_outliers)

message(glue("[02] 이상치 탐지: {nrow(outlier_periods)}건 (총 {length(unique(outlier_periods$feature_name))}개 피처)"))

# ── 3. BQ 저장 ─────────────────────────────────────────────────────────
bq_replace_run(desc_stats,     "analysis_descriptive_stats", RUN_ID)
bq_replace_run(outlier_periods, "analysis_outlier_periods",  RUN_ID)

message("[02] 기술통계 분석 완료")

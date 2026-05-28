# ============================================================================
# 07_yoy.R
# 전년동월 대비 분석 (YoY 절대 변화량 + YoY%)
#
# 분석 항목:
#   - 전체/남성/여성/청년/계절조정 취업자 전년동월차 (만명) 및 전년비 (%)
#   - 임금·근로시간·구직급여 전년동월차
#   - 미국 비농업 취업자 전년동월차
#   - 최근 trend: 전년동월차 방향성 (증가/감소 연속 개월 수)
#
# BQ 저장:
#   analysis_yoy_summary  : 피처별 최신 YoY 절대차 + YoY% + 방향성
#   analysis_yoy_series   : 전체 시계열 (차트용)
# ============================================================================

source("00_config.R")
source("01_load_features.R")

suppressPackageStartupMessages({
  library(purrr)
})

message("[07] YoY 전년동월 분석 시작")

# ── 분석 대상 피처 정의 ────────────────────────────────────────────────────
YOY_FEATURES <- list(
  # feature_value(원값),  feature_yoy(절대차),  feature_yoy_pct,  label,          unit
  list(val="kr_employed_total_sa_value",    yoy="kr_employed_total_sa_yoy",    pct="kr_employed_total_sa_yoy_pct",    label="전체취업자(계절조정)",   unit="만명"),
  list(val="kr_employed_male_total_value",  yoy=NA,                            pct=NA,                                label="남성취업자(원계열)",     unit="만명"),
  list(val="kr_employed_female_total_value",yoy=NA,                            pct=NA,                                label="여성취업자(원계열)",     unit="만명"),
  list(val="kr_employed_youth_value",       yoy="kr_employed_youth_yoy",       pct="kr_employed_youth_yoy_pct",       label="청년취업자(15~29)",      unit="만명"),
  list(val="kr_employed_mfg_sa_value",      yoy="kr_employed_mfg_sa_yoy",      pct="kr_employed_mfg_sa_yoy_pct",      label="제조업취업자(계절조정)", unit="만명"),
  list(val="kr_employed_service_sa_value",  yoy="kr_employed_service_sa_yoy",  pct="kr_employed_service_sa_yoy_pct",  label="서비스업취업자(계절조정)",unit="만명"),
  list(val="kr_wage_total_value",           yoy="kr_wage_total_yoy",           pct="kr_wage_total_yoy_pct",           label="월평균임금",             unit="원"),
  list(val="kr_jobseeker_new_value",        yoy="kr_jobseeker_new_yoy",        pct="kr_jobseeker_new_yoy_pct",        label="구직급여신규신청",        unit="명"),
  list(val="us_nonfarm_total_sa_value",     yoy="us_nonfarm_total_sa_yoy",     pct="us_nonfarm_total_sa_yoy_pct",     label="미국비농업취업자",        unit="천명")
)

# ── 1. YoY 시계열 계산 ────────────────────────────────────────────────────
# fs_wide_clean에서 직접 계산 (yoy 피처가 아직 BQ에 없을 수 있으므로 자체 계산도 병행)
compute_yoy_series <- function(feat) {
  val_col <- feat$val

  if (!val_col %in% names(fs_wide_clean)) return(NULL)

  df <- fs_wide_clean |>
    select(period, period_date, value = all_of(val_col)) |>
    arrange(period_date) |>
    mutate(
      yoy_chg = value - lag(value, 12),
      yoy_pct = (value / lag(value, 12) - 1) * 100,
      feature_name  = val_col,
      label         = feat$label,
      unit          = feat$unit
    ) |>
    filter(!is.na(yoy_chg))

  df
}

yoy_series_list <- map(YOY_FEATURES, compute_yoy_series)
yoy_series <- bind_rows(compact(yoy_series_list)) |>
  mutate(run_id = RUN_ID, analyzed_at = Sys.time())

message(glue("[07] YoY 시계열: {nrow(yoy_series)}행 ({n_distinct(yoy_series$feature_name)}개 피처)"))

# ── 2. YoY 요약 (최신 기준월 기준) ───────────────────────────────────────
latest_p <- max(yoy_series$period)

# 방향성: 연속 증가/감소 개월 수
direction_streak <- function(vals) {
  if (length(vals) == 0) return(0L)
  last_sign <- sign(tail(vals, 1))
  streak <- 0L
  for (v in rev(vals)) {
    if (sign(v) == last_sign && !is.na(v)) streak <- streak + 1L
    else break
  }
  streak * last_sign  # 양수=증가, 음수=감소
}

yoy_summary <- yoy_series |>
  arrange(feature_name, period_date) |>
  group_by(feature_name, label, unit) |>
  summarise(
    latest_period   = max(period),
    latest_value    = last(value[!is.na(value)]),
    latest_yoy_chg  = last(yoy_chg[!is.na(yoy_chg)]),
    latest_yoy_pct  = last(yoy_pct[!is.na(yoy_pct)]),
    prev_yoy_chg    = nth(yoy_chg[!is.na(yoy_chg)], -2),
    streak_months   = direction_streak(yoy_chg),
    n_positive_12m  = sum(yoy_chg > 0, na.rm = TRUE),
    n_negative_12m  = sum(yoy_chg < 0, na.rm = TRUE),
    .groups = "drop"
  ) |>
  mutate(
    direction       = case_when(
      latest_yoy_chg > 0 ~ "증가",
      latest_yoy_chg < 0 ~ "감소",
      TRUE               ~ "보합"
    ),
    run_id       = RUN_ID,
    analyzed_at  = Sys.time()
  )

message(glue("[07] YoY 요약: {nrow(yoy_summary)}개 피처"))

# ── 3. BQ 저장 ────────────────────────────────────────────────────────────
bq_replace_run(
  yoy_series |> select(period, period_date, feature_name, label, unit,
                        value, yoy_chg, yoy_pct, run_id, analyzed_at),
  "analysis_yoy_series", RUN_ID
)

bq_replace_run(yoy_summary, "analysis_yoy_summary", RUN_ID)

message("[07] YoY 전년동월 분석 완료")

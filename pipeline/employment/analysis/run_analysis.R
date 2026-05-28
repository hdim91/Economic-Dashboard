# ============================================================================
# run_analysis.R
# Analysis Layer 오케스트레이터 — 전체 분석 순차 실행
#
# 실행:
#   Rscript run_analysis.R                   # 전체
#   Rscript run_analysis.R --steps 02,03     # 특정 단계만
#   Rscript run_analysis.R --skip 06         # 특정 단계 제외
#
# 실행 순서:
#   01 Feature 로드 (공통 전처리)
#   02 기술통계
#   03 STL 분해
#   04 ACF/PACF
#   05 CCF (교차상관)
#   06 ARIMA + Prophet 예측
# ============================================================================

suppressPackageStartupMessages(library(glue))

# ── CLI 인자 파싱 ─────────────────────────────────────────────────────────
args     <- commandArgs(trailingOnly = TRUE)

parse_steps_arg <- function(args, flag) {
  idx <- which(args == flag)
  if (length(idx) == 0) return(NULL)
  strsplit(args[idx + 1], ",")[[1]]
}

steps_only <- parse_steps_arg(args, "--steps")
steps_skip <- parse_steps_arg(args, "--skip")

ALL_STEPS <- c("02", "03", "04", "05", "06", "07")

run_steps <- if (!is.null(steps_only)) {
  intersect(steps_only, ALL_STEPS)
} else if (!is.null(steps_skip)) {
  setdiff(ALL_STEPS, steps_skip)
} else {
  ALL_STEPS
}

message("=" |> strrep(70))
message(glue("[Analysis] 실행 단계: {paste(run_steps, collapse=', ')}"))
message("=" |> strrep(70))

# ── 실행 함수 ─────────────────────────────────────────────────────────────
run_step <- function(step_id, script, label) {
  if (!step_id %in% run_steps) {
    message(glue("[{step_id}] SKIP — {label}"))
    return(invisible(NULL))
  }
  t_start <- proc.time()["elapsed"]
  message(glue("\n[{step_id}] START — {label}"))
  tryCatch(
    source(script, local = FALSE),
    error = function(e) {
      message(glue("[{step_id}] FAILED: {conditionMessage(e)}"))
      stop(e)   # 상위로 전파 → 전체 파이프라인 중단
    }
  )
  elapsed <- round(proc.time()["elapsed"] - t_start, 1)
  message(glue("[{step_id}] DONE  ({elapsed}초)"))
}

# ── run_id 통일: PIPELINE_RUN_ID 없으면 여기서 한 번만 생성 ──────────────
if (nchar(Sys.getenv("PIPELINE_RUN_ID")) == 0) {
  Sys.setenv(PIPELINE_RUN_ID = format(Sys.time(), "%Y%m%dT%H%M%SZ"))
}
message(glue("[Analysis] run_id = {Sys.getenv('PIPELINE_RUN_ID')}"))

# ── 01: Feature 로드는 항상 실행 ──────────────────────────────────────────
t0 <- proc.time()["elapsed"]
message("\n[01] START — Feature Store 로드")
source("01_load_features.R", local = FALSE)
message(glue("[01] DONE  ({round(proc.time()['elapsed'] - t0, 1)}초)"))

# ── 02~06 순차 실행 ──────────────────────────────────────────────────────
steps_cfg <- list(
  list(id = "02", script = "02_descriptive.R", label = "기술통계"),
  list(id = "03", script = "03_stl_decomp.R",  label = "STL 분해"),
  list(id = "04", script = "04_acf_pacf.R",    label = "ACF/PACF"),
  list(id = "05", script = "05_ccf.R",         label = "CCF 교차상관"),
  list(id = "06", script = "06_forecast.R",    label = "ARIMA + Prophet 예측"),
  list(id = "07", script = "07_yoy.R",          label = "YoY 전년동월 분석")
)

t_pipeline_start <- proc.time()["elapsed"]
for (cfg in steps_cfg) {
  run_step(cfg$id, cfg$script, cfg$label)
}

total_elapsed <- round(proc.time()["elapsed"] - t_pipeline_start, 1)
message(glue("\n{'=' |> strrep(70)}"))
message(glue("[Analysis] 완료  총 소요: {total_elapsed}초  run_id={RUN_ID}"))
message('=' |> strrep(70))

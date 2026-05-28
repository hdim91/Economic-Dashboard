# ============================================================================
# run_analysis.R
# 금융동향 분석 오케스트레이터
#
# 실행:
#   Rscript run_analysis.R                   # 전체
#   Rscript run_analysis.R --steps 02,03     # 특정 단계만
#   Rscript run_analysis.R --skip 03         # 특정 단계 제외
#
# 실행 순서:
#   01 데이터 로드  (finance_raw_dedup → long/wide tibble)
#   02 추세 분석    (MoM/YoY + 방향성)
#   03 한미 스프레드 분석
# ============================================================================

suppressPackageStartupMessages(library(glue))

# ── CLI 인자 파싱 ─────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)

parse_steps_arg <- function(args, flag) {
  idx <- which(args == flag)
  if (length(idx) == 0) return(NULL)
  strsplit(args[idx + 1], ",")[[1]]
}

steps_only <- parse_steps_arg(args, "--steps")
steps_skip <- parse_steps_arg(args, "--skip")

ALL_STEPS <- c("02", "03")

run_steps <- if (!is.null(steps_only)) {
  intersect(steps_only, ALL_STEPS)
} else if (!is.null(steps_skip)) {
  setdiff(ALL_STEPS, steps_skip)
} else {
  ALL_STEPS
}

message("=" |> strrep(70))
message(glue("[Finance Analysis] 실행 단계: {paste(run_steps, collapse=', ')}"))
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
      stop(e)
    }
  )
  elapsed <- round(proc.time()["elapsed"] - t_start, 1)
  message(glue("[{step_id}] DONE  ({elapsed}초)"))
}

# ── run_id: PIPELINE_RUN_ID 없으면 여기서 생성 ───────────────────────────
if (nchar(Sys.getenv("PIPELINE_RUN_ID")) == 0) {
  Sys.setenv(PIPELINE_RUN_ID = format(Sys.time(), "%Y%m%dT%H%M%SZ"))
}
message(glue("[Finance Analysis] run_id = {Sys.getenv('PIPELINE_RUN_ID')}"))

# ── 01: 데이터 로드 — 항상 실행 ──────────────────────────────────────────
t0 <- proc.time()["elapsed"]
message("\n[01] START — 금융동향 데이터 로드")
source("01_load_data.R", local = FALSE)
message(glue("[01] DONE  ({round(proc.time()['elapsed'] - t0, 1)}초)"))

# ── 02~03 순차 실행 ──────────────────────────────────────────────────────
steps_cfg <- list(
  list(id = "02", script = "02_trend.R",         label = "핵심지표 추세 분석 (MoM/YoY)"),
  list(id = "03", script = "03_kr_us_spread.R",  label = "한미 금리·물가 스프레드 분석")
)

t_pipeline_start <- proc.time()["elapsed"]
for (cfg in steps_cfg) {
  run_step(cfg$id, cfg$script, cfg$label)
}

total_elapsed <- round(proc.time()["elapsed"] - t_pipeline_start, 1)
message(glue("\n{'=' |> strrep(70)}"))
message(glue(
  "[Finance Analysis] 완료  총 소요: {total_elapsed}초  ",
  "run_id={Sys.getenv('PIPELINE_RUN_ID')}"
))
message("=" |> strrep(70))

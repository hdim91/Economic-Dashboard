"""
transform.py
─────────────────────────────────────────────────────────────────────────────
Pipeline & Transform 레이어 — 수집 레코드 → 파생 지표 계산

━━━ 처리 흐름 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  validate_records()로 검증된 레코드 목록
        │
        ▼
  [1] records_to_dataframe()     dict list → pandas DataFrame
        │
        ▼
  [2] clean()                    결측·이상치 처리
        │   ├─ value=None → NaN 유지 (BQ에서 NULL로 적재)
        │   └─ IQR 3σ 이상치 → NaN 마킹 + 로그
        │
        ▼
  [3] compute_derived()          파생 지표 계산
        │   ├─ MoM 증감률   (전월 대비 %)
        │   ├─ YoY 증감률   (전년동월 대비 %)
        │   ├─ MA3  이동평균 (3개월)
        │   ├─ MA6  이동평균 (6개월)
        │   └─ MA12 이동평균 (12개월)
        │
        ▼
  [4] dataframe_to_records()     DataFrame → dict list (BQ 적재 형식)
        │
        ▼
  run_transform() 반환값
      {
        "transformed": list[dict],   # 원본 + 파생 지표 모두 포함
        "report":      dict          # 처리 통계
      }

━━━ variable_name 네이밍 규칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  원본:     {adjustment_type}_{base_name}
            예) raw_employed_by_sex_age
                seasonal_employed_by_industry

  파생:     {adjustment_type}_{base_name}__{metric}
            예) raw_employed_by_sex_age__mom_pct
                raw_employed_by_sex_age__yoy_pct
                raw_employed_by_sex_age__ma3
                seasonal_employed__ma12

  ※ 이중 언더스코어(__)로 원본 variable_name과 파생 metric을 구분

━━━ 파생 지표 세부 정의 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  mom_pct  : (value_t - value_{t-1}) / |value_{t-1}| × 100
             전월 대비 증감률(%). 전월 0이면 NaN.

  yoy_pct  : (value_t - value_{t-12}) / |value_{t-12}| × 100
             전년동월 대비 증감률(%). 전년동월 0이면 NaN.

  ma3      : 최근 3개월 단순이동평균 (현재 포함, min_periods=2)
  ma6      : 최근 6개월 단순이동평균 (min_periods=3)
  ma12     : 최근 12개월 단순이동평균 (min_periods=6)

━━━ adjustment_type 정책 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  - 원본 레코드: raw | seasonal | index → 그대로 유지
  - 파생 레코드(mom_pct, yoy_pct): adjustment_type = "derived_pct"
  - 파생 레코드(ma*):              adjustment_type = "derived_ma"
"""

import logging
import math
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

# ── 파생 지표 adjustment_type 상수 ─────────────────────────────────────────
ADJ_DERIVED_PCT = "derived_pct"
ADJ_DERIVED_MA  = "derived_ma"
ADJ_DERIVED_CHG = "derived_chg"

# 이동평균 설정: (suffix, window, min_periods)
MA_CONFIGS = [
    ("ma3",  3,  2),
    ("ma6",  6,  3),
    ("ma12", 12, 6),
]

# IQR 이상치 배수 (fence = Q1 - k*IQR, Q3 + k*IQR)
IQR_K = 3.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: dict list → DataFrame
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def records_to_dataframe(records: list[dict]) -> pd.DataFrame:
    """
    검증된 레코드 목록을 DataFrame으로 변환.
    period 컬럼을 datetime으로 파싱해 시계열 정렬에 활용.
    """
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # period_dt: 정렬 및 lag 계산용 datetime 컬럼 (BQ 적재 대상 아님)
    df["period_dt"] = pd.to_datetime(df["period"], format="%Y-%m")

    # value를 float으로 강제 변환 (None → NaN)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    return df


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: 결측·이상치 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    결측값 처리 및 IQR 기반 이상치 마킹.

    이상치 마킹 정책:
      - 그룹(source, variable_name, category_key)별 시계열로 분리
      - Q1 - IQR_K * IQR  또는  Q3 + IQR_K * IQR 초과 값 → NaN
      - 원본 값은 value_original 컬럼에 보존 (추적용)
      - 비율·지수 계열(variable_name에 'rate'/'pct'/'index'/'ratio' 포함)은
        이상치 처리 제외 (자연스러운 큰 변동 허용)

    Returns
    -------
    (cleaned_df, report)
    """
    if df.empty:
        return df, {"outlier_count": 0, "null_count": 0}

    df = df.copy()
    df["value_original"] = df["value"]   # 원본 보존

    null_before = df["value"].isna().sum()
    outlier_count = 0

    # 이상치 처리 제외 대상 패턴
    SKIP_OUTLIER_PATTERNS = ("rate", "pct", "index", "ratio", "participation")

    group_keys = ["source", "variable_name", "category_key"]

    for _, grp in df.groupby(group_keys, sort=False):
        vname = grp["variable_name"].iloc[0]

        # 비율·지수 계열 제외
        if any(pat in vname for pat in SKIP_OUTLIER_PATTERNS):
            continue

        vals = grp["value"].dropna()
        if len(vals) < 4:   # 관측치 부족 시 스킵
            continue

        q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue

        lower = q1 - IQR_K * iqr
        upper = q3 + IQR_K * iqr

        mask = (
            df.index.isin(grp.index)
            & df["value"].notna()
            & ((df["value"] < lower) | (df["value"] > upper))
        )
        if mask.any():
            n = mask.sum()
            outlier_count += n
            logger.debug(
                f"  [clean] 이상치 {n}건 마킹 — {vname} "
                f"(fence: {lower:.1f} ~ {upper:.1f})"
            )
            df.loc[mask, "value"] = float("nan")

    null_after = df["value"].isna().sum()
    report = {
        "null_count":    int(null_before),
        "outlier_count": int(outlier_count),
        "null_after":    int(null_after),
    }

    if outlier_count:
        logger.info(f"[Transform/clean] 이상치 마킹: {outlier_count}건")

    return df, report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: 파생 지표 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _pct_change(current: float, previous: float) -> float | None:
    """(current - previous) / |previous| × 100. previous=0이면 None."""
    if previous is None or math.isnan(previous) or previous == 0:
        return None
    if current is None or math.isnan(current):
        return None
    return (current - previous) / abs(previous) * 100.0


def compute_derived(
    df:      pd.DataFrame,
    run_id:  str,
) -> pd.DataFrame:
    """
    그룹(source, variable_name, category_key)별 시계열 정렬 후
    MoM%, YoY%, MA3/MA6/MA12 파생 레코드 생성.

    파생 레코드는 원본 레코드와 동일한 스키마를 공유하며,
    variable_name에 __{metric} suffix가 붙음.

    Returns
    -------
    파생 레코드만 담은 DataFrame (원본은 포함하지 않음)
    """
    if df.empty:
        return pd.DataFrame()

    ingested_at = datetime.now(timezone.utc).isoformat()
    derived_rows: list[dict] = []

    group_keys = ["source", "variable_name", "category_key"]

    for (source, vname, cat_key), grp in df.groupby(group_keys, sort=False):
        # 시계열 오름차순 정렬
        grp = grp.sort_values("period_dt").reset_index(drop=True)
        values = grp["value"].tolist()
        periods = grp["period"].tolist()

        # 원본 adjustment_type (같은 그룹 내 동일하다고 가정)
        orig_adj = grp["adjustment_type"].iloc[0]

        # ── 공통 메타 추출 ──────────────────────────────────────────────
        base_meta = {
            "source":        source,
            "category_key":  cat_key,
            "category_name": grp["category_name"].iloc[0] if "category_name" in grp else None,
            "ingested_at":   ingested_at,
            "run_id":        run_id,
            "tbl_id":        grp["tbl_id"].iloc[0] if "tbl_id" in grp.columns else None,
        }

        # ── MoM / YoY ─────────────────────────────────────────────────
        for i, (period, val) in enumerate(zip(periods, values)):
            val_f = val if (val is not None and not (isinstance(val, float) and math.isnan(val))) else None

            # MoM: lag 1
            if i >= 1:
                prev = values[i - 1]
                prev_f = prev if (prev is not None and not (isinstance(prev, float) and math.isnan(prev))) else None
                mom = _pct_change(val_f, prev_f)
                derived_rows.append({
                    **base_meta,
                    "period":          period,
                    "variable_name":   f"{vname}__mom_pct",
                    "value":           mom,
                    "adjustment_type": ADJ_DERIVED_PCT,
                })

            # YoY: lag 12
            if i >= 12:
                prev12 = values[i - 12]
                prev12_f = prev12 if (prev12 is not None and not (isinstance(prev12, float) and math.isnan(prev12))) else None
                yoy = _pct_change(val_f, prev12_f)
                derived_rows.append({
                    **base_meta,
                    "period":          period,
                    "variable_name":   f"{vname}__yoy_pct",
                    "value":           yoy,
                    "adjustment_type": ADJ_DERIVED_PCT,
                })
                # YoY 절대 변화량
                yoy_chg = (val_f - prev12_f) if (val_f is not None and prev12_f is not None) else None
                derived_rows.append({
                    **base_meta,
                    "period":          period,
                    "variable_name":   f"{vname}__yoy",
                    "value":           yoy_chg,
                    "adjustment_type": ADJ_DERIVED_CHG,
                })

        # ── 이동평균 (pandas rolling) ──────────────────────────────────
        s = grp.set_index("period_dt")["value"]

        for suffix, window, min_p in MA_CONFIGS:
            ma = s.rolling(window=window, min_periods=min_p).mean()
            for period, ma_val in zip(periods, ma.values):
                v = None if (math.isnan(ma_val) if isinstance(ma_val, float) else False) else float(ma_val)
                derived_rows.append({
                    **base_meta,
                    "period":          period,
                    "variable_name":   f"{vname}__{suffix}",
                    "value":           v,
                    "adjustment_type": ADJ_DERIVED_MA,
                })

    if not derived_rows:
        return pd.DataFrame()

    return pd.DataFrame(derived_rows)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: DataFrame → dict list
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def dataframe_to_records(df: pd.DataFrame) -> list[dict]:
    """
    DataFrame → BQ 적재용 dict list 변환.
    period_dt, value_original 등 내부 컬럼은 제거.
    NaN → None 변환.
    """
    if df.empty:
        return []

    drop_cols = [c for c in ["period_dt", "value_original"] if c in df.columns]
    df = df.drop(columns=drop_cols)

    records = df.where(pd.notna(df), None).to_dict(orient="records")
    return records


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run_transform(
    validated_records: list[dict],
    run_id:            str,
) -> dict:
    """
    Pipeline & Transform 레이어 통합 실행.

    Parameters
    ----------
    validated_records : validator.validate_records()를 통과한 레코드 목록
    run_id            : 현재 실행 UUID

    Returns
    -------
    {
        "transformed":  list[dict],  # 원본 + 파생 레코드 (BQ 적재 대상)
        "report": {
            "input_count":   int,    # 입력 레코드 수
            "clean": {...},          # 이상치/결측 처리 통계
            "derived_count": int,    # 생성된 파생 레코드 수
            "output_count":  int,    # 총 출력 레코드 수
        }
    }
    """
    logger.info(f"[Transform] 시작 — 입력 {len(validated_records):,}건")

    # ── Step 1: DataFrame 변환 ──────────────────────────────────────────
    df = records_to_dataframe(validated_records)
    if df.empty:
        logger.warning("[Transform] 입력 레코드가 없어 변환 생략")
        return {"transformed": [], "report": {"input_count": 0}}

    # ── Step 2: 이상치·결측 처리 ───────────────────────────────────────
    df_clean, clean_report = clean(df)
    logger.info(
        f"[Transform/clean] 이상치={clean_report['outlier_count']}건 / "
        f"기존결측={clean_report['null_count']}건"
    )

    # ── Step 3: 파생 지표 계산 ──────────────────────────────────────────
    df_derived = compute_derived(df_clean, run_id)
    derived_count = len(df_derived)
    logger.info(f"[Transform/derived] 파생 레코드 {derived_count:,}건 생성")

    # ── Step 4: dict 변환 ───────────────────────────────────────────────
    orig_records    = dataframe_to_records(df_clean)
    derived_records = dataframe_to_records(df_derived) if not df_derived.empty else []

    all_records = orig_records + derived_records

    report = {
        "input_count":   len(validated_records),
        "clean":         clean_report,
        "derived_count": derived_count,
        "output_count":  len(all_records),
    }

    logger.info(
        f"[Transform] 완료 — "
        f"원본 {len(orig_records):,}건 + 파생 {derived_count:,}건 "
        f"= 총 {len(all_records):,}건"
    )

    return {"transformed": all_records, "report": report}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단독 실행 (디버그)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import json
    import uuid

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    RUN_ID = str(uuid.uuid4())
    INGESTED_AT = datetime.now(timezone.utc).isoformat()

    # ── 샘플 레코드 생성 (취업자 수 12개월치) ──────────────────────────
    sample: list[dict] = []
    base_values = [
        28100, 27900, 28200, 28400, 28700, 28600,
        28900, 29100, 28800, 28500, 28300, 28600,
        28700, 28950,  # 2년차 2개월 (YoY 계산 가능)
    ]
    for i, val in enumerate(base_values):
        year  = 2024 + (i >= 12)
        month = (i % 12) + 1
        sample.append({
            "source":          "kosis_emp",
            "period":          f"{year}-{month:02d}",
            "variable_name":   "raw_employed_by_sex_age",
            "category_key":    "0|30|T30",
            "value":           float(val),
            "adjustment_type": "raw",
            "ingested_at":     INGESTED_AT,
            "run_id":          RUN_ID,
            "category_name":   "전체 | 30-39세 | 취업자",
            "tbl_id":          "DT_1DA7012S",
        })

    result = run_transform(sample, run_id=RUN_ID)

    print("\n=== 변환 결과 요약 ===")
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))

    print("\n=== 파생 레코드 샘플 (첫 8건) ===")
    derived = [r for r in result["transformed"] if "__" in r["variable_name"]]
    for r in derived[:8]:
        print(f"  {r['period']}  {r['variable_name']:45s}  {r['value']}")

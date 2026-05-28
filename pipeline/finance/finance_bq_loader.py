"""
finance_bq_loader.py
─────────────────────────────────────────────────────────────────────────────
BigQuery RAW LAYER — 금융동향 파이프라인 (고용동향 bq_loader.py 구조 동일)

━━━ 테이블 구조 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  finance_raw  (파티션 + 클러스터링)
  ┌─────────────────────┬──────────────┬──────────────────────────────────────┐
  │ 컬럼                │ 타입         │ 비고                                 │
  ├─────────────────────┼──────────────┼──────────────────────────────────────┤
  │ source              │ STRING REQ   │ ecos / fred / kosis_finance          │
  │ period              │ STRING REQ   │ YYYY-MM                              │
  │ period_date         │ DATE REQ     │ YYYY-MM-01 파티션 키                 │
  │ variable_name       │ STRING REQ   │ kr_base_rate / us_fed_funds_rate ... │
  │ category_key        │ STRING REQ   │ ECOS item_code / FRED series_id 등   │
  │ value               │ FLOAT64      │ NULL 허용 (결측)                     │
  │ adjustment_type     │ STRING       │ raw / index                          │
  │ ingested_at         │ TIMESTAMP    │ 적재 시각                            │
  │ run_id              │ STRING       │ 실행 UUID                            │
  │ category_name       │ STRING       │ 한글/영문 분류명                     │
  │ tbl_id              │ STRING       │ ECOS stat_code / FRED series_id      │
  └─────────────────────┴──────────────┴──────────────────────────────────────┘
  파티션  : period_date (MONTH)
  클러스터: source, variable_name, category_key

  finance_stg   (Staging — 파티션 없음, run마다 TRUNCATE 후 적재)
  finance_raw_dedup  (View — Raw 위 ROW_NUMBER Dedup)

━━━ category_key 패턴 (소스별) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  source          │ category_key 패턴              │ 예시
  ────────────────┼────────────────────────────────┼────────────────────────
  ecos            │ item_code1 (단독)              │ "0101000", "BBHA00"
                  │ item_code1|item_code2 (복합)   │ "BBHA00|"
  fred            │ series_id                      │ "FEDFUNDS", "GS10"
  kosis_finance   │ C1|C2|C3|ITM_ID (조합)        │ "0||13103112810M_102+"

━━━ Unique Key ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  (source, period, variable_name, category_key)
"""

import logging
import math

from google.cloud import bigquery

from finance_config import GCP_PROJECT_ID, BQ_DATASET, BQ_RAW_TABLE, BQ_STG_TABLE

logger = logging.getLogger(__name__)

# ── MERGE Unique Key ──────────────────────────────────────────────────────────
_MERGE_KEY = ("source", "period", "variable_name", "category_key")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 스키마 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAW_TABLE_SCHEMA = [
    bigquery.SchemaField("source",          "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("period",          "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("period_date",     "DATE",      mode="REQUIRED"),  # 파티션 키
    bigquery.SchemaField("variable_name",   "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("category_key",    "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("value",           "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("adjustment_type", "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("ingested_at",     "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("run_id",          "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("category_name",   "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("tbl_id",          "STRING",    mode="NULLABLE"),  # 고용과 다른 추가 컬럼
]

STG_TABLE_SCHEMA   = [f for f in RAW_TABLE_SCHEMA if f.name != "period_date"]
_STG_COLUMNS: frozenset[str] = frozenset(f.name for f in STG_TABLE_SCHEMA)

DEDUP_VIEW_NAME = "finance_raw_dedup"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_bq_client() -> bigquery.Client:
    return bigquery.Client(project=GCP_PROJECT_ID)


def _full(table: str) -> str:
    return f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{table}`"


def _sanitize(record: dict) -> dict:
    """Staging INSERT 전처리: 스키마 외 컬럼 제거 + NaN/Inf → None."""
    out = {}
    for k, v in record.items():
        if k not in _STG_COLUMNS:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        else:
            out[k] = v
    return out


def _dedup_records(records: list[dict]) -> tuple[list[dict], int]:
    """MERGE Key 기준 Python 레벨 중복 제거 (ingested_at 최신 우선)."""
    seen: dict[tuple, dict] = {}
    for rec in records:
        key = tuple(rec.get(k, "") for k in _MERGE_KEY)
        existing = seen.get(key)
        if existing is None:
            seen[key] = rec
        else:
            new_ts = rec.get("ingested_at", "") or ""
            old_ts = existing.get("ingested_at", "") or ""
            if new_ts >= old_ts:
                seen[key] = rec
    deduped = list(seen.values())
    return deduped, len(records) - len(deduped)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터셋 / 테이블 / 뷰 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ensure_dataset(client: bigquery.Client) -> None:
    """finance_stats 데이터셋이 없으면 생성 (서울 리전)."""
    dataset_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}"
    try:
        client.get_dataset(dataset_id)
        logger.info(f"[BQ] 데이터셋 확인: {BQ_DATASET}")
    except Exception:
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = "asia-northeast3"
        client.create_dataset(dataset, exists_ok=True)
        logger.info(f"[BQ] 데이터셋 생성 완료: {BQ_DATASET} (asia-northeast3)")


def ensure_raw_table(client: bigquery.Client) -> None:
    """finance_raw 테이블이 없으면 생성."""
    table_ref = client.dataset(BQ_DATASET).table(BQ_RAW_TABLE)
    try:
        client.get_table(table_ref)
        logger.info(f"[BQ] Raw 테이블 확인: {BQ_DATASET}.{BQ_RAW_TABLE}")
    except Exception:
        table = bigquery.Table(table_ref, schema=RAW_TABLE_SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.MONTH,
            field="period_date",
            require_partition_filter=False,
        )
        table.clustering_fields = ["source", "variable_name", "category_key"]
        client.create_table(table)
        logger.info(
            f"[BQ] Raw 테이블 생성 완료: {BQ_DATASET}.{BQ_RAW_TABLE} "
            "(파티션: period_date/MONTH, 클러스터: source/variable_name/category_key)"
        )


def ensure_stg_table(client: bigquery.Client) -> None:
    """finance_stg 테이블이 없으면 생성 (파티션 없음)."""
    table_ref = client.dataset(BQ_DATASET).table(BQ_STG_TABLE)
    try:
        client.get_table(table_ref)
    except Exception:
        table = bigquery.Table(table_ref, schema=STG_TABLE_SCHEMA)
        client.create_table(table)
        logger.info(f"[BQ] Staging 테이블 생성 완료: {BQ_DATASET}.{BQ_STG_TABLE}")


def create_dedup_view(client: bigquery.Client) -> None:
    """finance_raw_dedup View 생성 또는 갱신."""
    sql = f"""
    CREATE OR REPLACE VIEW `{GCP_PROJECT_ID}.{BQ_DATASET}.{DEDUP_VIEW_NAME}` AS
    SELECT * EXCEPT (rn)
    FROM (
      SELECT
        *,
        ROW_NUMBER() OVER (
          PARTITION BY source, period, variable_name, category_key
          ORDER BY ingested_at DESC
        ) AS rn
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_RAW_TABLE}`
    )
    WHERE rn = 1
    """
    client.query(sql).result()
    logger.info(f"[BQ] Dedup View 갱신 완료: {BQ_DATASET}.{DEDUP_VIEW_NAME}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Staging 적재
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_to_staging(
    client: bigquery.Client,
    records: list[dict],
    run_id: str,
) -> tuple[int, int]:
    """레코드를 Staging 테이블에 INSERT (TRUNCATE → 배치 INSERT)."""
    if not records:
        logger.info("[BQ Staging] 적재할 데이터 없음")
        return 0, 0

    records, dropped = _dedup_records(records)
    if dropped:
        logger.warning(f"[BQ Staging] MERGE 키 중복 {dropped:,}건 제거")

    client.query(f"TRUNCATE TABLE {_full(BQ_STG_TABLE)}").result()
    logger.info("[BQ Staging] TRUNCATE 완료")

    table_ref = client.dataset(BQ_DATASET).table(BQ_STG_TABLE)
    BATCH = 1_000
    total_inserted = total_errors = 0

    for i in range(0, len(records), BATCH):
        batch  = [_sanitize(r) for r in records[i: i + BATCH]]
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            total_errors += len(errors)
            logger.error(f"[BQ Staging] INSERT 오류 ({len(errors)}건): {errors[:2]}")
        else:
            total_inserted += len(batch)

    logger.info(f"[BQ Staging] 적재 완료: {total_inserted:,}건 (오류: {total_errors:,}건)")
    return total_inserted, dropped


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MERGE (Upsert)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_MERGE_SQL = """
MERGE {raw} AS T
USING (
  SELECT
    source, period,
    DATE(CONCAT(period, '-01')) AS period_date,
    variable_name, category_key,
    value, adjustment_type, ingested_at, run_id, category_name, tbl_id
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY source, period, variable_name, category_key
        ORDER BY ingested_at DESC
      ) AS rn
    FROM {stg}
  )
  WHERE rn = 1
) AS S
ON  T.source        = S.source
AND T.period        = S.period
AND T.variable_name = S.variable_name
AND T.category_key  = S.category_key
WHEN MATCHED THEN
  UPDATE SET
    T.value           = S.value,
    T.adjustment_type = S.adjustment_type,
    T.ingested_at     = S.ingested_at,
    T.run_id          = S.run_id,
    T.category_name   = S.category_name,
    T.tbl_id          = S.tbl_id
WHEN NOT MATCHED THEN
  INSERT (
    source, period, period_date, variable_name, category_key,
    value, adjustment_type, ingested_at, run_id, category_name, tbl_id
  )
  VALUES (
    S.source, S.period, S.period_date, S.variable_name, S.category_key,
    S.value, S.adjustment_type, S.ingested_at, S.run_id, S.category_name, S.tbl_id
  )
"""


def merge_staging_to_raw(client: bigquery.Client) -> int:
    """Staging → Raw MERGE (Upsert). Unique Key: (source, period, variable_name, category_key)"""
    sql = _MERGE_SQL.format(raw=_full(BQ_RAW_TABLE), stg=_full(BQ_STG_TABLE))
    logger.info("[BQ MERGE] Staging → Raw Upsert 실행 중")
    job = client.query(sql)
    job.result()
    rows_affected = job.num_dml_affected_rows or 0
    logger.info(f"[BQ MERGE] 완료: {rows_affected:,}행 영향")
    return rows_affected


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 적재 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def upsert_records(records: list[dict], run_id: str) -> dict:
    """
    수집 레코드를 finance_raw에 Upsert.

    Returns: {"staged": int, "dropped": int, "merged": int}
    """
    client = get_bq_client()

    ensure_dataset(client)
    ensure_raw_table(client)
    ensure_stg_table(client)

    staged, dropped = load_to_staging(client, records, run_id)
    merged          = merge_staging_to_raw(client)
    create_dedup_view(client)

    return {"staged": staged, "dropped": dropped, "merged": merged}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# category_key 검증 유틸 (fin-04 검증용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def validate_category_keys(records: list[dict]) -> dict:
    """
    수집된 레코드의 category_key 패턴을 소스별로 검증.

    소스별 기대 패턴:
      ecos         → non-empty string (item_code 기반)
      fred         → FRED series ID (영문 대문자, 예: FEDFUNDS)
      kosis_finance→ '|' 구분 코드 조합

    Returns
    -------
    dict : {
        "total": int,
        "empty_category_key": int,   # category_key가 빈 문자열인 건수
        "by_source": {source: {"count": int, "sample_keys": list[str]}}
    }
    """
    from collections import defaultdict

    by_source: dict[str, dict] = defaultdict(lambda: {"count": 0, "sample_keys": []})
    empty_count = 0

    for rec in records:
        src = rec.get("source", "unknown")
        key = rec.get("category_key", "")

        if not key:
            empty_count += 1

        by_source[src]["count"] += 1
        if len(by_source[src]["sample_keys"]) < 3:
            by_source[src]["sample_keys"].append(key)

    return {
        "total": len(records),
        "empty_category_key": empty_count,
        "by_source": dict(by_source),
    }


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    client = get_bq_client()
    ensure_dataset(client)
    ensure_raw_table(client)
    ensure_stg_table(client)
    create_dedup_view(client)
    print("finance_stats 데이터셋 및 테이블 생성 완료")

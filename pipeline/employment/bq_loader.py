"""
bq_loader.py
─────────────────────────────────────────────────────────────────────────────
BigQuery RAW LAYER — Upsert & Dedup

━━━ 테이블 구조 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  employment_raw  (파티션 + 클러스터링)
  ┌─────────────────────┬──────────────┬──────────────────────────────────┐
  │ 컬럼                │ 타입         │ 비고                             │
  ├─────────────────────┼──────────────┼──────────────────────────────────┤
  │ source              │ STRING REQ   │ kosis_emp / bls / ...            │
  │ period              │ STRING REQ   │ YYYY-MM                          │
  │ period_date         │ DATE REQ     │ YYYY-MM-01 파티션 키             │
  │ variable_name       │ STRING REQ   │ raw_employed__ / seasonal__ ...  │
  │ category_key        │ STRING REQ   │ 분류 코드 조합                   │
  │ value               │ FLOAT64      │ NULL 허용 (결측)                 │
  │ adjustment_type     │ STRING       │ raw/seasonal/index/derived_*     │
  │ ingested_at         │ TIMESTAMP    │ 적재 시각                        │
  │ run_id              │ STRING       │ 실행 UUID                        │
  │ category_name       │ STRING       │ 한글 분류명                      │
  └─────────────────────┴──────────────┴──────────────────────────────────┘
  파티션  : period_date (MONTH) — 분석 쿼리의 기간 필터와 일치
  클러스터: source, adjustment_type, variable_name

  employment_stg  (Staging — 파티션 없음, run마다 TRUNCATE 후 적재)

  employment_raw_dedup  (View — Raw 위 ROW_NUMBER Dedup)

━━━ 적재 흐름 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  records (Python list[dict])
      │
      ├─ [1] _dedup_records()      Python 레벨 MERGE 키 중복 제거 (1차 방어)
      │
      ├─ [2] _sanitize()           NaN/Inf → None, 스키마 외 컬럼 제거
      │
      ├─ [3] TRUNCATE Staging      run 경계 명확화, 누적 방지
      │
      ├─ [4] INSERT → Staging      1,000건 배치
      │
      ├─ [5] MERGE Staging→Raw     ROW_NUMBER Dedup USING (2차 방어)
      │       MATCHED     → UPDATE
      │       NOT MATCHED → INSERT
      │
      └─ [6] CREATE OR REPLACE VIEW employment_raw_dedup

━━━ Unique Key ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  (source, period, variable_name, category_key)
"""

import logging
import math
from datetime import datetime, timezone

from google.cloud import bigquery

from config import GCP_PROJECT_ID, BQ_DATASET, BQ_RAW_TABLE, BQ_STG_TABLE

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
]

# Staging은 period_date 없이 동일 스키마 사용 (파티션 불필요)
STG_TABLE_SCHEMA = [f for f in RAW_TABLE_SCHEMA if f.name != "period_date"]

# insert_rows_json 허용 컬럼 집합 (스키마 기반 자동 생성)
_STG_COLUMNS: frozenset[str] = frozenset(f.name for f in STG_TABLE_SCHEMA)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_bq_client() -> bigquery.Client:
    """ADC 기반 BigQuery 클라이언트 반환 (Cloud Run / GCE 자동 인증)."""
    return bigquery.Client(project=GCP_PROJECT_ID)


def _full(table: str) -> str:
    """project.dataset.table 형식의 전체 참조 문자열."""
    return f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{table}`"


def _period_to_date(period: str) -> str:
    """'YYYY-MM' → 'YYYY-MM-01' (BQ DATE 타입용)."""
    return f"{period}-01"


def _sanitize(record: dict) -> dict:
    """
    Staging INSERT 전처리.
    1. Staging 스키마 외 컬럼 제거 (tbl_id, value_original, period_date 등)
    2. float NaN / Inf / -Inf → None
    3. period_date 자동 생성 (period 컬럼에서 파생) — Staging에는 없음
    """
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
    """
    MERGE Unique Key 기준 Python 레벨 중복 제거 (1차 방어).
    동일 키가 여럿이면 ingested_at이 가장 늦은 레코드를 유지.
    ingested_at이 없으면 리스트 순서상 마지막 레코드를 사용.

    Returns
    -------
    (deduped_records, dropped_count)
    """
    seen: dict[tuple, dict] = {}
    for rec in records:
        key = tuple(rec.get(k, "") for k in _MERGE_KEY)
        existing = seen.get(key)
        if existing is None:
            seen[key] = rec
        else:
            # ingested_at 비교: 더 최신 레코드를 유지
            new_ts  = rec.get("ingested_at", "") or ""
            old_ts  = existing.get("ingested_at", "") or ""
            if new_ts >= old_ts:
                seen[key] = rec
    deduped = list(seen.values())
    dropped = len(records) - len(deduped)
    return deduped, dropped


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테이블 / 뷰 DDL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _has_column(table: bigquery.Table, column: str) -> bool:
    """테이블 스키마에 해당 컬럼이 존재하는지 확인."""
    return any(f.name == column for f in table.schema)


def ensure_raw_table(client: bigquery.Client) -> None:
    """
    Raw 테이블이 없으면 생성, 있으면 스키마 마이그레이션 적용.

    마이그레이션:
      - period_date (DATE) 컬럼이 없으면 ALTER TABLE ADD COLUMN 후
        기존 rows에 period 기반으로 UPDATE
        (파티션 변경은 불가 — 기존 테이블은 ingested_at 파티션 유지,
         신규 테이블만 period_date 파티션으로 생성)

    파티션  : period_date MONTH (신규) / ingested_at MONTH (기존 유지)
    클러스터: source, adjustment_type, variable_name
    """
    dataset_ref = client.dataset(BQ_DATASET)
    table_ref   = dataset_ref.table(BQ_RAW_TABLE)

    try:
        table = client.get_table(table_ref)
        logger.info(f"[BQ] Raw 테이블 확인: {BQ_DATASET}.{BQ_RAW_TABLE}")

        # period_date 컬럼이 없으면 추가
        if not _has_column(table, "period_date"):
            logger.info("[BQ] period_date 컬럼 없음 — ALTER TABLE ADD COLUMN 실행")
            alter_sql = f"""
            ALTER TABLE {_full(BQ_RAW_TABLE)}
            ADD COLUMN IF NOT EXISTS period_date DATE
            """
            client.query(alter_sql).result()
            logger.info("[BQ] period_date 컬럼 추가 완료")

            # 기존 행 period_date 채우기 (period STRING → DATE)
            logger.info("[BQ] 기존 행 period_date 백필 시작")
            backfill_sql = f"""
            UPDATE {_full(BQ_RAW_TABLE)}
            SET period_date = DATE(CONCAT(period, '-01'))
            WHERE period_date IS NULL
            """
            job = client.query(backfill_sql)
            job.result()
            updated = job.num_dml_affected_rows or 0
            logger.info(f"[BQ] period_date 백필 완료: {updated:,}행 갱신")
        return

    except Exception:
        pass

    # 테이블이 없으면 신규 생성
    logger.info(f"[BQ] Raw 테이블 생성 중: {BQ_DATASET}.{BQ_RAW_TABLE}")
    table = bigquery.Table(table_ref, schema=RAW_TABLE_SCHEMA)
    table.time_partitioning = bigquery.TimePartitioning(
        type_  = bigquery.TimePartitioningType.MONTH,
        field  = "period_date",
        require_partition_filter = False,
    )
    table.clustering_fields = ["source", "adjustment_type", "variable_name"]
    client.create_table(table)
    logger.info("[BQ] Raw 테이블 생성 완료 (파티션: period_date/MONTH, 클러스터: source/adjustment_type/variable_name)")


def ensure_stg_table(client: bigquery.Client) -> None:
    """
    Staging 테이블이 없으면 생성 (파티션 없음).
    run마다 TRUNCATE 후 재적재하므로 파티션 불필요.
    """
    dataset_ref = client.dataset(BQ_DATASET)
    table_ref   = dataset_ref.table(BQ_STG_TABLE)

    try:
        client.get_table(table_ref)
    except Exception:
        table = bigquery.Table(table_ref, schema=STG_TABLE_SCHEMA)
        client.create_table(table)
        logger.info(f"[BQ] Staging 테이블 생성 완료: {BQ_DATASET}.{BQ_STG_TABLE}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Staging 적재
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_to_staging(
    client:  bigquery.Client,
    records: list[dict],
    run_id:  str,
) -> tuple[int, int]:
    """
    레코드를 Staging 테이블에 INSERT.

    흐름:
      1. Python 레벨 MERGE 키 중복 제거 (_dedup_records)
      2. Staging TRUNCATE (run 경계 명확화, 이전 데이터 누적 방지)
      3. 1,000건 배치 INSERT (_sanitize 적용)

    TRUNCATE 이유:
      run_id 조건 DELETE 방식은 이전 run 데이터가 Staging에 남아
      MERGE 소스가 커지고 ROW_NUMBER 방어에도 불구하고 쿼리 비용이 증가.
      Staging은 임시 테이블이므로 매 run 시작 시 완전히 비움.

    Returns
    -------
    (inserted_count, dropped_count)
    """
    if not records:
        logger.info("[BQ Staging] 적재할 데이터 없음")
        return 0, 0

    # ── [1] Python 레벨 dedup ─────────────────────────────────────────
    records, dropped = _dedup_records(records)
    if dropped:
        logger.warning(f"[BQ Staging] MERGE 키 중복 {dropped:,}건 제거 (ingested_at 최신 유지)")

    # ── [2] Staging TRUNCATE ─────────────────────────────────────────
    stg_full = _full(BQ_STG_TABLE)
    client.query(f"TRUNCATE TABLE {stg_full}").result()
    logger.info("[BQ Staging] TRUNCATE 완료")

    # ── [3] 배치 INSERT ───────────────────────────────────────────────
    table_ref = client.dataset(BQ_DATASET).table(BQ_STG_TABLE)
    BATCH = 1_000
    total_inserted = 0
    total_errors   = 0

    for i in range(0, len(records), BATCH):
        batch  = [_sanitize(r) for r in records[i : i + BATCH]]
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            total_errors += len(errors)
            logger.error(
                f"[BQ Staging] INSERT 오류 배치 {i // BATCH + 1} "
                f"({len(errors)}건): {errors[:2]}"
            )
        else:
            total_inserted += len(batch)

    if total_errors:
        logger.warning(f"[BQ Staging] INSERT 오류 합계: {total_errors:,}건")
    logger.info(f"[BQ Staging] 적재 완료: {total_inserted:,}건 (오류: {total_errors:,}건)")
    return total_inserted, dropped


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MERGE (Upsert)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# USING 절 내부에서 ROW_NUMBER로 Staging 중복을 재차 제거 (2차 방어).
# period_date는 MERGE 시 period에서 파생해 Raw에 삽입.
MERGE_SQL = """
MERGE {raw} AS T
USING (
  SELECT
    source,
    period,
    DATE(CONCAT(period, '-01'))  AS period_date,
    variable_name,
    category_key,
    value,
    adjustment_type,
    ingested_at,
    run_id,
    category_name
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
    T.category_name   = S.category_name
WHEN NOT MATCHED THEN
  INSERT (
    source, period, period_date, variable_name, category_key,
    value, adjustment_type, ingested_at, run_id, category_name
  )
  VALUES (
    S.source, S.period, S.period_date, S.variable_name, S.category_key,
    S.value, S.adjustment_type, S.ingested_at, S.run_id, S.category_name
  )
"""


def merge_staging_to_raw(client: bigquery.Client) -> int:
    """
    Staging → Raw MERGE (Upsert).
    Unique Key: (source, period, variable_name, category_key)

    MATCHED     → value / adjustment_type / ingested_at / run_id / category_name 갱신
    NOT MATCHED → 신규 행 INSERT (period_date 자동 파생)

    Returns
    -------
    int : DML 영향 행 수
    """
    sql = MERGE_SQL.format(
        raw = _full(BQ_RAW_TABLE),
        stg = _full(BQ_STG_TABLE),
    )
    logger.info("[BQ MERGE] Staging → Raw Upsert 실행 중")
    job = client.query(sql)
    job.result()
    rows_affected = job.num_dml_affected_rows or 0
    logger.info(f"[BQ MERGE] 완료: {rows_affected:,}행 영향")
    return rows_affected


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dedup View
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEDUP_VIEW_NAME = "employment_raw_dedup"

DEDUP_VIEW_SQL = """
CREATE OR REPLACE VIEW `{project}.{dataset}.{view}` AS
-- Raw 테이블에서 Unique Key 기준 최신 행만 노출하는 Dedup View.
-- Mart / 분석 레이어는 이 View를 소스로 사용한다.
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY source, period, variable_name, category_key
      ORDER BY ingested_at DESC
    ) AS rn
  FROM `{project}.{dataset}.{raw}`
)
WHERE rn = 1
"""


def create_dedup_view(client: bigquery.Client) -> None:
    """employment_raw_dedup View를 생성 또는 갱신."""
    sql = DEDUP_VIEW_SQL.format(
        project = GCP_PROJECT_ID,
        dataset = BQ_DATASET,
        view    = DEDUP_VIEW_NAME,
        raw     = BQ_RAW_TABLE,
    )
    client.query(sql).result()
    logger.info(f"[BQ] Dedup View 갱신 완료: {BQ_DATASET}.{DEDUP_VIEW_NAME}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Raw 테이블 읽기 (--transform-only 용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_raw_records(
    client:       bigquery.Client,
    period_start: str,
    period_end:   str,
    sources:      list[str] | None = None,
) -> list[dict]:
    """
    Raw 테이블에서 원본 레코드를 읽어 반환.

    --transform-only 모드에서 API 수집 없이 기존 BQ 데이터를
    변환 입력으로 사용할 때 호출.

    조회 대상:
      - adjustment_type IN ('raw', 'seasonal', 'index')
        → derived_pct / derived_ma 는 중복 생성 방지를 위해 제외
      - period >= period_start AND period <= period_end (STRING 사전순 비교)
        period는 'YYYY-MM' 형식이므로 사전순 = 날짜순, period_date 컬럼 불필요
      - sources 지정 시 해당 소스만

    Returns
    -------
    list[dict] : validator.IngestionRecord 스키마 호환 레코드 목록
    """
    ORIGINAL_ADJ = ("raw", "seasonal", "index")
    adj_list     = ", ".join(f"'{t}'" for t in ORIGINAL_ADJ)
    src_clause   = ""
    if sources:
        src_list   = ", ".join(f"'{s}'" for s in sources)
        src_clause = f"AND source IN ({src_list})"

    # period는 'YYYY-MM' STRING이므로 직접 비교 (사전순 = 날짜순)
    # period_date 컬럼 유무와 무관하게 동작하도록 period로 필터링
    sql = f"""
    SELECT
        source,
        period,
        variable_name,
        category_key,
        value,
        adjustment_type,
        ingested_at,
        run_id,
        category_name
    FROM {_full(BQ_RAW_TABLE)}
    WHERE period >= @period_start
      AND period <= @period_end
      AND adjustment_type IN ({adj_list})
      {src_clause}
    ORDER BY source, variable_name, category_key, period
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("period_start", "STRING", period_start),
            bigquery.ScalarQueryParameter("period_end",   "STRING", period_end),
        ]
    )

    logger.info(
        f"[BQ Read] Raw 조회 — 기간: {period_start}~{period_end}  "
        f"소스: {sources or '전체'}  adj_type: {ORIGINAL_ADJ}"
    )

    rows    = client.query(sql, job_config=job_config).result()
    records = [dict(row) for row in rows]

    # ingested_at: BQ Timestamp 객체 → ISO 문자열
    for rec in records:
        ia = rec.get("ingested_at")
        if ia is not None and not isinstance(ia, str):
            rec["ingested_at"] = ia.isoformat()

    logger.info(f"[BQ Read] 조회 완료: {len(records):,}건")
    return records


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 적재 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def upsert_records(records: list[dict], run_id: str) -> dict:
    """
    수집·변환 레코드 전체를 BigQuery Raw 테이블에 Upsert.

    Flow:
        records
          → dedup (Python)
          → TRUNCATE Staging
          → INSERT Staging (배치)
          → MERGE Staging → Raw
          → CREATE OR REPLACE VIEW employment_raw_dedup

    Returns
    -------
    dict : {
        "staged":  int,   # Staging에 INSERT된 행 수
        "dropped": int,   # Python 레벨 dedup 제거 건수
        "merged":  int,   # Raw에 MERGE된 행 수 (INSERT + UPDATE 합산)
    }
    """
    client = get_bq_client()

    ensure_raw_table(client)
    ensure_stg_table(client)

    staged, dropped = load_to_staging(client, records, run_id)
    merged          = merge_staging_to_raw(client)
    create_dedup_view(client)

    return {"staged": staged, "dropped": dropped, "merged": merged}

#!/usr/bin/env python3
"""fin-10: pipeline-job E2E verification script."""

from __future__ import annotations

import subprocess
import sys
import os
import json
from typing import Iterable

try:
    from google.cloud import bigquery  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    bigquery = None  # type: ignore


PROJECT = "auto-report-489722"
REGION = "asia-northeast3"
JOB = "pipeline-job"
DATASET = "finance_stats"
TABLES = ("finance_raw", "finance_raw_dedup")
_GCLOUD_PYTHON = os.environ.get("CLOUDSDK_PYTHON", sys.executable)


def _print_header(step: str, message: str) -> None:
    print(f"[{step}] {message}")


def run_pipeline() -> None:
    _print_header("1/3", f"Cloud Run Job '{JOB}' execution started...")
    env = os.environ.copy()
    env.setdefault("CLOUDSDK_PYTHON", _GCLOUD_PYTHON)
    result = subprocess.run(
        [
            "gcloud.cmd",
            "run",
            "jobs",
            "execute",
            JOB,
            "--region",
            REGION,
            "--project",
            PROJECT,
            "--wait",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        print("FAIL:")
        print(result.stderr.strip())
        sys.exit(1)

    print("    done")
    if result.stdout.strip():
        print(result.stdout.strip())


def _require_bigquery_client():
    if bigquery is None:
        return None
    return bigquery.Client(project=PROJECT)


def _fetch_count(client, table: str) -> int:
    if client is None:
        row = _run_bq_cli_query(
            f"SELECT COUNT(*) AS cnt FROM `{PROJECT}.{DATASET}.{table}`"
        )[0]
        return int(row["cnt"])
    query = f"SELECT COUNT(*) AS cnt FROM `{PROJECT}.{DATASET}.{table}`"
    row = next(client.query(query).result())
    return int(row.cnt)


def _latest_period_rows(client) -> Iterable:
    query = f"""
        SELECT variable_name, MAX(period) AS latest_period, COUNT(*) AS cnt
        FROM `{PROJECT}.{DATASET}.finance_raw_dedup`
        GROUP BY variable_name
        ORDER BY variable_name
    """
    if client is None:
        return _run_bq_cli_query(query)
    return client.query(query).result()


def _latest_ingested_rows(client) -> Iterable:
    query = f"""
        SELECT
          run_id,
          MAX(ingested_at) AS latest_ingested_at,
          COUNT(*) AS cnt
        FROM `{PROJECT}.{DATASET}.finance_raw_dedup`
        GROUP BY run_id
        ORDER BY latest_ingested_at DESC
        LIMIT 5
    """
    if client is None:
        return _run_bq_cli_query(query)
    return client.query(query).result()


def _run_bq_cli_query(query: str) -> list[dict]:
    env = os.environ.copy()
    env.setdefault("CLOUDSDK_PYTHON", _GCLOUD_PYTHON)
    result = subprocess.run(
        [
            "bq.cmd",
            "query",
            "--project_id",
            PROJECT,
            "--use_legacy_sql=false",
            "--format=json",
            query,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        print("FAIL:")
        print(result.stderr.strip())
        sys.exit(1)
    return json.loads(result.stdout or "[]")


def verify_bq() -> None:
    _print_header("2/3", "BigQuery load verification...")
    client = _require_bigquery_client()

    for table in TABLES:
        cnt = _fetch_count(client, table)
        status = "OK" if cnt > 0 else "FAIL (0 rows)"
        print(f"    {table}: {cnt:,} rows -> {status}")
        if cnt == 0:
            sys.exit(1)

    _print_header("3/3", "Latest period by variable_name:")
    for row in _latest_period_rows(client):
        variable_name = row["variable_name"] if isinstance(row, dict) else row.variable_name
        latest_period = row["latest_period"] if isinstance(row, dict) else row.latest_period
        cnt = row["cnt"] if isinstance(row, dict) else row.cnt
        print(f"    {variable_name:<30} latest={latest_period} rows={cnt}")

    print("    recent run_id / ingested_at:")
    for row in _latest_ingested_rows(client):
        run_id = row["run_id"] if isinstance(row, dict) else row.run_id
        latest_ingested_at = (
            row["latest_ingested_at"] if isinstance(row, dict) else row.latest_ingested_at
        )
        cnt = row["cnt"] if isinstance(row, dict) else row.cnt
        print(
            f"    run_id={run_id or '-'} "
            f"latest_ingested_at={latest_ingested_at} rows={cnt}"
        )


def main() -> None:
    run_pipeline()
    verify_bq()
    print("\nE2E verification finished")


if __name__ == "__main__":
    main()

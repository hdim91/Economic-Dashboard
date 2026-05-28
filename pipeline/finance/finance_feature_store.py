"""
finance_feature_store.py

Create finance feature store BigQuery views.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

try:
    from google.cloud import bigquery  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    bigquery = None  # type: ignore

from finance_config import BQ_DATASET, GCP_PROJECT_ID

logger = logging.getLogger(__name__)

_DEDUP = f"`{GCP_PROJECT_ID}.{BQ_DATASET}.finance_raw_dedup`"


def _view(name: str) -> str:
    return f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{name}`"


FEATURE_SPECS: list[dict[str, str]] = [
    {"feature_name": "kr_base_rate", "source": "ecos", "variable_name": "kr_base_rate", "unit": "%", "description": "Korea base rate"},
    {"feature_name": "kr_govbond_3y", "source": "ecos", "variable_name": "kr_govbond_3y", "unit": "%", "description": "Korea 3-year government bond yield"},
    {"feature_name": "kr_govbond_10y", "source": "ecos", "variable_name": "kr_govbond_10y", "unit": "%", "description": "Korea 10-year government bond yield"},
    {"feature_name": "kr_cd_91d", "source": "ecos", "variable_name": "kr_cd_91d", "unit": "%", "description": "CD 91-day rate"},
    {"feature_name": "kr_household_credit", "source": "ecos", "variable_name": "kr_household_credit", "unit": "KRW_bn", "description": "Household credit balance"},
    {"feature_name": "kr_household_loan", "source": "ecos", "variable_name": "kr_household_loan", "unit": "KRW_bn", "description": "Household loan balance"},
    {"feature_name": "kr_m2", "source": "ecos", "variable_name": "kr_m2", "unit": "KRW_bn", "description": "Korea M2 money supply"},
    {"feature_name": "kr_usd_rate", "source": "ecos", "variable_name": "kr_usd_rate", "unit": "KRW_per_USD", "description": "KRW USD exchange rate monthly average"},
    {"feature_name": "kr_cpi", "source": "ecos", "variable_name": "kr_cpi", "unit": "index", "description": "Korea CPI"},
    {"feature_name": "kr_ppi", "source": "ecos", "variable_name": "kr_ppi", "unit": "index", "description": "Korea PPI"},
    {"feature_name": "us_fed_funds_rate", "source": "fred", "variable_name": "us_fed_funds_rate", "unit": "%", "description": "US federal funds rate"},
    {"feature_name": "us_treasury_10y", "source": "fred", "variable_name": "us_treasury_10y", "unit": "%", "description": "US 10-year treasury yield"},
    {"feature_name": "us_treasury_2y", "source": "fred", "variable_name": "us_treasury_2y", "unit": "%", "description": "US 2-year treasury yield"},
    {"feature_name": "us_yield_spread", "source": "fred", "variable_name": "us_yield_spread", "unit": "%", "description": "US 10Y minus 2Y spread"},
    {"feature_name": "us_cpi", "source": "fred", "variable_name": "us_cpi", "unit": "index", "description": "US CPI"},
    {"feature_name": "us_pce", "source": "fred", "variable_name": "us_pce", "unit": "index", "description": "US PCE price index"},
    {"feature_name": "us_cs_hpi", "source": "fred", "variable_name": "us_cs_hpi", "unit": "index", "description": "Case Shiller national HPI"},
    {"feature_name": "us_mortgage_30y", "source": "fred", "variable_name": "us_mortgage_30y", "unit": "%", "description": "US 30-year mortgage rate"},
    {"feature_name": "us_consumer_credit", "source": "fred", "variable_name": "us_consumer_credit", "unit": "USD_mn", "description": "US consumer credit"},
    {"feature_name": "us_credit_delinquency", "source": "fred", "variable_name": "us_credit_delinquency", "unit": "%", "description": "US consumer credit delinquency"},
    {"feature_name": "raw_kr_cpi_kosis", "source": "kosis_finance", "variable_name": "raw_kr_cpi_kosis", "unit": "index", "description": "KOSIS Korea CPI"},
    {"feature_name": "raw_kr_ppi_kosis", "source": "kosis_finance", "variable_name": "raw_kr_ppi_kosis", "unit": "index", "description": "KOSIS Korea PPI"},
    {"feature_name": "raw_kr_house_price_sale", "source": "kosis_finance", "variable_name": "raw_kr_house_price_sale", "unit": "index", "description": "Korea housing sale price index"},
    {"feature_name": "raw_kr_house_price_rent", "source": "kosis_finance", "variable_name": "raw_kr_house_price_rent", "unit": "index", "description": "Korea housing rent price index"},
]

FEATURE_NAMES: list[str] = [item["feature_name"] for item in FEATURE_SPECS]


def _catalog_struct(item: dict[str, str]) -> str:
    return (
        "STRUCT("
        f"'{item['feature_name']}' AS feature_name, "
        f"'{item['source']}' AS source, "
        f"'{item['variable_name']}' AS variable_name, "
        f"'{item['unit']}' AS unit, "
        f"'{item['description']}' AS description_ko)"
    )


_CATALOG_ROWS = ",\n  ".join(_catalog_struct(item) for item in FEATURE_SPECS)

FS_CATALOG_SQL = f"""
CREATE OR REPLACE VIEW {_view('fin_feature_catalog')} AS
SELECT
  feature_name,
  source,
  variable_name,
  unit,
  description_ko
FROM UNNEST([
  {_CATALOG_ROWS}
]) AS t
"""


FS_LONG_SQL = f"""
CREATE OR REPLACE VIEW {_view('fin_fs_long')} AS
WITH catalog AS (
  SELECT * FROM {_view('fin_feature_catalog')}
),
base AS (
  SELECT
    period,
    period_date,
    source,
    variable_name,
    category_key,
    category_name,
    value,
    adjustment_type,
    ingested_at,
    run_id,
    tbl_id
  FROM {_DEDUP}
  WHERE value IS NOT NULL
)
SELECT
  b.period,
  b.period_date,
  c.feature_name,
  b.source,
  b.variable_name,
  b.category_key,
  b.category_name,
  b.value,
  c.unit,
  c.description_ko,
  b.adjustment_type,
  b.ingested_at,
  b.run_id,
  b.tbl_id
FROM base b
INNER JOIN catalog c
  ON b.source = c.source
 AND b.variable_name = c.variable_name
"""


def _wide_col(name: str) -> str:
    return f"  MAX(IF(feature_name = '{name}', value, NULL)) AS {name}"


_WIDE_COLS = ",\n".join(_wide_col(name) for name in FEATURE_NAMES)

FS_WIDE_SQL = f"""
CREATE OR REPLACE VIEW {_view('fin_fs_wide')} AS
WITH filtered AS (
  SELECT *
  FROM {_view('fin_fs_long')}
  WHERE NOT (
    variable_name LIKE 'raw_kr_house_price%'
    AND category_key NOT LIKE '0%'
  )
)
SELECT
  period,
  period_date,
{_WIDE_COLS}
FROM filtered
GROUP BY period, period_date
ORDER BY period
"""


FS_VIEWS: list[tuple[str, str]] = [
    ("fin_feature_catalog", FS_CATALOG_SQL),
    ("fin_fs_long", FS_LONG_SQL),
    ("fin_fs_wide", FS_WIDE_SQL),
]

FS_VIEW_NAMES: list[str] = [name for name, _sql in FS_VIEWS]


def create_fin_fs_views(client: Any, view_names: list[str] | None = None) -> dict[str, Any]:
    target = [(name, sql) for name, sql in FS_VIEWS if view_names is None or name in view_names]
    created: list[str] = []
    failed: dict[str, str] = {}

    for name, sql in target:
        try:
            client.query(sql).result()
            logger.info("[FIN-FS] created %s.%s", BQ_DATASET, name)
            created.append(name)
        except Exception as exc:  # pragma: no cover
            logger.error("[FIN-FS] failed %s: %s", name, exc)
            failed[name] = str(exc)

    return {"created": created, "failed": failed}


def refresh_all_fin_fs(client: Any = None) -> dict[str, Any]:
    if client is None:
        if bigquery is None:
            raise ModuleNotFoundError("google.cloud.bigquery is required to create views")
        client = bigquery.Client(project=GCP_PROJECT_ID)
    return create_fin_fs_views(client)


def get_feature_count() -> int:
    return len(FEATURE_NAMES)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Create finance feature store views")
    parser.add_argument("--view", nargs="*", choices=FS_VIEW_NAMES, metavar="VIEW")
    parser.add_argument("--list-features", action="store_true")
    args = parser.parse_args()

    if args.list_features:
        for idx, feature_name in enumerate(FEATURE_NAMES, 1):
            print(f"{idx:>3}. {feature_name}")
        print(f"\nTotal: {get_feature_count()}")
    else:
        if args.view:
            if bigquery is None:
                raise ModuleNotFoundError("google.cloud.bigquery is required to create views")
            result = create_fin_fs_views(bigquery.Client(project=GCP_PROJECT_ID), args.view)
        else:
            result = refresh_all_fin_fs()
        print(json.dumps(result, ensure_ascii=False, indent=2))
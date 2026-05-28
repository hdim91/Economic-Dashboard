"""
fin-03: ECOS fetcher tests.

Run unit tests:
  py -m pytest pipeline/finance/tests/test_ecos_fetcher.py -v -m "not integration"

Run integration tests:
  $env:ECOS_API_KEY="real-key"
  py -m pytest pipeline/finance/tests/test_ecos_fetcher.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ecos_fetcher import (  # noqa: E402
    ECOS_DATASETS,
    EcosDataset,
    _aggregate_daily_to_monthly,
    _call_ecos_api,
    _normalize,
    _to_ecos_period,
    fetch_all_ecos,
    fetch_ecos_dataset,
)


TEST_RUN_ID = "test-ecos-00000000"


@pytest.fixture
def monthly_dataset():
    return EcosDataset(
        variable_name="kr_base_rate",
        stat_code="722Y001",
        cycle="M",
        item_code1="0101000",
        description="Korean base rate",
        available_from="1999-05",
        unit="%",
    )


@pytest.fixture
def quarterly_dataset():
    return EcosDataset(
        variable_name="kr_household_credit",
        stat_code="151Y001",
        cycle="Q",
        item_code1="1000000",
        description="Household credit",
        available_from="2003-09",
        unit="billion KRW",
    )


@pytest.fixture
def daily_dataset():
    return EcosDataset(
        variable_name="kr_usd_rate",
        stat_code="731Y001",
        cycle="D",
        item_code1="0000001",
        description="KRW/USD exchange rate",
        available_from="1964-01",
        unit="KRW",
        needs_agg_m=True,
    )


class TestNormalize:
    def test_monthly_time_converts_to_period(self, monthly_dataset, ecos_mock_rows):
        result = _normalize(ecos_mock_rows[:1], monthly_dataset, TEST_RUN_ID)
        assert result[0]["period"] == "2024-01"

    def test_daily_time_converts_to_period(self, daily_dataset):
        rows = [
            {
                "TIME": "20240115",
                "DATA_VALUE": "1320.10",
                "ITEM_CODE1": "0000001",
                "ITEM_NAME1": "usd-rate",
                "ITEM_CODE2": "",
                "ITEM_NAME2": "",
            }
        ]
        result = _normalize(rows, daily_dataset, TEST_RUN_ID)
        assert result[0]["period"] == "2024-01"

    def test_missing_data_value_converts_to_none(self, monthly_dataset):
        rows = [
            {
                "TIME": "202401",
                "DATA_VALUE": "-",
                "ITEM_CODE1": "0101000",
                "ITEM_NAME1": "base-rate",
                "ITEM_CODE2": "",
                "ITEM_NAME2": "",
            }
        ]
        result = _normalize(rows, monthly_dataset, TEST_RUN_ID)
        assert result[0]["value"] is None

    def test_run_id_and_source_are_fixed(self, monthly_dataset, ecos_mock_rows):
        result = _normalize(ecos_mock_rows, monthly_dataset, TEST_RUN_ID)
        for record in result:
            assert record["run_id"] == TEST_RUN_ID
            assert record["source"] == "ecos"


class TestToEcosPeriod:
    def test_monthly_period(self):
        assert _to_ecos_period("2024-01", "M") == "202401"

    def test_quarterly_period(self, quarterly_dataset):
        assert _to_ecos_period("2024-01", quarterly_dataset.cycle) == "2024Q1"

    def test_daily_period_start(self):
        assert _to_ecos_period("2024-01", "D", is_start=True) == "20240101"

    def test_daily_period_end(self):
        assert _to_ecos_period("2024-01", "D", is_start=False) == "20240131"


class TestAggregateDailyToMonthly:
    def test_aggregates_three_daily_rows_into_one_month(self):
        records = [
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": 1300.0, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": 1310.0, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": 1320.0, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
        ]
        result = _aggregate_daily_to_monthly(records)
        assert len(result) == 1
        assert result[0]["value"] == 1310.0

    def test_ignores_none_values_in_average(self):
        records = [
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": 1300.0, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": None, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
            {"period": "2024-01", "variable_name": "kr_usd_rate", "category_key": "0000001", "value": 1320.0, "source": "ecos", "adjustment_type": "raw", "ingested_at": "2026-04-17T00:00:00+00:00", "run_id": TEST_RUN_ID, "category_name": "usd-rate", "tbl_id": "731Y001"},
        ]
        result = _aggregate_daily_to_monthly(records)
        assert len(result) == 1
        assert result[0]["value"] == 1310.0


class TestCallEcosApi:
    def test_returns_statistic_search_rows(self, monthly_dataset, ecos_api_key, ecos_mock_rows):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"StatisticSearch": {"row": ecos_mock_rows}}

        with patch("ecos_fetcher.requests.get", return_value=mock_response) as mock_get:
            result = _call_ecos_api(monthly_dataset, "2024-01", "2024-03")

        assert result == ecos_mock_rows
        assert mock_get.call_count == 1

    def test_raises_value_error_for_result_error(self, monthly_dataset, ecos_api_key, ecos_mock_error):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = ecos_mock_error

        with patch("ecos_fetcher.requests.get", return_value=mock_response), patch(
            "ecos_fetcher.time.sleep",
            return_value=None,
        ):
            with pytest.raises(ValueError):
                _call_ecos_api(monthly_dataset, "2024-01", "2024-03")

    def test_retries_three_times_before_failure(self, monthly_dataset, ecos_api_key):
        with patch(
            "ecos_fetcher.requests.get",
            side_effect=requests.RequestException("network error"),
        ) as mock_get, patch("ecos_fetcher.time.sleep", return_value=None):
            with pytest.raises(requests.RequestException):
                _call_ecos_api(monthly_dataset, "2024-01", "2024-03", retries=3)

        assert mock_get.call_count == 3


class TestFetchAllEcos:
    def test_attempts_to_collect_all_datasets(self, ecos_api_key):
        side_effect = [[{"dataset": idx}] for idx, _ds in enumerate(ECOS_DATASETS)]

        with patch(
            "ecos_fetcher.fetch_ecos_dataset",
            side_effect=side_effect,
        ) as mock_fetch:
            result = fetch_all_ecos(TEST_RUN_ID)

        assert len(result) == len(ECOS_DATASETS)
        assert mock_fetch.call_count == len(ECOS_DATASETS)

    def test_needs_agg_dataset_is_aggregated_after_fetch(self, ecos_api_key, daily_dataset):
        daily_rows = [
            {"TIME": "20240101", "DATA_VALUE": "1300.0", "ITEM_CODE1": "0000001", "ITEM_NAME1": "usd-rate", "ITEM_CODE2": "", "ITEM_NAME2": ""},
            {"TIME": "20240102", "DATA_VALUE": "1310.0", "ITEM_CODE1": "0000001", "ITEM_NAME1": "usd-rate", "ITEM_CODE2": "", "ITEM_NAME2": ""},
            {"TIME": "20240103", "DATA_VALUE": "1320.0", "ITEM_CODE1": "0000001", "ITEM_NAME1": "usd-rate", "ITEM_CODE2": "", "ITEM_NAME2": ""},
        ]
        with patch("ecos_fetcher._call_ecos_api", return_value=daily_rows), patch(
            "ecos_fetcher.time.sleep",
            return_value=None,
        ):
            result = fetch_ecos_dataset(daily_dataset, "2024-01", "2024-01", TEST_RUN_ID)

        assert len(result) == 1
        assert result[0]["period"] == "2024-01"
        assert result[0]["value"] == 1310.0


@pytest.mark.integration
class TestEcosIntegration:
    def test_real_api_returns_data(self):
        real_key = os.getenv("ECOS_API_KEY", "")
        if not real_key:
            pytest.skip("ECOS_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-03"

        result = fetch_all_ecos(TEST_RUN_ID)
        assert len(result) > 0

    def test_real_api_record_schema(self):
        real_key = os.getenv("ECOS_API_KEY", "")
        if not real_key:
            pytest.skip("ECOS_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-01"

        result = fetch_all_ecos(TEST_RUN_ID)
        required = {
            "source",
            "period",
            "variable_name",
            "category_key",
            "value",
            "adjustment_type",
            "ingested_at",
            "run_id",
        }
        for record in result[:5]:
            assert required.issubset(record.keys())
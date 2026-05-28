"""
fin-02: Finance KOSIS fetcher tests.

Run unit tests:
  py -m pytest pipeline/finance/tests/test_finance_kosis_fetcher.py -v -m "not integration"

Run integration tests:
  KOSIS_API_KEY=... py -m pytest pipeline/finance/tests/test_finance_kosis_fetcher.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finance_kosis_fetcher import (  # noqa: E402
    FINANCE_KOSIS_DATASETS,
    FinanceKosisDataset,
    _call_kosis_api,
    _normalize,
    fetch_all_finance_kosis,
)


TEST_RUN_ID = "test-kosis-fin-00000000"


@pytest.fixture
def sample_dataset():
    return FinanceKosisDataset(
        variable_name="kr_cpi_kosis",
        org_id="101",
        tbl_id="DT_1J22003",
        adjustment_type="raw",
        description="Korean CPI",
        available_from="1965-01",
        itm_id="13103112810M_102+",
        obj_l1="0+",
        unit="index",
    )


class TestNormalize:
    def test_normalizes_record_count(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        assert len(result) == 2

    def test_required_fields_exist(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        required = {
            "source",
            "period",
            "variable_name",
            "category_key",
            "value",
            "adjustment_type",
            "ingested_at",
            "run_id",
            "category_name",
            "tbl_id",
        }
        for record in result:
            assert required.issubset(record.keys())

    def test_period_conversion(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        assert result[0]["period"] == "2024-01"
        assert result[1]["period"] == "2024-02"

    def test_value_converts_to_float(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        for record in result:
            assert isinstance(record["value"], float)

    def test_missing_values_convert_to_none(
        self,
        sample_dataset,
        kosis_fin_mock_records_with_missing,
    ):
        result = _normalize(
            kosis_fin_mock_records_with_missing,
            sample_dataset,
            TEST_RUN_ID,
        )
        assert result[0]["value"] is not None
        assert result[1]["value"] is None

    def test_source_is_fixed(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        for record in result:
            assert record["source"] == "kosis_finance"

    def test_variable_name_uses_adjustment_prefix(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        expected = f"{sample_dataset.adjustment_type}_{sample_dataset.variable_name}"
        for record in result:
            assert record["variable_name"] == expected

    def test_invalid_prd_de_is_skipped(self, sample_dataset):
        bad_records = [
            {"PRD_DE": "2024", "DT": "100", "C1": "", "C1_NM": "", "C2": "", "C2_NM": "", "C3": "", "C3_NM": "", "ITM_ID": "x", "ITM_NM": ""},
            {"PRD_DE": "ABCDEF", "DT": "100", "C1": "", "C1_NM": "", "C2": "", "C2_NM": "", "C3": "", "C3_NM": "", "ITM_ID": "x", "ITM_NM": ""},
        ]
        assert _normalize(bad_records, sample_dataset, TEST_RUN_ID) == []

    def test_run_id_is_propagated(self, sample_dataset, kosis_fin_mock_records):
        result = _normalize(kosis_fin_mock_records, sample_dataset, TEST_RUN_ID)
        for record in result:
            assert record["run_id"] == TEST_RUN_ID

    def test_empty_input_returns_empty_list(self, sample_dataset):
        assert _normalize([], sample_dataset, TEST_RUN_ID) == []


class TestCallKosisApi:
    def test_returns_records_on_success(
        self,
        sample_dataset,
        kosis_api_key,
        kosis_fin_mock_records,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = kosis_fin_mock_records

        with patch("finance_kosis_fetcher.requests.get", return_value=mock_response) as mock_get:
            result = _call_kosis_api(sample_dataset, "2024-01", "2024-03")

        assert result == kosis_fin_mock_records
        assert mock_get.call_count == 1

    def test_raises_value_error_for_err_field(
        self,
        sample_dataset,
        kosis_api_key,
        kosis_fin_mock_error_response,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = kosis_fin_mock_error_response

        with patch("finance_kosis_fetcher.requests.get", return_value=mock_response), patch(
            "finance_kosis_fetcher.time.sleep",
            return_value=None,
        ):
            with pytest.raises(ValueError):
                _call_kosis_api(sample_dataset, "2024-01", "2024-03")

    def test_retries_http_errors(self, sample_dataset, kosis_api_key):
        with patch(
            "finance_kosis_fetcher.requests.get",
            side_effect=requests.ConnectionError("network error"),
        ) as mock_get, patch("finance_kosis_fetcher.time.sleep", return_value=None):
            with pytest.raises(requests.ConnectionError):
                _call_kosis_api(sample_dataset, "2024-01", "2024-03", retries=3)

        assert mock_get.call_count == 3

    def test_returns_after_retry_success(
        self,
        sample_dataset,
        kosis_api_key,
        kosis_fin_mock_records,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = kosis_fin_mock_records

        with patch(
            "finance_kosis_fetcher.requests.get",
            side_effect=[requests.ConnectionError("temporary"), mock_response],
        ) as mock_get, patch("finance_kosis_fetcher.time.sleep", return_value=None):
            result = _call_kosis_api(sample_dataset, "2024-01", "2024-03", retries=3)

        assert result == kosis_fin_mock_records
        assert mock_get.call_count == 2

    def test_non_list_dict_response_returns_empty_list(self, sample_dataset, kosis_api_key):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"message": "not a list"}

        with patch("finance_kosis_fetcher.requests.get", return_value=mock_response):
            result = _call_kosis_api(sample_dataset, "2024-01", "2024-03")

        assert result == []


class TestFetchAllFinanceKosis:
    def test_collects_all_datasets(self, kosis_api_key, kosis_fin_mock_records):
        with patch(
            "finance_kosis_fetcher._call_kosis_api",
            return_value=kosis_fin_mock_records,
        ) as mock_call, patch("finance_kosis_fetcher.time.sleep", return_value=None):
            result = fetch_all_finance_kosis(TEST_RUN_ID)

        assert len(result) == len(FINANCE_KOSIS_DATASETS) * len(kosis_fin_mock_records)
        assert mock_call.call_count == len(FINANCE_KOSIS_DATASETS)

    def test_skips_failed_datasets(self, kosis_api_key):
        side_effect = []
        for idx, _dataset in enumerate(FINANCE_KOSIS_DATASETS):
            side_effect.append([] if idx % 2 else [{"dataset": idx}])

        with patch(
            "finance_kosis_fetcher.fetch_finance_kosis_dataset",
            side_effect=side_effect,
        ) as mock_fetch:
            result = fetch_all_finance_kosis(TEST_RUN_ID)

        assert len(result) == (len(FINANCE_KOSIS_DATASETS) + 1) // 2
        assert mock_fetch.call_count == len(FINANCE_KOSIS_DATASETS)

    def test_returned_records_follow_schema(self, kosis_api_key, kosis_fin_mock_records):
        required = {
            "source",
            "period",
            "variable_name",
            "category_key",
            "value",
            "adjustment_type",
            "ingested_at",
            "run_id",
            "category_name",
            "tbl_id",
        }

        with patch(
            "finance_kosis_fetcher._call_kosis_api",
            return_value=kosis_fin_mock_records,
        ), patch("finance_kosis_fetcher.time.sleep", return_value=None):
            result = fetch_all_finance_kosis(TEST_RUN_ID)

        assert result
        for record in result:
            assert required.issubset(record.keys())


@pytest.mark.integration
class TestKosisFinIntegration:
    def test_real_api_returns_data(self):
        real_key = os.getenv("KOSIS_API_KEY", "")
        if not real_key:
            pytest.skip("KOSIS_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-03"

        result = fetch_all_finance_kosis(TEST_RUN_ID)
        assert len(result) > 0

    def test_real_api_record_schema(self):
        real_key = os.getenv("KOSIS_API_KEY", "")
        if not real_key:
            pytest.skip("KOSIS_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-01"

        result = fetch_all_finance_kosis(TEST_RUN_ID)
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

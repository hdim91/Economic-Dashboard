"""
fin-01: FRED fetcher tests.

Run unit tests:
  py -m pytest pipeline/finance/tests/test_fred_fetcher.py -v -m "not integration"

Run integration tests:
  FRED_API_KEY=... py -m pytest pipeline/finance/tests/test_fred_fetcher.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fred_fetcher import (  # noqa: E402
    FRED_SERIES,
    FredSeries,
    _call_fred_api,
    _normalize,
    fetch_all_fred,
)


TEST_RUN_ID = "test-fred-00000000"


@pytest.fixture
def sample_series():
    return FredSeries(
        variable_name="us_fed_funds_rate",
        series_id="FEDFUNDS",
        description="US federal funds rate",
        available_from="1954-07",
        unit="%",
        aggregation="avg",
    )


class TestNormalize:
    def test_normalizes_observation_count(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
        assert len(result) == 3

    def test_required_fields_exist(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
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

    def test_period_format(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
        for record in result:
            assert len(record["period"]) == 7
            assert record["period"][4] == "-"

    def test_value_converts_to_float(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
        for record in result:
            assert isinstance(record["value"], float)

    def test_missing_values_convert_to_none(
        self,
        sample_series,
        fred_mock_observations_with_missing,
    ):
        result = _normalize(
            fred_mock_observations_with_missing["observations"],
            sample_series,
            TEST_RUN_ID,
        )
        assert result[0]["value"] == 5.33
        assert result[1]["value"] is None
        assert result[2]["value"] is None

    def test_source_is_fixed(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
        for record in result:
            assert record["source"] == "fred"

    def test_run_id_is_propagated(self, sample_series, fred_mock_observations):
        result = _normalize(fred_mock_observations["observations"], sample_series, TEST_RUN_ID)
        for record in result:
            assert record["run_id"] == TEST_RUN_ID

    def test_empty_observations_return_empty_list(self, sample_series):
        assert _normalize([], sample_series, TEST_RUN_ID) == []


class TestCallFredApi:
    def test_returns_observations_on_success(
        self,
        sample_series,
        fred_api_key,
        fred_mock_observations,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = fred_mock_observations

        with patch("fred_fetcher.requests.get", return_value=mock_response) as mock_get:
            result = _call_fred_api(sample_series, "2024-01", "2024-03")

        assert result == fred_mock_observations["observations"]
        assert mock_get.call_count == 1

    def test_raises_value_error_for_error_code(
        self,
        sample_series,
        fred_api_key,
        fred_mock_error_response,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = fred_mock_error_response

        with patch("fred_fetcher.requests.get", return_value=mock_response), patch(
            "fred_fetcher.time.sleep",
            return_value=None,
        ):
            with pytest.raises(ValueError):
                _call_fred_api(sample_series, "2024-01", "2024-03")

    def test_retries_http_errors(self, sample_series, fred_api_key):
        with patch(
            "fred_fetcher.requests.get",
            side_effect=requests.RequestException("network error"),
        ) as mock_get, patch("fred_fetcher.time.sleep", return_value=None):
            with pytest.raises(requests.RequestException):
                _call_fred_api(sample_series, "2024-01", "2024-03", retries=3)

        assert mock_get.call_count == 3

    def test_returns_after_retry_success(
        self,
        sample_series,
        fred_api_key,
        fred_mock_observations,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = fred_mock_observations

        with patch(
            "fred_fetcher.requests.get",
            side_effect=[requests.RequestException("temporary"), mock_response],
        ) as mock_get, patch("fred_fetcher.time.sleep", return_value=None):
            result = _call_fred_api(sample_series, "2024-01", "2024-03", retries=3)

        assert result == fred_mock_observations["observations"]
        assert mock_get.call_count == 2


class TestFetchAllFred:
    def test_collects_all_series(self, fred_api_key, fred_mock_observations):
        with patch(
            "fred_fetcher._call_fred_api",
            return_value=fred_mock_observations["observations"],
        ) as mock_call, patch("fred_fetcher.time.sleep", return_value=None):
            result = fetch_all_fred(TEST_RUN_ID)

        assert len(result) == len(FRED_SERIES) * len(fred_mock_observations["observations"])
        assert mock_call.call_count == len(FRED_SERIES)

    def test_skips_failed_series(self, fred_api_key):
        side_effect = []
        for idx, _series in enumerate(FRED_SERIES):
            side_effect.append([] if idx % 2 else [{"series": idx}])

        with patch(
            "fred_fetcher.fetch_fred_series",
            side_effect=side_effect,
        ) as mock_fetch:
            result = fetch_all_fred(TEST_RUN_ID)

        assert len(result) == (len(FRED_SERIES) + 1) // 2
        assert mock_fetch.call_count == len(FRED_SERIES)

    def test_returned_records_follow_schema(self, fred_api_key, fred_mock_observations):
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
            "fred_fetcher._call_fred_api",
            return_value=fred_mock_observations["observations"],
        ), patch("fred_fetcher.time.sleep", return_value=None):
            result = fetch_all_fred(TEST_RUN_ID)

        assert result
        for record in result:
            assert required.issubset(record.keys())


@pytest.mark.integration
class TestFredIntegration:
    def test_real_api_returns_data(self):
        real_key = os.getenv("FRED_API_KEY", "")
        if not real_key:
            pytest.skip("FRED_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-03"

        result = fetch_all_fred(TEST_RUN_ID)
        assert len(result) > 0

    def test_real_api_record_schema(self):
        real_key = os.getenv("FRED_API_KEY", "")
        if not real_key:
            pytest.skip("FRED_API_KEY is not set")

        os.environ["PERIOD_START"] = "2024-01"
        os.environ["PERIOD_END"] = "2024-01"

        result = fetch_all_fred(TEST_RUN_ID)
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

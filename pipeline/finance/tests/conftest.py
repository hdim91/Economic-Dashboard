"""
Shared fixtures for finance fetcher tests.
"""

import pytest


@pytest.fixture(autouse=True)
def set_period_env(monkeypatch):
    monkeypatch.setenv("PERIOD_START", "2024-01")
    monkeypatch.setenv("PERIOD_END", "2024-03")


@pytest.fixture
def fred_api_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_fred_key_dummy")


@pytest.fixture
def kosis_api_key(monkeypatch):
    monkeypatch.setenv("KOSIS_API_KEY", "test_kosis_key_dummy")


@pytest.fixture
def ecos_api_key(monkeypatch):
    monkeypatch.setenv("ECOS_API_KEY", "test_ecos_key_dummy")


@pytest.fixture
def fred_mock_observations():
    return {
        "observations": [
            {"date": "2024-01-01", "value": "5.33"},
            {"date": "2024-02-01", "value": "5.33"},
            {"date": "2024-03-01", "value": "5.33"},
        ]
    }


@pytest.fixture
def fred_mock_observations_with_missing():
    return {
        "observations": [
            {"date": "2024-01-01", "value": "5.33"},
            {"date": "2024-02-01", "value": "."},
            {"date": "2024-03-01", "value": ""},
        ]
    }


@pytest.fixture
def fred_mock_error_response():
    return {
        "error_code": 400,
        "error_message": "Bad Request. The series does not exist.",
    }


@pytest.fixture
def kosis_fin_mock_records():
    return [
        {
            "PRD_DE": "202401",
            "DT": "113.24",
            "C1": "0",
            "C1_NM": "all",
            "C2": "",
            "C2_NM": "",
            "C3": "",
            "C3_NM": "",
            "ITM_ID": "13103112810M_102+",
            "ITM_NM": "total-index",
        },
        {
            "PRD_DE": "202402",
            "DT": "113.81",
            "C1": "0",
            "C1_NM": "all",
            "C2": "",
            "C2_NM": "",
            "C3": "",
            "C3_NM": "",
            "ITM_ID": "13103112810M_102+",
            "ITM_NM": "total-index",
        },
    ]


@pytest.fixture
def kosis_fin_mock_records_with_missing():
    return [
        {
            "PRD_DE": "202401",
            "DT": "113.24",
            "C1": "0",
            "C1_NM": "all",
            "C2": "",
            "C2_NM": "",
            "C3": "",
            "C3_NM": "",
            "ITM_ID": "x+",
            "ITM_NM": "total-index",
        },
        {
            "PRD_DE": "202402",
            "DT": "-",
            "C1": "0",
            "C1_NM": "all",
            "C2": "",
            "C2_NM": "",
            "C3": "",
            "C3_NM": "",
            "ITM_ID": "x+",
            "ITM_NM": "total-index",
        },
    ]


@pytest.fixture
def kosis_fin_mock_error_response():
    return {"err": "30", "errMsg": "No matching statistics data."}


@pytest.fixture
def ecos_mock_rows():
    return [
        {
            "TIME": "202401",
            "DATA_VALUE": "3.50",
            "ITEM_CODE1": "0101000",
            "ITEM_NAME1": "base-rate",
            "ITEM_CODE2": "",
            "ITEM_NAME2": "",
        },
        {
            "TIME": "202402",
            "DATA_VALUE": "3.50",
            "ITEM_CODE1": "0101000",
            "ITEM_NAME1": "base-rate",
            "ITEM_CODE2": "",
            "ITEM_NAME2": "",
        },
    ]


@pytest.fixture
def ecos_mock_error():
    return {
        "RESULT": {
            "CODE": "INFO-100",
            "MESSAGE": "No data found.",
        }
    }
import os
import sys
from unittest.mock import MagicMock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finance_feature_store import (  # noqa: E402
    FEATURE_NAMES,
    FS_LONG_SQL,
    FS_VIEWS,
    FS_WIDE_SQL,
    create_fin_fs_views,
    get_feature_count,
)


class TestFinanceFeatureStoreSql:
    def test_fin_fs_long_contains_expected_columns(self):
        required_tokens = [
            "period",
            "period_date",
            "feature_name",
            "value",
            "category_key",
            "run_id",
        ]
        for token in required_tokens:
            assert token in FS_LONG_SQL

    def test_fin_fs_wide_contains_all_feature_columns(self):
        for feature_name in FEATURE_NAMES:
            assert f"AS {feature_name}" in FS_WIDE_SQL


class TestCreateFinFsViews:
    def test_create_fin_fs_views_executes_queries(self):
        client = MagicMock()
        client.query.return_value.result.return_value = None

        result = create_fin_fs_views(client)

        assert client.query.call_count == len(FS_VIEWS)
        assert result["failed"] == {}
        assert len(result["created"]) == len(FS_VIEWS)

    def test_view_names_filter_runs_only_selected_view(self):
        client = MagicMock()
        client.query.return_value.result.return_value = None

        result = create_fin_fs_views(client, view_names=["fin_fs_long"])

        assert client.query.call_count == 1
        assert result["created"] == ["fin_fs_long"]


class TestFeatureCount:
    def test_get_feature_count_returns_positive_number(self):
        assert get_feature_count() > 0

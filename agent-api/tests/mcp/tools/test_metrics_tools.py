"""Unit tests for app.mcp.tools.metrics_tools.

All boto3 calls are mocked so tests run without AWS credentials.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.tools.metrics_tools import (
    get_metric_data,
    get_metric_statistics,
    list_metric_alarms,
)


# ---------------------------------------------------------------------------
# get_metric_data
# ---------------------------------------------------------------------------

class TestGetMetricData:
    @pytest.mark.asyncio
    async def test_returns_time_series(self):
        now = datetime.now(timezone.utc)
        mock_response = {
            "MetricDataResults": [
                {
                    "Id": "errors",
                    "Label": "Lambda Errors",
                    "Timestamps": [now],
                    "Values": [5.0],
                    "StatusCode": "Complete",
                }
            ]
        }

        with patch("app.mcp.tools.metrics_tools._get_metrics_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_metric_data = MagicMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await get_metric_data(
                metric_queries=[{
                    "Id": "errors",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/Lambda",
                            "MetricName": "Errors",
                            "Dimensions": [{"Name": "FunctionName", "Value": "kyc-auth"}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                }],
                time_range_minutes=60,
            )

        assert result["success"] is True
        assert "errors" in result["metrics"]
        assert result["metrics"]["errors"]["values"] == [5.0]
        assert result["metrics"]["errors"]["summary"]["total"] == 5.0

    @pytest.mark.asyncio
    async def test_summary_computed(self):
        now = datetime.now(timezone.utc)
        mock_response = {
            "MetricDataResults": [
                {
                    "Id": "m1",
                    "Label": "CPU",
                    "Timestamps": [now, now, now],
                    "Values": [10.0, 20.0, 30.0],
                    "StatusCode": "Complete",
                }
            ]
        }

        with patch("app.mcp.tools.metrics_tools._get_metrics_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_metric_data = MagicMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await get_metric_data(metric_queries=[{"Id": "m1"}])

        s = result["metrics"]["m1"]["summary"]
        assert s["count"] == 3
        assert s["total"] == 60.0
        assert s["average"] == 20.0
        assert s["maximum"] == 30.0
        assert s["minimum"] == 10.0


# ---------------------------------------------------------------------------
# get_metric_statistics
# ---------------------------------------------------------------------------

class TestGetMetricStatistics:
    @pytest.mark.asyncio
    async def test_returns_sorted_datapoints(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        mock_response = {
            "Datapoints": [
                {"Timestamp": now, "Sum": 3.0, "Average": 1.5},
                {"Timestamp": now - timedelta(minutes=5), "Sum": 1.0, "Average": 0.5},
            ]
        }

        with patch("app.mcp.tools.metrics_tools._get_metrics_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_metric_statistics = MagicMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await get_metric_statistics(
                namespace="AWS/Lambda",
                metric_name="Errors",
                dimensions=[{"Name": "FunctionName", "Value": "kyc-auth"}],
            )

        assert result["success"] is True
        # Should be sorted by Timestamp ascending.
        assert result["datapoints"][0]["Sum"] == 1.0
        assert result["datapoints"][1]["Sum"] == 3.0


# ---------------------------------------------------------------------------
# list_metric_alarms
# ---------------------------------------------------------------------------

class TestListMetricAlarms:
    @pytest.mark.asyncio
    async def test_lists_firing_alarms(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        mock_response = {
            "MetricAlarms": [
                {
                    "AlarmName": "HighErrorRate",
                    "StateValue": "ALARM",
                    "StateReason": "Threshold crossed",
                    "MetricName": "Errors",
                    "Namespace": "AWS/Lambda",
                    "Threshold": 10.0,
                    "ComparisonOperator": "GreaterThanThreshold",
                    "Period": 300,
                    "Statistic": "Sum",
                    "EvaluationPeriods": 1,
                    "StateUpdatedTimestamp": now,
                    "ActionsEnabled": True,
                    "AlarmArn": "arn:aws:cloudwatch:us-east-1:123:alarm:HighErrorRate",
                    "Dimensions": [],
                }
            ]
        }

        with patch("app.mcp.tools.metrics_tools._get_metrics_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.describe_alarms = MagicMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await list_metric_alarms(state_value="ALARM")

        assert result["success"] is True
        assert result["summary"]["in_alarm"] == 1
        assert result["alarms"][0]["name"] == "HighErrorRate"
        assert result["alarms"][0]["state"] == "ALARM"

    @pytest.mark.asyncio
    async def test_summary_counts_by_state(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        mock_response = {
            "MetricAlarms": [
                {"AlarmName": "A1", "StateValue": "ALARM", "StateUpdatedTimestamp": now,
                 "MetricName": "X", "Namespace": "N", "Threshold": 1,
                 "ComparisonOperator": "GT", "Dimensions": []},
                {"AlarmName": "A2", "StateValue": "OK", "StateUpdatedTimestamp": now,
                 "MetricName": "X", "Namespace": "N", "Threshold": 1,
                 "ComparisonOperator": "GT", "Dimensions": []},
                {"AlarmName": "A3", "StateValue": "INSUFFICIENT_DATA", "StateUpdatedTimestamp": now,
                 "MetricName": "X", "Namespace": "N", "Threshold": 1,
                 "ComparisonOperator": "GT", "Dimensions": []},
            ]
        }

        with patch("app.mcp.tools.metrics_tools._get_metrics_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.describe_alarms = MagicMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await list_metric_alarms()

        assert result["summary"]["total"] == 3
        assert result["summary"]["in_alarm"] == 1
        assert result["summary"]["ok"] == 1
        assert result["summary"]["insufficient_data"] == 1

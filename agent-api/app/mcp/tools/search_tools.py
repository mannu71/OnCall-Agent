import asyncio
import functools
import boto3
import json
import re
import time
from datetime import datetime
from typing import List

from . import handle_exceptions
from .utils import get_time_range

# Hard ceiling on rows returned from a single Insights query. Raising this is a
# cost decision, not a correctness one — the row payload is the dominant token
# cost of a search, and callers budget against it.
MAX_QUERY_LIMIT = 100

# `| limit N` as a pipeline stage (also matches a leading `limit N`). The
# fix-up path appends "\n| limit 100", so the newline has to be tolerated.
_LIMIT_RE = re.compile(r"(?:^|\|)\s*limit\s+(\d+)\s*(?=\||$)", re.IGNORECASE)


def parse_query_limit(query: str, ceiling: int = MAX_QUERY_LIMIT) -> int:
    """Return the row limit to request, honouring the query's own `| limit N`.

    ``start_query`` takes a ``limit`` parameter that bounds the rows returned,
    and it was pinned at the ceiling regardless of what the caller's query asked
    for. An agent that deliberately requested a small sample still paid for a
    full-size payload, which the token budget then discarded by position —
    measured live 2026-07-27, a query ending ``| limit 5`` returned 100 rows of
    40,733 matching, ~23,700 tokens against a 1,500-token budget.

    When several `limit` stages are present the last one governs the output, so
    that is the one used. Missing, malformed, or non-positive values fall back
    to *ceiling*, and an over-large request is clamped to it.
    """
    found = _LIMIT_RE.findall(query or "")
    if not found:
        return ceiling
    try:
        requested = int(found[-1])
    except (TypeError, ValueError):
        return ceiling
    return min(requested, ceiling) if requested > 0 else ceiling


class CloudWatchLogsSearchTools:
    """Tools for searching and querying CloudWatch Logs."""

    def __init__(self, profile_name=None, region_name=None):
        """Initialize the CloudWatch Logs client.

        Args:
            profile_name: Optional AWS profile name to use for credentials
            region_name: Optional AWS region name to use for API calls
        """
        # Initialize boto3 CloudWatch Logs client using specified profile/region or default credential chain
        self.profile_name = profile_name
        self.region_name = region_name
        session = boto3.Session(profile_name=profile_name, region_name=region_name)
        self.logs_client = session.client("logs")

    @handle_exceptions
    async def search_logs(
        self,
        log_group_name: str,
        query: str,
        hours: int = 24,
        start_time: str = None,
        end_time: str = None,
    ) -> str:
        """
        Search logs using CloudWatch Logs Insights query.

        Args:
            log_group_name: The log group to search
            query: CloudWatch Logs Insights query syntax
            hours: Number of hours to look back
            start_time: Start time in ISO8601 format
            end_time: End time in ISO8601 format

        Returns:
            JSON string with search results
        """
        return await self.search_logs_multi(
            [log_group_name], query, hours, start_time, end_time
        )

    @handle_exceptions
    async def search_logs_multi(
        self,
        log_group_names: List[str],
        query: str,
        hours: int = 24,
        start_time: str = None,
        end_time: str = None,
    ) -> str:
        """
        Search logs across multiple log groups using CloudWatch Logs Insights query.

        Args:
            log_group_names: List of log groups to search
            query: CloudWatch Logs Insights query syntax
            hours: Number of hours to look back
            start_time: Start time in ISO8601 format
            end_time: End time in ISO8601 format

        Returns:
            JSON string with search results
        """
        start_ts, end_ts = get_time_range(hours, start_time, end_time)
        # Start the query
        query_start_time = time.time()
        loop = asyncio.get_running_loop()
        start_query_response = await loop.run_in_executor(
            None,
            functools.partial(
                self.logs_client.start_query,
                logGroupNames=log_group_names,
                startTime=start_ts,
                endTime=end_ts,
                queryString=query,
                limit=parse_query_limit(query),
            ),
        )
        query_id = start_query_response["queryId"]

        # Poll for query results
        response = None
        while response is None or response["status"] == "Running":
            await asyncio.sleep(1)  # Wait before checking again
            response = await loop.run_in_executor(
                None,
                functools.partial(self.logs_client.get_query_results, queryId=query_id),
            )
            elapsed_time = time.time() - query_start_time

            # Avoid long-running queries
            if response["status"] == "Running":
                # Check if we've been running too long (60 seconds)
                if elapsed_time > 60:
                    return json.dumps(
                        {
                            "status": "Timeout",
                            "error": "Search query failed to complete within time limit",
                        },
                        indent=2,
                    )

        # Process and format the results
        formatted_results = {
            "status": response["status"],
            "statistics": response.get("statistics", {}),
            "searchedLogGroups": log_group_names,
            "results": [],
        }

        for result in response.get("results", []):
            result_dict = {}
            for field in result:
                result_dict[field["field"]] = field["value"]
            formatted_results["results"].append(result_dict)

        return json.dumps(formatted_results)

    @handle_exceptions
    async def filter_log_events(
        self,
        log_group_name: str,
        filter_pattern: str,
        hours: int = 24,
        start_time: str = None,
        end_time: str = None,
    ) -> str:
        """
        Filter log events by pattern across all streams in a log group.

        Args:
            log_group_name: The log group to filter
            filter_pattern: The pattern to search for (CloudWatch Logs filter syntax)
            hours: Number of hours to look back
            start_time: Start time in ISO8601 format
            end_time: End time in ISO8601 format

        Returns:
            JSON string with filtered events
        """
        start_ts, end_ts = get_time_range(hours, start_time, end_time)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            functools.partial(
                self.logs_client.filter_log_events,
                logGroupName=log_group_name,
                filterPattern=filter_pattern,
                startTime=start_ts,
                endTime=end_ts,
                limit=100,
            ),
        )

        events = response.get("events", [])
        formatted_events = []

        for event in events:
            formatted_events.append(
                {
                    "timestamp": datetime.fromtimestamp(
                        event.get("timestamp", 0) / 1000
                    ).isoformat(),
                    "message": event.get("message"),
                    "logStreamName": event.get("logStreamName"),
                }
            )

        return json.dumps(formatted_events)

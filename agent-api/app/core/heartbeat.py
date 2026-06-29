"""Proactive on-call monitoring — heartbeat that polls CloudWatch alarms
and auto-triggers investigation workflows when new ALARM-state alerts appear.

Architecture
------------
``HeartbeatMonitor`` runs as a single background asyncio task.  Each tick:

  1. Fetch all CloudWatch alarms currently in ALARM state via boto3.
  2. Diff against the ``_seen`` registry (alarm ARN → first-seen timestamp).
  3. For each *new* alarm that matches an :class:`AlarmRule`, trigger the
     mapped investigation workflow via ``visual_executor``.
  4. Respect a per-alarm cooldown so a flapping alarm doesn't create a new
     investigation every 60 seconds.
  5. Sleep until the next tick.

If no CloudWatch credentials are configured the monitor degrades gracefully
to polling the local ``alerts`` DB table for new CRITICAL entries — useful
for local development without AWS access.

Lifecycle
---------
Call ``heartbeat_monitor.start()`` in the FastAPI lifespan startup handler
and ``heartbeat_monitor.stop()`` in the shutdown handler.  Both are no-ops
if the monitor is already in the desired state.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.thread_pools import run_in_aws_pool

logger = logging.getLogger(__name__)

_SEEN_TTL = timedelta(hours=settings.heartbeat_seen_ttl_hours)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AlarmRule:
    """Maps a CloudWatch alarm name pattern to a workflow that investigates it.

    Attributes:
        alarm_pattern:  Regex matched against the alarm name.
        workflow_name:  Name of the visual workflow to trigger.
        min_severity:   Only trigger for alarms at this severity or above.
                        CloudWatch itself doesn't have a native severity field;
                        we derive severity from the alarm name prefix
                        (CRITICAL_ / HIGH_ / etc.) or treat all as CRITICAL.
        query_template: Template for the investigation query.
                        ``{alarm_name}`` and ``{alarm_reason}`` are substituted.
    """
    alarm_pattern:  str
    workflow_name:  str
    min_severity:   str = "CRITICAL"
    query_template: str = (
        "Investigate the following CloudWatch alarm that entered ALARM state: "
        "{alarm_name}. Alarm reason: {alarm_reason}. "
        "Identify the root cause and recommend a resolution."
    )


@dataclass
class HeartbeatConfig:
    """Runtime configuration for the heartbeat monitor."""

    poll_interval_seconds: int = 60
    cooldown_seconds: int = 300
    max_concurrent_triggers: int = 3
    aws_region: str = "us-east-1"
    aws_profile: Optional[str] = None
    alarm_rules: List[AlarmRule] = field(default_factory=list)
    db_fallback_enabled: bool = True

    @classmethod
    def from_settings(cls) -> "HeartbeatConfig":
        return cls(
            poll_interval_seconds=settings.heartbeat_poll_interval,
            cooldown_seconds=settings.heartbeat_cooldown,
            max_concurrent_triggers=settings.heartbeat_max_concurrent,
            aws_region=settings.heartbeat_aws_region,
            aws_profile=settings.heartbeat_aws_profile,
            db_fallback_enabled=settings.heartbeat_db_fallback,
        )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class HeartbeatMonitor:
    """Background async task that proactively monitors for new alarms.

    Usage (in main.py lifespan)::

        heartbeat_monitor.start()   # non-blocking
        yield
        await heartbeat_monitor.stop()
    """

    def __init__(self, config: Optional[HeartbeatConfig] = None) -> None:
        self._cfg   = config or HeartbeatConfig.from_settings()
        self._task: Optional[asyncio.Task] = None
        self._stop  = asyncio.Event()

        # alarm_arn → datetime of first trigger (or last cooldown reset).
        self._seen:  Dict[str, datetime] = {}
        self._deferred: List[Dict[str, Any]] = []

        # Cache CloudWatch boto3 client (created lazily on first poll).
        self._cw_client: Optional[Any] = None
        self._cw_available: Optional[bool] = None  # None = not yet tested

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background polling loop (non-blocking)."""
        if self._task and not self._task.done():
            logger.debug("HeartbeatMonitor: already running")
            return

        self._stop.clear()
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run_loop(), name="heartbeat_monitor")
            logger.info(
                "HeartbeatMonitor: started (poll_interval=%ds cooldown=%ds)",
                self._cfg.poll_interval_seconds,
                self._cfg.cooldown_seconds,
            )
        except RuntimeError:
            logger.warning("HeartbeatMonitor: no running event loop — skipping start")

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to exit cleanly."""
        self._stop.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        logger.info("HeartbeatMonitor: stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ------------------------------------------------------------------
    # Alarm rules management
    # ------------------------------------------------------------------

    def add_rule(self, rule: AlarmRule) -> None:
        """Register an alarm rule at runtime."""
        self._cfg.alarm_rules.append(rule)
        logger.info(
            "HeartbeatMonitor: added rule pattern='%s' workflow='%s'",
            rule.alarm_pattern, rule.workflow_name,
        )

    def clear_rules(self) -> None:
        """Remove all registered alarm rules."""
        self._cfg.alarm_rules.clear()

    async def load_rules_from_db(self) -> int:
        """Load alarm rules from workflow metadata stored in the DB.

        Looks for workflows whose ``metadata`` JSON contains a
        ``heartbeat_rules`` array.  Each entry must have at minimum an
        ``alarm_pattern`` key.  Returns the number of rules loaded.
        Non-fatal — any error is silently ignored.
        """
        try:
            from app.infrastructure.persistence import workflow_repository

            workflows = await workflow_repository.list_all()
            count = 0
            for wf in workflows or []:
                metadata = wf.get("metadata") or {}
                if isinstance(metadata, str):
                    import json as _json
                    try:
                        metadata = _json.loads(metadata)
                    except Exception:
                        metadata = {}
                for r in (metadata.get("heartbeat_rules") or []):
                    if not r.get("alarm_pattern") or not r.get("workflow_name"):
                        continue
                    self.add_rule(AlarmRule(
                        alarm_pattern  = r["alarm_pattern"],
                        workflow_name  = r["workflow_name"],
                        min_severity   = r.get("min_severity", "CRITICAL"),
                        query_template = r.get("query_template", AlarmRule.query_template),
                    ))
                    count += 1
            return count
        except Exception as exc:
            logger.debug("HeartbeatMonitor: load_rules_from_db skipped — %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Tick → poll → act → sleep, until stop is signalled."""
        # Try to load DB rules before the first tick.
        loaded = await self.load_rules_from_db()
        if loaded:
            logger.info("HeartbeatMonitor: loaded %d rule(s) from DB", loaded)

        while not self._stop.is_set():
            try:
                await self._poll_and_act()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "HeartbeatMonitor: unhandled error in poll cycle — %s",
                    exc, exc_info=True,
                )

            # Sleep in small chunks so stop() responds quickly.
            remaining = self._cfg.poll_interval_seconds
            while remaining > 0 and not self._stop.is_set():
                await asyncio.sleep(min(5, remaining))
                remaining -= 5

        logger.debug("HeartbeatMonitor: loop exited cleanly")

    # ------------------------------------------------------------------
    # Poll + act
    # ------------------------------------------------------------------

    async def _poll_and_act(self) -> None:
        """Fetch active alarms, find new ones, trigger workflows."""
        alarms = await self._fetch_active_alarms()
        if self._deferred:
            alarms = self._deferred + (alarms or [])
            self._deferred = []
        if not alarms:
            return

        now       = datetime.now(timezone.utc)
        cooldown  = timedelta(seconds=self._cfg.cooldown_seconds)
        triggered = 0

        trigger_tasks: List[asyncio.Task] = []

        for idx, alarm in enumerate(alarms):
            alarm_arn    = alarm.get("AlarmArn", alarm.get("AlarmName", ""))
            alarm_name   = alarm.get("AlarmName", "")
            alarm_reason = alarm.get("StateReason", "")

            # Skip if still in cooldown window.
            last_seen = self._seen.get(alarm_arn)
            if last_seen and (now - last_seen) < cooldown:
                continue

            # Match against rules.
            matched_rule = self._match_rule(alarm_name)
            if not matched_rule:
                continue

            logger.info(
                "HeartbeatMonitor: new alarm '%s' matched rule '%s' → triggering '%s'",
                alarm_name, matched_rule.alarm_pattern, matched_rule.workflow_name,
            )

            self._seen[alarm_arn] = now

            if triggered >= self._cfg.max_concurrent_triggers:
                logger.info(
                    "HeartbeatMonitor: max_concurrent_triggers=%d reached; "
                    "queuing %d alarm(s) for next tick",
                    self._cfg.max_concurrent_triggers,
                    len(alarms) - idx,
                )
                self._deferred.extend(alarms[idx:])
                break

            query = matched_rule.query_template.format(
                alarm_name   = alarm_name,
                alarm_reason = alarm_reason or "not specified",
            )
            trigger_tasks.append(
                asyncio.create_task(
                    self._trigger_investigation(
                        matched_rule.workflow_name, query, alarm
                    ),
                    name=f"heartbeat_trigger_{alarm_name[:40]}",
                )
            )
            triggered += 1

        if trigger_tasks:
            await asyncio.gather(*trigger_tasks, return_exceptions=True)

        self._prune_seen(now)

    def _prune_seen(self, now: datetime) -> None:
        """Drop alarm ARNs not referenced within the TTL window."""
        cutoff = now - _SEEN_TTL
        expired = [arn for arn, seen_at in self._seen.items() if seen_at < cutoff]
        for arn in expired:
            del self._seen[arn]

    async def _fetch_active_alarms(self) -> List[Dict[str, Any]]:
        """Return all CloudWatch alarms in ALARM state.

        Falls back to the DB alerts table when CloudWatch is unavailable.
        """
        if self._cw_available is not False:
            try:
                return await self._fetch_cloudwatch_alarms()
            except Exception as exc:
                logger.warning(
                    "HeartbeatMonitor: CloudWatch fetch failed (%s); "
                    "switching to DB fallback",
                    exc,
                )
                self._cw_available = False

        if self._cfg.db_fallback_enabled:
            return await self._fetch_db_alerts()

        return []

    async def _fetch_cloudwatch_alarms(self) -> List[Dict[str, Any]]:
        """Call ``describe_alarms(StateValue='ALARM')`` asynchronously."""
        client = await self._get_cw_client()
        if client is None:
            raise RuntimeError("CloudWatch client unavailable")

        response = await run_in_aws_pool(
            lambda: client.describe_alarms(StateValue="ALARM", MaxRecords=50),
        )

        alarms = (
            response.get("MetricAlarms", [])
            + response.get("CompositeAlarms", [])
        )
        self._cw_available = True

        if alarms:
            logger.debug(
                "HeartbeatMonitor: %d active CloudWatch alarm(s)", len(alarms)
            )
        return alarms

    async def _fetch_db_alerts(self) -> List[Dict[str, Any]]:
        """Poll the local DB alerts table for CRITICAL alerts not yet seen."""
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.db_models import AlertModel
            from sqlalchemy import select

            since = datetime.now(timezone.utc) - timedelta(
                seconds=self._cfg.poll_interval_seconds * 2
            )

            async with AsyncSessionLocal() as session:
                q = (
                    select(AlertModel)
                    .where(AlertModel.severity == "CRITICAL")
                    .where(AlertModel.created_at >= since)
                    .order_by(AlertModel.created_at.desc())
                    .limit(20)
                )
                rows = (await session.execute(q)).scalars().all()

            # Map DB model to CloudWatch-compatible dict shape.
            return [
                {
                    "AlarmArn":    f"db-alert-{row.id}",
                    "AlarmName":   row.message or f"CRITICAL-{row.id}",
                    "StateReason": row.details or "",
                }
                for row in rows
            ]
        except Exception as exc:
            logger.debug("HeartbeatMonitor: DB fallback failed — %s", exc)
            return []

    # ------------------------------------------------------------------
    # Workflow trigger
    # ------------------------------------------------------------------

    async def _trigger_investigation(
        self,
        workflow_name: str,
        query:         str,
        alarm:         Dict[str, Any],
    ) -> None:
        """Look up and execute the named workflow via the visual executor."""
        try:
            from app.infrastructure.persistence import workflow_repository
            from app.workflow.routing import execute_visual_workflow

            workflow_data = await workflow_repository.get_by_name(workflow_name)
            if not workflow_data:
                logger.warning(
                    "HeartbeatMonitor: workflow '%s' not found in DB; "
                    "skipping trigger for alarm '%s'",
                    workflow_name, alarm.get("AlarmName"),
                )
                return

            # Inject the heartbeat query as the investigation input.
            inputs = {
                "user_query":     query,
                "trigger_source": "heartbeat",
                "alarm_name":     alarm.get("AlarmName", ""),
                "alarm_reason":   alarm.get("StateReason", ""),
            }

            logger.info(
                "HeartbeatMonitor: triggering workflow '%s' for alarm '%s'",
                workflow_name, alarm.get("AlarmName"),
            )

            result = await execute_visual_workflow(workflow_data, inputs=inputs)

            status = (result or {}).get("status", "unknown")
            logger.info(
                "HeartbeatMonitor: workflow '%s' completed with status '%s'",
                workflow_name, status,
            )

        except Exception as exc:
            logger.error(
                "HeartbeatMonitor: failed to trigger workflow '%s' — %s",
                workflow_name, exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # CloudWatch client
    # ------------------------------------------------------------------

    async def _get_cw_client(self) -> Optional[Any]:
        """Return a cached boto3 CloudWatch client, or None on failure."""
        if self._cw_client is not None:
            return self._cw_client
        try:
            import boto3
            from botocore.config import Config as BotocoreConfig
            from app.core.aws_credentials import resolve_aws_credentials

            creds, region = await resolve_aws_credentials(
                aws_profile=self._cfg.aws_profile,
                aws_region=self._cfg.aws_region,
            )

            boto_cfg = BotocoreConfig(retries={"max_attempts": 2})

            if creds.get("access_key_id"):
                session = boto3.Session(
                    aws_access_key_id     = creds["access_key_id"],
                    aws_secret_access_key = creds.get("secret_access_key"),
                    aws_session_token     = creds.get("session_token"),
                    region_name           = region,
                )
            elif creds.get("aws_profile"):
                session = boto3.Session(
                    profile_name = creds["aws_profile"],
                    region_name  = region,
                )
            else:
                session = boto3.Session(region_name=region)

            self._cw_client = session.client(
                "cloudwatch",
                region_name = region,
                config      = boto_cfg,
                verify=settings.aws_ssl_verify,
            )
            logger.info(
                "HeartbeatMonitor: CloudWatch client ready (region=%s)", region
            )
            return self._cw_client

        except Exception as exc:
            logger.warning(
                "HeartbeatMonitor: could not create CloudWatch client — %s", exc
            )
            self._cw_available = False
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _match_rule(self, alarm_name: str) -> Optional[AlarmRule]:
        """Return the first matching alarm rule, or None."""
        for rule in self._cfg.alarm_rules:
            try:
                if re.search(rule.alarm_pattern, alarm_name, re.IGNORECASE):
                    return rule
            except re.error:
                if rule.alarm_pattern.lower() in alarm_name.lower():
                    return rule
        return None

    def status(self) -> Dict[str, Any]:
        """Return a snapshot of monitor state for health-check endpoints."""
        return {
            "running":        self.is_running,
            "poll_interval":  self._cfg.poll_interval_seconds,
            "cooldown":       self._cfg.cooldown_seconds,
            "rules":          len(self._cfg.alarm_rules),
            "seen_alarms":    len(self._seen),
            "cw_available":   self._cw_available,
        }


# ---------------------------------------------------------------------------
# Global singleton — imported by main.py lifespan and health routes
# ---------------------------------------------------------------------------
heartbeat_monitor = HeartbeatMonitor()

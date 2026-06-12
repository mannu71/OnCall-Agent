"""CloudWatch analysis helpers — static summaries, alerts, and LLM synthesis."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Pipeline-synthesis guardrail circuit breaker ─────────────────────────────
# The pipeline's single Bedrock synthesis call is frequently blocked by an
# account-level Bedrock guardrail (returns a ~45-char refusal), wasting ~24s +
# tokens every run. Track consecutive refusals and skip the call once a cooldown
# threshold is reached; one usable synthesis resets it. State is process-local
# (good enough — a container restart re-probes once).
_GUARDRAIL_REFUSALS = 0


def _synthesis_circuit_open() -> bool:
    from app.config import settings
    return _GUARDRAIL_REFUSALS >= max(1, settings.cloudwatch_synthesis_guardrail_cooldown)


def _record_guardrail_refusal() -> None:
    global _GUARDRAIL_REFUSALS
    _GUARDRAIL_REFUSALS += 1


def _reset_guardrail_refusals() -> None:
    global _GUARDRAIL_REFUSALS
    _GUARDRAIL_REFUSALS = 0


def build_cloudwatch_summary(
    result: Dict[str, Any],
    analysis_type: str,
    log_groups: list,
) -> str:
    """Build a human-readable summary from CloudWatch analysis results."""
    if not isinstance(result, dict):
        return f"Analysis complete ({analysis_type})"

    if analysis_type == 'error-patterns':
        patterns = result.get('patterns', {})
        pattern_summaries = []
        for ptype, pdata in patterns.items():
            status = pdata.get('status', 'unknown')
            if status == 'Complete':
                data_count = len(pdata.get('data', []))
                pattern_summaries.append(f"{ptype}: {data_count} results")
            else:
                pattern_summaries.append(f"{ptype}: {status}")
        return (
            f"Pattern analysis: {', '.join(pattern_summaries)} "
            f"across {len(log_groups)} log group(s)"
        )

    if analysis_type == 'activity-summary':
        summary = result.get('summary', {})
        total_events = summary.get('total_events', 0)
        successful = summary.get('successful_log_groups', 0)
        return (
            f"Activity summary: {total_events} events across "
            f"{successful}/{len(log_groups)} log group(s)"
        )

    if analysis_type == 'anomaly-detection':
        anomaly_summary = result.get('summary', {})
        total = anomaly_summary.get('total_anomalies', 0)
        critical = anomaly_summary.get('critical_severity', 0)
        high = anomaly_summary.get('high_severity', 0)
        medium = anomaly_summary.get('medium_severity', 0)
        low = anomaly_summary.get('low_severity', 0)
        return (
            f"Anomaly detection: {total} anomalies "
            f"(critical: {critical}, high: {high}, medium: {medium}, low: {low})"
        )

    if analysis_type == 'correlation':
        summary = result.get('summary', {})
        total_events = summary.get('total_events', 0)
        services = summary.get('services_involved', [])
        return (
            f"Log correlation: {total_events} events "
            f"across {len(services)} service(s)"
        )

    return f"Analysis complete ({analysis_type})"


# Phrases that mean the LLM refused / produced a non-answer. When the synthesis
# comes back like this (often a provider/guardrail canned response with no token
# usage), we must NOT show it as the result — fall back to the deterministic
# evidence report instead.
_REFUSAL_MARKERS = (
    "cannot answer", "can't answer", "can not answer", "unable to answer",
    "cannot help with", "i'm sorry", "i am sorry", "as an ai",
    "cannot provide", "cannot assist",
)


def is_usable_synthesis(text: Optional[str]) -> bool:
    """True when an LLM synthesis is a real answer, not empty/refusal/stub."""
    t = (text or "").strip()
    if len(t) < 60:
        return False
    low = t.lower()
    return not any(m in low for m in _REFUSAL_MARKERS)


def _fmt_drilldown_events(events: Any, max_chars: int = 2000) -> str:
    """Render drill-down raw events (a JSON string or dict) as readable lines.

    The drill-down Insights query returns @timestamp/@message/@logStream/@log —
    @message carries correlation/request/trace IDs, so we surface it verbatim.
    """
    import json as _json
    if isinstance(events, str):
        try:
            events = _json.loads(events)
        except Exception:
            return events[:max_chars]
    rows = []
    results = events.get("results") if isinstance(events, dict) else events
    if isinstance(results, list):
        for row in results[:10]:
            # Insights rows are lists of {field, value} dicts.
            if isinstance(row, list):
                fields = {f.get("field"): f.get("value") for f in row if isinstance(f, dict)}
                ts = fields.get("@timestamp", "")
                msg = fields.get("@message", "")
                stream = fields.get("@logStream", "")
                rows.append(f"  [{ts}] {stream}  {msg}".rstrip())
            elif isinstance(row, dict):
                rows.append("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
    text = "\n".join(rows) if rows else (str(events))
    return text[:max_chars]


def _clean_log_line(msg: Any) -> str:
    """Render a raw log line readably for reports.

    If the line is structured JSON (Serilog ``{"@t":…,"@m":…}`` etc.), surface the
    human message (`@m`/`@message`) plus any exception — keeping identifiers that
    live in the message — instead of dumping the full escaped JSON blob (which
    reads as "raw"). Falls back to the raw text for non-JSON lines.
    """
    s = str(msg or "").strip()
    if s.startswith("{") and ('"@m"' in s or '"@message"' in s or '"message"' in s):
        try:
            obj = json.loads(s)
            m = obj.get("@m") or obj.get("@message") or obj.get("message")
            if m:
                ex = obj.get("@x") or obj.get("@l") or obj.get("exception")
                return (str(m) + (f"  |  {str(ex)[:200]}" if ex else "")).strip()
        except Exception:  # noqa: BLE001 — best-effort prettifier
            pass
    return s


def build_synthesis_payload(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Compact evidence for the LLM synthesis — with REAL identifiers intact.

    This is an internal root-cause-analysis tool, so correlation / profile /
    trace IDs and raw error text are preserved verbatim (they are exactly what
    RCA needs). We only keep the payload *compact* (top-N + per-field char caps,
    tunable via settings) for token budget — no PII masking. Genuine secrets are
    still stripped later by ``redact()``.
    """
    if not isinstance(evidence, dict):
        return evidence

    from app.config import settings
    _ex_cap = settings.cloudwatch_example_msg_chars
    _drill_cap = settings.cloudwatch_drill_sample_chars

    out: Dict[str, Any] = {}
    if evidence.get("data_quality"):
        out["data_quality"] = evidence["data_quality"]
    alarms = evidence.get("alarms")
    if isinstance(alarms, dict) and alarms.get("summary"):
        out["alarms_summary"] = alarms["summary"]

    patterns = evidence.get("patterns") or {}
    uniq = patterns.get("unique_patterns") if isinstance(patterns, dict) else None
    if uniq:
        out["error_patterns"] = [
            {
                "pattern": str(p.get("normalized_pattern", ""))[:400],
                "occurrence_count": p.get("occurrence_count"),
                "affected_streams": p.get("affected_streams"),
                "example": _clean_log_line(p.get("example_message", ""))[:_ex_cap],
                "first_seen": p.get("first_seen"),
                "last_seen": p.get("last_seen"),
            }
            for p in uniq[:10] if isinstance(p, dict)
        ]

    anomalies = evidence.get("anomalies") or {}
    if anomalies.get("anomalies"):
        out["anomalies"] = anomalies["anomalies"][:10]
        out["anomaly_summary"] = anomalies.get("summary")

    # Drill-down samples give the model concrete raw context (with real IDs) to
    # reason about root cause.
    drill = evidence.get("drilldown") or []
    if isinstance(drill, list) and drill:
        out["drilldown_samples"] = []
        for d in drill[:5]:
            if not isinstance(d, dict):
                continue
            ev = d.get("events")
            txt = _fmt_drilldown_events(ev, max_chars=_drill_cap) if ev else (d.get("error") or "")
            out["drilldown_samples"].append(
                {"target": d.get("target"), "sample": txt}
            )
    return out


_SEVERITY_RANK = ["none", "low", "medium", "high", "critical"]


def _clamp_severity_for_weak_evidence(parsed: Any, evidence: Dict[str, Any]) -> None:
    """Deterministically cap synthesized severity when the evidence is too weak.

    The LLM cannot be relied on to lower severity on its own, so we enforce the
    guardrail in code: when coverage is 'partial'/'none', results were sampled,
    or the patterns 'evidence_grade' is 'low', severity is capped at 'medium'
    UNLESS a CloudWatch alarm is in ALARM state or a high/critical anomaly was
    detected (both are strong signals that survive incomplete coverage). This
    stops the synthesis from crying 'high'/'critical' on incomplete data.
    Mutates *parsed.severity* in place; never raises.
    """
    try:
        dq = (evidence or {}).get("data_quality") or {}
        coverage = str(dq.get("coverage", "")).lower()
        sampled = bool(dq.get("sampled"))
        grade = str(((evidence or {}).get("patterns") or {}).get("evidence_grade", "")).lower()
        alarm_summary = ((evidence or {}).get("alarms") or {}).get("summary") or {}
        in_alarm = int(alarm_summary.get("in_alarm") or 0) > 0
        anomalies = ((evidence or {}).get("anomalies") or {}).get("anomalies") or []
        strong_anomaly = any(
            str(a.get("severity", "")).lower() in ("high", "critical")
            for a in anomalies if isinstance(a, dict)
        )
        weak = coverage in ("partial", "none") or grade == "low" or sampled
        if not (weak and not in_alarm and not strong_anomaly):
            return
        current = str(getattr(parsed, "severity", "") or "").lower()
        if current in _SEVERITY_RANK and _SEVERITY_RANK.index(current) > _SEVERITY_RANK.index("medium"):
            parsed.severity = "medium"
    except Exception:  # pragma: no cover - guardrail must never break synthesis
        pass


def build_rich_cloudwatch_report(
    evidence: Dict[str, Any],
    log_groups: list,
    time_range: str,
    focus: Optional[str] = None,
) -> str:
    """Deterministic, detail-rich report built directly from the evidence bundle.

    Does NOT depend on the LLM — surfaces the concrete specifics an on-call
    engineer needs (actual error messages, correlation/request/trace IDs from
    the drill-down raw events and pattern example_messages, occurrence counts,
    timestamps, log streams, anomalies, and alarms). Used as the synthesis floor
    so a refusing/unavailable LLM never reduces the output to bare counts.
    """
    if not isinstance(evidence, dict):
        return f"CloudWatch analysis across {len(log_groups)} log group(s) over {time_range}."

    dq = evidence.get("data_quality") or {}
    coverage = dq.get("coverage")
    lines: List[str] = ["## CloudWatch Investigation"]
    if coverage == "none":
        lines.append(
            f"⚠️ Could not analyze any of {len(log_groups)} log group(s) over {time_range} — "
            "all analyses failed (check IAM permissions for logs:StartQuery / "
            "cloudwatch:DescribeAlarms)."
        )
    elif coverage == "partial":
        bits = []
        if dq.get("sampled"):
            bits.append("results sampled")
        if dq.get("failures"):
            bits.append("failed: " + ", ".join(f.get("analysis", "?") for f in dq["failures"]))
        lines.append(
            f"⚠️ Partial coverage of {len(log_groups)} log group(s) over {time_range}"
            + (f" ({'; '.join(bits)})" if bits else "") + "."
        )
    elif coverage == "targeted":
        _ident = dq.get("correlation_id") or dq.get("trace_id") or "the requested id"
        lines.append(
            f"Targeted correlation lookup for `{_ident}` across "
            f"{len(log_groups)} log group(s) over **{time_range}**."
        )
    else:
        lines.append(f"Checked {len(log_groups)} log group(s) over **{time_range}**.")

    # ── Summary headline (deterministic, mirrors the old format) ─────────
    _corr_pre = evidence.get("correlation") if isinstance(evidence.get("correlation"), dict) else {}
    _corr_timeline_pre = (_corr_pre.get("timeline") or []) if _corr_pre else []
    _patterns_pre = (evidence.get("patterns") or {}).get("unique_patterns") or []
    _anoms_pre = (evidence.get("anomalies") or {}).get("anomalies") or []
    _alarms_pre = evidence.get("alarms") or {}
    _in_alarm_pre = (
        (_alarms_pre.get("summary") or {}).get("in_alarm")
        if isinstance(_alarms_pre, dict) else None
    )
    _top_oc_pre = (
        _patterns_pre[0].get("occurrence_count")
        if _patterns_pre and isinstance(_patterns_pre[0], dict) else None
    )
    _summary_bits: List[str] = []
    if _corr_timeline_pre:
        _summary_bits.append(
            f"{_corr_pre.get('timeline_total', len(_corr_timeline_pre))} correlated event(s)"
        )
    if _patterns_pre:
        _summary_bits.append(
            f"{len(_patterns_pre)} recurring error pattern(s)"
            + (f" (top ×{_top_oc_pre})" if _top_oc_pre else "")
        )
    if _anoms_pre:
        _summary_bits.append(f"{len(_anoms_pre)} log-volume anomaly(ies)")
    if _in_alarm_pre:
        _summary_bits.append(f"{_in_alarm_pre} alarm(s) firing")
    lines.append("\n### Summary")
    if _summary_bits:
        lines.append(
            "Detected " + ", ".join(_summary_bits)
            + f" across {len(log_groups)} log group(s) over {time_range}."
            + (" Some analyses were unavailable — see coverage above."
               if dq.get("failures") else "")
        )
    else:
        lines.append(
            f"No errors, anomalies, or firing alarms detected across "
            f"{len(log_groups)} log group(s) over {time_range}."
        )
    lines.append("\n### Findings")

    # Build each section independently, then emit them ordered by the focus lens
    # (Analysis type). The comprehensive triage always runs; focus only decides
    # which dimension leads the report.
    sections: Dict[str, List[str]] = {"alarms": [], "patterns": [], "anomalies": []}

    # ── Alarms ──────────────────────────────────────────────────────────
    alarms = evidence.get("alarms") or {}
    if isinstance(alarms, dict):
        in_alarm = (alarms.get("summary") or {}).get("in_alarm")
        if in_alarm:
            sections["alarms"].append(f"\n**Active alarms:** {in_alarm}")

    # ── Error patterns (example_message carries correlation IDs) ─────────
    patterns = evidence.get("patterns") or {}
    uniq = patterns.get("unique_patterns") if isinstance(patterns, dict) else None
    if uniq:
        sections["patterns"].append("\n**Top error patterns:**")
        for p in uniq[:10]:
            if not isinstance(p, dict):
                continue
            oc = p.get("occurrence_count")
            ex = _clean_log_line(p.get("example_message") or p.get("normalized_pattern") or "")
            seen = ""
            if p.get("first_seen") or p.get("last_seen"):
                seen = f" — first {p.get('first_seen')} / last {p.get('last_seen')}"
            streams = p.get("affected_streams")
            stream_str = f" [{streams} stream(s)]" if streams else ""
            sections["patterns"].append(f"- ×{oc}{stream_str}{seen}\n  `{ex}`")
    elif coverage == "full":
        sections["patterns"].append("\nNo error/warning patterns found in the window.")

    # ── Anomalies ───────────────────────────────────────────────────────
    anomalies = (evidence.get("anomalies") or {}).get("anomalies") or []
    if anomalies:
        sections["anomalies"].append("\n**Anomalies (log-volume spikes):**")
        for a in anomalies[:10]:
            if not isinstance(a, dict):
                continue
            sections["anomalies"].append(
                f"- {a.get('timestamp')} [{a.get('severity')}] z={a.get('z_score')} "
                f"count={a.get('current_count')} (baseline {a.get('baseline_average')}) "
                f"in {a.get('log_group')}"
            )

    # Emit in focus-priority order, then the remaining sections.
    _focus_section = {
        "anomaly-detection": "anomalies", "anomalies": "anomalies",
        "error-patterns": "patterns", "alarms": "alarms",
    }.get((focus or "").strip().lower())
    _default_order = ["alarms", "patterns", "anomalies"]
    _order = ([_focus_section] if _focus_section else []) + [
        k for k in _default_order if k != _focus_section
    ]
    for _key in _order:
        lines.extend(sections[_key])

    # ── Correlation timeline (targeted trace/correlation lookups) ───────
    corr = evidence.get("correlation") if isinstance(evidence.get("correlation"), dict) else None
    if corr:
        if corr.get("error"):
            lines.append(f"\n**Correlation lookup failed:** {corr['error']}")
        else:
            _tl = corr.get("timeline") or []
            if _tl:
                _total = corr.get("timeline_total", len(_tl))
                lines.append(f"\n**Correlation timeline** ({_total} event(s), chronological):")
                for ev in _tl[:40]:
                    if not isinstance(ev, dict):
                        continue
                    _stream = ev.get("log_stream")
                    _loc = f"{ev.get('log_group','')}" + (f"/{_stream}" if _stream else "")
                    lines.append(
                        f"- {ev.get('timestamp')} [{_loc}]\n  {_clean_log_line(ev.get('message',''))}"
                    )

    # ── Raw drill-down events — correlation/request/trace IDs live here ──
    drill = evidence.get("drilldown") or []
    if isinstance(drill, list) and drill:
        lines.append("\n**Raw matching events** (correlation/request/trace IDs, timestamps):")
        for d in drill[:10]:
            if not isinstance(d, dict):
                continue
            tgt = d.get("target") or "match"
            if d.get("error"):
                lines.append(f"- {tgt}: (drill-down failed: {d['error']})")
                continue
            rendered = _fmt_drilldown_events(d.get("events"))
            lines.append(f"- {tgt}:\n```\n{rendered}\n```")

    return "\n".join(lines)


def check_cloudwatch_alerts(
    result: Dict[str, Any],
    analysis_type: str,
    error_threshold: int,
) -> list:
    """Check analysis results against thresholds and generate alerts."""
    alerts = []

    if analysis_type == 'error-patterns':
        patterns = result.get('patterns', {})
        if not isinstance(patterns, dict):
            patterns = {}
        error_data = patterns.get('error', {})
        if not isinstance(error_data, dict):
            error_data = {}
        for entry in error_data.get('data', []):
            if not isinstance(entry, (list, tuple)):
                continue
            for field in entry:
                if not isinstance(field, dict):
                    continue
                if field.get('field') == 'count()':
                    try:
                        count = float(field.get('value', 0))
                        if count > error_threshold:
                            alerts.append({
                                "type": "error_threshold_exceeded",
                                "message": (
                                    f"Error count {count} exceeds threshold {error_threshold}"
                                ),
                                "severity": "high" if count > error_threshold * 2 else "medium",
                            })
                    except (ValueError, TypeError):
                        pass

        for pattern_entry in result.get('unique_patterns', []):
            if not isinstance(pattern_entry, (list, tuple)):
                continue
            for field in pattern_entry:
                if not isinstance(field, dict):
                    continue
                if field.get('field') == 'occurrence_count':
                    try:
                        occ_count = float(field.get('value', 0))
                        if occ_count > error_threshold:
                            pattern_text = ""
                            for f2 in pattern_entry:
                                if isinstance(f2, dict) and f2.get('field') == 'error_pattern':
                                    pattern_text = (f2.get('value') or '')[:120]
                                    break
                            alerts.append({
                                "type": "repeated_error_pattern",
                                "message": (
                                    f"Error pattern occurred {int(occ_count)} times "
                                    f"(threshold {error_threshold}): {pattern_text}"
                                ),
                                "severity": (
                                    "high" if occ_count > error_threshold * 3 else "medium"
                                ),
                            })
                    except (ValueError, TypeError):
                        pass

    elif analysis_type == 'anomaly-detection':
        for anomaly in result.get('anomalies', []):
            if not isinstance(anomaly, dict):
                continue
            z_info = ""
            if anomaly.get('z_score'):
                z_info = f", z-score={anomaly['z_score']}"
            deviation_info = ""
            if anomaly.get('deviation_factor'):
                deviation_info = f" ({anomaly['deviation_factor']}x baseline)"
            alerts.append({
                "type": "anomaly_detected",
                "message": (
                    f"Anomaly at {anomaly.get('timestamp')}: "
                    f"{anomaly.get('current_count')} events{deviation_info}{z_info}"
                ),
                "severity": anomaly.get('severity', 'medium'),
            })

    return alerts


async def analyze_cloudwatch_with_llm(
    *,
    active_executions: Dict[str, Dict[str, Any]],
    execution_id: str,
    raw_result: Dict[str, Any],
    analysis_type: str,
    log_groups: List[str],
    time_range: str,
    alerts: list,
    focus: Optional[str] = None,
) -> tuple:
    """Use the workflow's LLM node to produce a structured CloudWatch analysis."""
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from pydantic import BaseModel as _PydanticBase, Field as _Field
        from typing import List as _List

        from app.workflow.strategies.react.llm_factory import build_llm
        from app.workflow.strategies.react.workflow_config import resolve_llm_config_for_workflow

        class CloudWatchAnalysisSummary(_PydanticBase):
            """Structured CloudWatch log analysis summary."""
            headline: str = _Field(
                description="One-sentence summary suitable for Slack or a notification."
            )
            severity: str = _Field(
                description="Overall severity: critical | high | medium | low | none."
            )
            key_findings: _List[str] = _Field(
                description="2-5 bullet points describing what was found."
            )
            primary_hypothesis: str = _Field(
                description=(
                    "Most likely explanation or key takeaway based on the data "
                    "(e.g. the probable root cause when investigating an issue). "
                    "Use 'Unknown' if there is insufficient evidence."
                )
            )
            recommended_actions: _List[str] = _Field(
                description="Ordered list of recommended next steps."
            )
            related_services: _List[str] = _Field(
                default_factory=list,
                description="Other services likely involved, inferred from patterns.",
            )
            confidence: str = _Field(
                description="Confidence in this analysis: high | medium | low."
            )

        active = active_executions.get(execution_id, {})
        workflow = active.get('workflow')
        if not workflow:
            return None, None, None, 0, 0

        from app.workflow.llm_config import LLM_NODE_TYPES
        nodes = workflow.get('nodes', [])
        # Accept both the legacy ReactFlow 'llm' node and the new LangflowEditor
        # 'language_model' node — otherwise synthesis is silently skipped on new
        # workflows and the run falls back to the terse deterministic summary.
        if not any(n.get('type') in LLM_NODE_TYPES for n in nodes):
            logger.info("CloudWatch LLM synthesis skipped: no LLM node in workflow "
                        "(execution_id=%s)", execution_id)
            return None, None, None, 0, 0

        from app.config import settings
        if not settings.cloudwatch_pipeline_llm_synthesis:
            logger.info("CloudWatch LLM synthesis skipped: disabled via "
                        "cloudwatch_pipeline_llm_synthesis (execution_id=%s)", execution_id)
            return None, None, None, 0, 0
        if _synthesis_circuit_open():
            logger.info("CloudWatch LLM synthesis skipped: guardrail circuit breaker open "
                        "(%d consecutive refusals) — using deterministic report "
                        "(execution_id=%s)", _GUARDRAIL_REFUSALS, execution_id)
            return None, None, None, 0, 0

        llm_config = await resolve_llm_config_for_workflow(workflow)
        llm = build_llm(llm_config)
        model_name = llm_config.get('model')

        # Send a COMPACT, PII-masked payload to the synthesis. The full evidence
        # bundle (raw drill-down @message blobs + customer/profile IDs) is large
        # and trips the account's Bedrock guardrail, which blocks the whole
        # synthesis. build_synthesis_payload mirrors the small summary the agent
        # gets; redact() additionally strips secrets. Real identifiers stay in the
        # deterministic report appended to the output, so nothing is lost to the
        # engineer.
        from app.core.redact import redact
        from app.core.toon import encode_toon, TOON_LEGEND
        # Serialize the compact evidence as TOON (uniform arrays → header + rows,
        # keys written once) instead of pretty JSON — ~30–60% fewer tokens on the
        # error_patterns/anomalies/drilldown tables. redact() still strips secrets.
        synthesis_evidence = build_synthesis_payload(raw_result)
        data_str = redact(encode_toon(synthesis_evidence))
        _syn_cap = settings.cloudwatch_synthesis_max_chars
        if len(data_str) > _syn_cap:
            data_str = data_str[:_syn_cap] + "\n... [truncated]"

        alert_section = ""
        if alerts:
            alert_section = (
                "\n\nAlerts triggered:\n"
                + redact(json.dumps(alerts, default=str))
            )

        system_prompt = (
            f"{TOON_LEGEND}\n\n"
            "You are an expert CloudWatch log analysis engineer. "
            "Analyze the provided log analysis results and give a clear, actionable summary. "
            "Be concise but thorough. Focus on what matters to whoever is investigating — "
            "whether that is root-cause analysis, log retrieval, or a general query. "
            "Respect the 'data_quality' field if present: when coverage is 'full' and no "
            "errors/anomalies were found, state plainly that the log groups were checked and "
            "nothing of concern was found — do not invent problems. When coverage is "
            "'partial' or 'none', or results were sampled, call that out explicitly and lower "
            "your confidence accordingly rather than presenting incomplete data as definitive. "
            "CAP SEVERITY WHEN EVIDENCE IS WEAK: if coverage is 'partial'/'none', results were "
            "sampled, or the patterns 'evidence_grade' is 'low', do NOT assign a severity above "
            "'medium' unless a CloudWatch alarm is in ALARM state — insufficient evidence cannot "
            "justify 'high' or 'critical'. "
            "Always surface CONCRETE SPECIFICS, not just counts: quote the actual error "
            "message(s) verbatim, and extract every identifier present in the evidence — "
            "correlation_id / correlationId, request_id / requestId, trace_id / X-Ray traceId, "
            "transaction_id, session_id, user_id, order/account IDs, exception class, "
            "@logStream, and the exact @timestamp. Mine the 'drilldown' raw events and each "
            "pattern's 'example_message' for these IDs. In key_findings, list each distinct "
            "error with its identifiers and timestamp so an engineer can grep for it directly; "
            "if a correlation/request/trace ID is present, ALWAYS include it."
        )

        human_prompt = (
            f"Analyze these **{analysis_type}** results from CloudWatch log groups "
            f"{log_groups} over the last **{time_range}**.\n\n"
            f"Analysis data (TOON format):\n```\n{data_str}\n```"
            f"{alert_section}\n\n"
            "Report the specific error(s) found, quoting the raw message and every "
            "correlation/request/trace/transaction ID and timestamp you can find in the "
            "evidence above (check the 'drilldown' events and 'example_message' fields)."
            + (
                f"\n\nThe engineer selected '{focus}' as the focus — LEAD your summary and "
                "key findings with that dimension, while still covering the other signals."
                if focus else ""
            )
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ]

        provider = llm_config.get('provider', '').lower()
        _structured_providers = {'openai', 'anthropic', 'bedrock', 'azure'}

        structured_analysis: Optional[Dict[str, Any]] = None
        analysis_text: Optional[str] = None
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        raw_msg: Any = None

        def _cache_read(usage: Any) -> int:
            """Pull cache-read tokens out of a LangChain usage_metadata dict."""
            if not isinstance(usage, dict):
                return 0
            details = usage.get('input_token_details') or {}
            return (details.get('cache_read', 0) if isinstance(details, dict) else 0) or 0

        def _coerce_message_text(content: Any) -> str:
            """Flatten an AIMessage.content (str or content-block list) to text."""
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                return "".join(parts)
            return str(content) if content else ""

        if provider in _structured_providers:
            try:
                structured_llm = llm.with_structured_output(
                    CloudWatchAnalysisSummary, include_raw=True
                )
                llm_raw = await asyncio.wait_for(
                    structured_llm.ainvoke(messages),
                    timeout=60.0,
                )
                parsed: Optional[CloudWatchAnalysisSummary] = (
                    llm_raw.get('parsed') if isinstance(llm_raw, dict) else llm_raw
                )
                raw_msg = llm_raw.get('raw') if isinstance(llm_raw, dict) else None
                usage = getattr(raw_msg, 'usage_metadata', None) or {}
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
                cache_read_tokens += _cache_read(usage)
                if parsed is not None:
                    _clamp_severity_for_weak_evidence(parsed, raw_result)
                    structured_analysis = parsed.model_dump()
                    analysis_text = (
                        f"**{parsed.headline}**\n\n"
                        f"Severity: {parsed.severity.upper()} (confidence: {parsed.confidence})\n\n"
                        f"**Key Findings:**\n"
                        + "\n".join(f"- {f}" for f in parsed.key_findings)
                        + f"\n\n**Primary Hypothesis:** {parsed.primary_hypothesis}\n\n"
                        f"**Recommended Actions:**\n"
                        + "\n".join(f"{i+1}. {a}" for i, a in enumerate(parsed.recommended_actions))
                        + (
                            f"\n\n**Related Services:** {', '.join(parsed.related_services)}"
                            if parsed.related_services else ""
                        )
                    )
                    logger.info(
                        "CloudWatch structured LLM analysis complete (severity=%s, model=%s, "
                        "tokens=%d, cache_read=%d)",
                        parsed.severity, model_name, input_tokens + output_tokens,
                        cache_read_tokens,
                    )
            except Exception as struct_err:
                logger.warning(
                    "CloudWatch structured output failed, falling back to free-form: %s",
                    struct_err,
                )

        # Reuse the structured call's raw response instead of paying for a second
        # free-form call. When schema validation fails (parsed is None) the model
        # often still emitted usable prose in the raw message — format that
        # directly. The second call is only issued when the first produced no
        # usable text at all (e.g. it errored/timed out before responding).
        if analysis_text is None and raw_msg is not None:
            raw_text = _coerce_message_text(getattr(raw_msg, 'content', None))
            if raw_text.strip():
                analysis_text = raw_text
                logger.info(
                    "CloudWatch synthesis: reused structured raw text (%d chars, model=%s) — "
                    "skipped free-form fallback call",
                    len(raw_text), model_name,
                )

        if analysis_text is None:
            free_form_prompt = human_prompt + (
                "\n\nProvide:\n"
                "1. A concise summary of findings\n"
                "2. Key patterns or anomalies identified\n"
                "3. Severity assessment\n"
                "4. Recommended next steps\n"
            )
            response = await asyncio.wait_for(
                llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=free_form_prompt),
                ]),
                timeout=60.0,
            )
            usage = getattr(response, 'usage_metadata', None) or {}
            input_tokens += usage.get('input_tokens', 0)
            output_tokens += usage.get('output_tokens', 0)
            cache_read_tokens += _cache_read(usage)
            analysis_text = _coerce_message_text(response.content) or None
            if analysis_text:
                logger.info(
                    "CloudWatch free-form LLM analysis complete (%d chars, model=%s, "
                    "tokens=%d, cache_read=%d)",
                    len(analysis_text), model_name, input_tokens + output_tokens,
                    cache_read_tokens,
                )

        # Feed the guardrail circuit breaker: a refusal/empty synthesis trips it
        # (so we stop paying for the blocked call); a usable one resets it.
        if analysis_text is not None:
            if is_usable_synthesis(analysis_text):
                _reset_guardrail_refusals()
            else:
                _record_guardrail_refusal()
                logger.info(
                    "CloudWatch synthesis returned a refusal/stub (%d chars) — guardrail "
                    "refusal count now %d (execution_id=%s)",
                    len(analysis_text), _GUARDRAIL_REFUSALS, execution_id,
                )

        return analysis_text, model_name, structured_analysis, input_tokens, output_tokens

    except Exception as e:
        logger.warning("CloudWatch LLM analysis failed (falling back to static): %s", e)
        return None, None, None, 0, 0

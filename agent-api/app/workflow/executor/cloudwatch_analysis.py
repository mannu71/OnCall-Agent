"""CloudWatch analysis helpers — static summaries, alerts, and LLM synthesis."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
) -> tuple:
    """Use the workflow's LLM node to produce a structured CloudWatch analysis."""
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from pydantic import BaseModel as _PydanticBase, Field as _Field
        from typing import List as _List

        from app.workflow.strategies.react.llm_factory import build_llm
        from app.workflow.strategies.react.workflow_config import resolve_llm_config_for_workflow

        class CloudWatchAnalysisSummary(_PydanticBase):
            """Structured CloudWatch incident analysis for on-call engineers."""
            headline: str = _Field(
                description="One-sentence incident summary suitable for Slack or PagerDuty."
            )
            severity: str = _Field(
                description="Overall severity: critical | high | medium | low | none."
            )
            key_findings: _List[str] = _Field(
                description="2-5 bullet points describing what was found."
            )
            root_cause_hypothesis: str = _Field(
                description=(
                    "Most likely root cause based on the data. "
                    "Use 'Unknown' if there is insufficient evidence."
                )
            )
            recommended_actions: _List[str] = _Field(
                description="Ordered list of actions for the on-call engineer."
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

        nodes = workflow.get('nodes', [])
        if not any(n.get('type') == 'llm' for n in nodes):
            logger.debug("CloudWatch LLM analysis skipped: no LLM node in workflow")
            return None, None, None, 0, 0

        llm_config = await resolve_llm_config_for_workflow(workflow)
        llm = build_llm(llm_config)
        model_name = llm_config.get('model')

        data_str = json.dumps(raw_result, indent=2, default=str)
        if len(data_str) > 15_000:
            data_str = data_str[:15_000] + "\n... [truncated]"

        alert_section = ""
        if alerts:
            alert_section = (
                "\n\nAlerts triggered:\n"
                + json.dumps(alerts, indent=2, default=str)
            )

        system_prompt = (
            "You are an expert CloudWatch log analysis engineer for KYC Protect. "
            "Analyze the provided log analysis results and give a clear, actionable summary. "
            "Be concise but thorough. Focus on what matters for an on-call engineer."
        )

        human_prompt = (
            f"Analyze these **{analysis_type}** results from CloudWatch log groups "
            f"{log_groups} over the last **{time_range}**.\n\n"
            f"Raw analysis data:\n```json\n{data_str}\n```"
            f"{alert_section}"
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

        if provider in _structured_providers:
            try:
                structured_llm = llm.with_structured_output(
                    CloudWatchAnalysisSummary, include_raw=True
                )
                llm_raw = await asyncio.wait_for(
                    structured_llm.ainvoke(messages),
                    timeout=60.0,
                )
                parsed: CloudWatchAnalysisSummary = (
                    llm_raw.get('parsed') if isinstance(llm_raw, dict) else llm_raw
                )
                raw_msg = llm_raw.get('raw') if isinstance(llm_raw, dict) else None
                usage = getattr(raw_msg, 'usage_metadata', None) or {}
                input_tokens += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
                structured_analysis = parsed.model_dump()
                analysis_text = (
                    f"**{parsed.headline}**\n\n"
                    f"Severity: {parsed.severity.upper()} (confidence: {parsed.confidence})\n\n"
                    f"**Key Findings:**\n"
                    + "\n".join(f"- {f}" for f in parsed.key_findings)
                    + f"\n\n**Root Cause:** {parsed.root_cause_hypothesis}\n\n"
                    f"**Recommended Actions:**\n"
                    + "\n".join(f"{i+1}. {a}" for i, a in enumerate(parsed.recommended_actions))
                    + (
                        f"\n\n**Related Services:** {', '.join(parsed.related_services)}"
                        if parsed.related_services else ""
                    )
                )
                logger.info(
                    "CloudWatch structured LLM analysis complete (severity=%s, model=%s, tokens=%d)",
                    parsed.severity, model_name, input_tokens + output_tokens,
                )
            except Exception as struct_err:
                logger.warning(
                    "CloudWatch structured output failed, falling back to free-form: %s",
                    struct_err,
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
            analysis_text = str(response.content) if response.content else None
            if analysis_text:
                logger.info(
                    "CloudWatch free-form LLM analysis complete (%d chars, model=%s, tokens=%d)",
                    len(analysis_text), model_name, input_tokens + output_tokens,
                )

        return analysis_text, model_name, structured_analysis, input_tokens, output_tokens

    except Exception as e:
        logger.warning("CloudWatch LLM analysis failed (falling back to static): %s", e)
        return None, None, None, 0, 0

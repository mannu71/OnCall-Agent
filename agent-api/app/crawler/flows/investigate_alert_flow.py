"""investigateAlertFlow — root-cause analysis for an on-call alert.

DAG: ParseAlert >> LookupOverview >> IdentifyLikelyAbstractions
          >> FindSymbolsInScope >> SynthesizeRootCause >> BuildReport
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes.investigate import (
    ParseAlert,
    LookupOverview,
    IdentifyLikelyAbstractions,
    FindSymbolsInScope,
    SynthesizeRootCause,
    BuildReport,
)

_parse = ParseAlert(max_retries=3, wait=10.0)
_lookup = LookupOverview()
_identify = IdentifyLikelyAbstractions(max_retries=3, wait=10.0)
_find = FindSymbolsInScope()
_synthesize = SynthesizeRootCause(max_retries=3, wait=10.0)
_report = BuildReport()

_parse >> _lookup >> _identify >> _find >> _synthesize >> _report

investigate_alert_flow = AsyncFlow(start=_parse)

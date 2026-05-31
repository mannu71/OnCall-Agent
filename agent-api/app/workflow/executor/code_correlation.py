"""Lightweight anomaly-to-code correlation for agent context injection."""
import asyncio
import logging
import re
from typing import Any, Dict, List, Set, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

_ENTITY_RE = re.compile(
    r'(?:ERROR|Exception|error|exception)\s+in\s+([\w\.]+)',
    re.IGNORECASE,
)


async def correlate_anomalies_to_code(
    cw_results: Dict[str, Any],
    code_results: Dict[str, Any],
    log_group_to_repo: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Map CloudWatch anomaly text to indexed code symbols via the crawler."""
    lookups: Set[Tuple[str, str, str, str]] = set()

    for _key, cw_val in cw_results.items():
        output_text = cw_val.get('output', '') or ''
        log_groups = cw_val.get('log_groups_analyzed') or []

        for lg in log_groups:
            repo_name = log_group_to_repo.get(lg)
            if not repo_name:
                continue

            for match in _ENTITY_RE.findall(output_text):
                func_name = match.split('.')[-1]
                if func_name:
                    lookups.add((lg, repo_name, func_name, output_text[:200]))

    if not lookups:
        return []

    from app.services.crawler_service import crawler_service

    sem = asyncio.Semaphore(settings.code_correlation_concurrency)
    correlations: List[Dict[str, Any]] = []

    async def _lookup(lg: str, repo_name: str, func_name: str, snippet: str) -> None:
        async with sem:
            try:
                hit = await crawler_service.find_symbol(
                    name=func_name, repo=repo_name, limit=1,
                )
                results_list = hit.get('results', [])
                if results_list:
                    r = results_list[0]
                    correlations.append({
                        'log_group': lg,
                        'repo': repo_name,
                        'anomaly_snippet': snippet,
                        'matched_function': func_name,
                        'matched_file': r.get('file', ''),
                        'matched_line': r.get('line'),
                        'confidence': 'exact_name_match',
                        'suggestion': (
                            f'crawler_trace_path(symbol="{func_name}", '
                            f'repo="{repo_name}")'
                        ),
                    })
            except Exception as corr_exc:
                logger.warning(
                    "Anomaly→code correlation failed for repo %s: %s",
                    repo_name, corr_exc, exc_info=True,
                )

    await asyncio.gather(
        *[_lookup(lg, repo, fn, snip) for lg, repo, fn, snip in lookups]
    )
    return correlations

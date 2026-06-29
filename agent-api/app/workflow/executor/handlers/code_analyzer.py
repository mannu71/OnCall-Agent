"""Code Analyzer node handler — validates indexed repos and optional pre-summary search."""
import asyncio
import logging
import os
import re
from typing import Any, Dict

from app.config import settings
from app.core.security import check_path, PathJailError
from app.services.crawler_service import crawler_service
from app.workflow.code_analyzer_config import read_code_analyzer_repos

from . import register

logger = logging.getLogger(__name__)

_WIN_ABS_RE = re.compile(r'^[A-Za-z]:[/\\]')


@register("codeAnalyzer")
@register("code_search_tool")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Code Analyzer node (both ``codeAnalyzer`` and ``code_search_tool``)."""
    try:
        from app.core.security import scan_injection, InjectionError
    except ImportError:
        def scan_injection(text: str) -> None:
            pass

        class InjectionError(Exception):
            pass

    node_data = node.get('data', {})
    node_params = node.get('params', {})
    # Accept both dialects: data.repos (list) and params.repos (string).
    repos = read_code_analyzer_repos(node)
    pre_summary = node_data.get('preSummary', node_params.get('preSummary', False))
    repos_base_path = settings.repos_base_path

    if not repos:
        return {
            'status': 'failed',
            'error': 'No repos configured for Code Analyzer node.',
            'code_analysis_type': 'pre_summary',
        }

    def _normalise_repo_path(repo_cfg: dict) -> dict:
        raw = repo_cfg.get('path', '')
        if raw and _WIN_ABS_RE.match(raw) and os.sep == '/':
            safe_name = (
                repo_cfg.get('name')
                or os.path.basename(raw.replace('\\', '/').rstrip('/'))
            )
            remapped = os.path.join(repos_base_path, safe_name)
            logger.warning(
                "codeAnalyzer: Windows path %r is not accessible from a "
                "Linux/Docker host; remapped to %r. "
                "To index local source, mount the directory into the container "
                "at that path or set REPOS_BASE_PATH accordingly.",
                raw, remapped,
            )
            return {**repo_cfg, 'path': remapped}
        return repo_cfg

    repos = [_normalise_repo_path(r) for r in repos]

    for repo in repos:
        try:
            scan_injection(repo.get('name', ''))
            scan_injection(repo.get('path', ''))
        except InjectionError as exc:
            return {
                'status': 'failed',
                'error': f'Injection detected in repo config: {exc}',
                'code_analysis_type': 'pre_summary',
            }
        # Name-only repos (the LangflowEditor dialect) carry no explicit path —
        # they resolve under REPOS_BASE_PATH downstream, so only jail-check an
        # explicit path when one is provided.
        if repo.get('path'):
            try:
                check_path(repo.get('path', ''), jail=repos_base_path)
            except PathJailError as exc:
                return {
                    'status': 'failed',
                    'error': f'Repo path outside allowed root ({repos_base_path}): {exc}',
                    'code_analysis_type': 'pre_summary',
                }

    repo_names = [repo.get("name", "") for repo in repos if repo.get("name")]

    from app.workflow.code_analyzer_config import read_code_analyzer_backend
    backend = read_code_analyzer_backend(node)

    if backend == "codegraph":
        # codegraph manages its own index state; the agent calls index_repository
        # on first use. Skip the crawler repo_abstractions check entirely.
        all_indexed = repo_names
        unindexed = []
    else:
        unindexed = await crawler_service.filter_unindexed_repos(repo_names)
        all_indexed = [name for name in repo_names if name not in unindexed]

    if unindexed:
        return {
            'status': 'not_indexed',
            'error': (
                f"Repos not yet indexed: {unindexed}. "
                "Re-save the workflow to trigger background indexing."
            ),
            'code_analysis_type': 'pre_summary',
        }

    pre_summary_results: Dict[str, Any] = {}
    if pre_summary and all_indexed:
        inputs = context.get('inputs', {})
        search_query = (
            inputs.get('service_name')
            or inputs.get('error_signature')
            or inputs.get('component')
            or inputs.get('user_query', '')[:200]
        )
        if search_query:
            # crawler_service is already imported at module scope; re-importing
            # here would make it a function-local name and shadow the earlier
            # use above (UnboundLocalError).
            search_concurrency = settings.code_analyzer_search_concurrency
            search_sem = asyncio.Semaphore(search_concurrency)

            async def _search_repo(repo_name: str) -> tuple[str, Any]:
                async with search_sem:
                    try:
                        hits = await crawler_service.search_semantic(
                            query=search_query, repo=repo_name, limit=5,
                        )
                        return repo_name, hits
                    except Exception as se:
                        logger.debug(
                            "codeAnalyzer pre-summary search failed for %s: %s",
                            repo_name, se,
                        )
                        return repo_name, None

            search_results = await asyncio.gather(
                *[_search_repo(rn) for rn in all_indexed]
            )
            for repo_name, hits in search_results:
                if hits is not None:
                    pre_summary_results[repo_name] = hits

    # Project intelligence auto-context: give the agent the per-repo brief +
    # coding standards at run start so it understands the project and writes code
    # that fits, without needing a tool call first. Best-effort and compact
    # (capped per repo); deeper detail is fetched on demand via the crawler tools.
    project_briefs: Dict[str, Any] = {}
    if all_indexed:
        async def _ctx(repo_name: str) -> tuple[str, Any]:
            try:
                return repo_name, await crawler_service.get_project_context(repo_name)
            except Exception as ce:  # noqa: BLE001
                logger.debug("codeAnalyzer project-context failed for %s: %s", repo_name, ce)
                return repo_name, None

        for repo_name, ctx in await asyncio.gather(*[_ctx(rn) for rn in all_indexed]):
            if ctx is not None:
                project_briefs[repo_name] = ctx

    result: Dict[str, Any] = {
        'status': 'success',
        'output': f'Repos ready: {", ".join(all_indexed)}',
        'code_analysis_type': 'pre_summary',
        'repos_indexed': all_indexed,
        'repos_config': repos,
        'pre_summary': pre_summary_results or None,
        'project_brief': project_briefs or None,
    }

    if backend == "codegraph":
        # codegraph projects are PRE-INDEXED on workflow save (background, fast
        # mode). The project name is the repo basename. Steer the agent to query
        # the existing graph directly — re-indexing a large repo at runtime is
        # slow (a 9 GB repo's FULL index exceeds 30 s and can blow the tool
        # timeout). Only index on demand, and only in fast mode, if a query
        # reports the project is missing (e.g. the store was reset).
        result['codegraph_repo_paths'] = {
            name: os.path.join(repos_base_path, name)
            for name in all_indexed
        }
        _names = ", ".join(all_indexed)
        result['output'] = (
            f'Repos pre-indexed in codegraph (project names: {_names}). '
            f'Query the GRAPH tools first — they are sub-second and ~500 tokens '
            f'each: prefer search_graph / find_symbol / query_graph / '
            f'search_semantic / trace_path / get_code_snippet, always with '
            f'project="<repo name>". '
            f'AVOID search_code for discovery — it greps source files and is far '
            f'slower and more token-heavy (~80K). Use search_code ONLY as a '
            f'last-resort literal-text fallback, and always scope it with '
            f'path_filter (e.g. path_filter="src/") or file_pattern. '
            f'Do NOT re-index; only if a tool reports the project is missing, '
            f'call index_repository once with '
            f'repo_path="{repos_base_path}/<name>" and mode="fast".'
        )

    return result

"""Code Analyzer node handler — validates codegraph-indexed repos."""
import logging
import os
import re
from typing import Any, Dict

from app.config import settings
from app.core.security import check_path, PathJailError
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

    # Accept both dialects: data.repos (list) and params.repos (string).
    repos = read_code_analyzer_repos(node)
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

    # Verify each repo actually has a POPULATED codegraph index before claiming
    # it's pre-indexed (a failed/empty background index must not steer the agent
    # as "ready"). Conservative: if the cache can't be read we fall back to the
    # assume-ready behavior, so this only ever ADDS a correct "index this first"
    # signal. We never hard-block — codegraph can self-index on demand (see
    # steering below).
    needs_index = []
    from app.services import codegraph_admin
    try:
        _projects = codegraph_admin.list_projects()
        _list_ok = True
    except Exception:  # noqa: BLE001
        _projects, _list_ok = [], False
    if _list_ok:
        _ready = {p.get("repo_name") for p in _projects if (p.get("files_indexed") or 0) > 0}
        needs_index = [n for n in repo_names if n not in _ready]
        if needs_index:
            logger.info("code_analyzer: codegraph repos need indexing first: %s", needs_index)
    all_indexed = [n for n in repo_names if n not in needs_index]

    result: Dict[str, Any] = {
        'status': 'success',
        'output': f'Repos ready: {", ".join(all_indexed)}',
        'code_analysis_type': 'pre_summary',
        'repos_indexed': all_indexed,
        'repos_config': repos,
    }

    # codegraph projects are PRE-INDEXED on workflow save (background, fast mode).
    # Internally the engine keys each project by its full indexed path with
    # '/' -> '-' (deliberate — avoids basename collisions across repos at
    # different paths; see cg_project_name's docstring). The agent should never
    # see that internal name: it always passes the SHORT repo name, and every
    # codegraph tool call rewrites it to the canonical name before hitting the
    # MCP server (app.workflow.mcp.mcp_langchain_adapter MCPToolWrapper.
    # project_aliases, wired in
    # app.workflow.tools.codegraph_tools.build_codegraph_tools).
    from app.workflow.tools.codegraph_tools import cg_project_name

    # Paths/aliases for ALL repos (including any not-yet-indexed) so the agent
    # can index the missing ones on demand.
    result['codegraph_repo_paths'] = {
        short_name: os.path.join(repos_base_path, short_name)
        for short_name in repo_names
    }
    result['codegraph_project_aliases'] = {
        short_name: cg_project_name(short_name)
        for short_name in repo_names
    }
    _names = ", ".join(f'"{n}"' for n in all_indexed) if all_indexed else "(none yet)"
    _example_name = all_indexed[0] if all_indexed else (repo_names[0] if repo_names else "<repo name>")
    _index_first = ""
    if needs_index:
        _need = ", ".join(f'"{n}"' for n in needs_index)
        _index_first = (
            f'NOT yet indexed: {_need} — before querying these, call '
            f'index_repository once each with repo_path="{repos_base_path}/<repo name>" '
            f'and mode="fast". '
        )
    result['output'] = (
        f'Repos pre-indexed in Code Crawler (project names: {_names}). '
        f'{_index_first}'
        f'Query the GRAPH tools first — they are sub-second and ~500 tokens '
        f'each: prefer search_graph / find_symbol / query_graph / '
        f'search_semantic / trace_path / get_code_snippet, always with '
        f'project="<repo name>" (e.g. project="{_example_name}"). '
        f'AVOID search_code for discovery — it greps source files and is far '
        f'slower and more token-heavy (~80K). Use search_code ONLY as a '
        f'last-resort literal-text fallback, and always scope it with '
        f'path_filter (e.g. path_filter="src/") or file_pattern. '
        f'For already-indexed repos do NOT re-index; only if a tool reports the '
        f'project is missing, call index_repository once with '
        f'repo_path="{repos_base_path}/<repo name>" and mode="fast".'
    )

    return result

"""Wiki output node handler — publishes agent response to Azure DevOps Wiki."""
import logging
import re
import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.services.azure_config_manager import ConfigManager
from app.services.azure_wiki_client import AzureWikiClient
from . import register

logger = logging.getLogger(__name__)


def resolve_ado_pat(pat: str = "", token_var: str = "") -> Optional[str]:
    """Resolve an Azure DevOps PAT with the standard precedence used across the
    Wiki node: explicit node value → env var → global stored credential.

    Args:
        pat: Explicit PAT from node config (highest priority).
        token_var: Name of an env var to read the PAT from (e.g. ADO_WIKI_PAT).

    Returns:
        The resolved PAT, or None if none is configured.
    """
    pat = (pat or "").strip()
    if pat:
        return pat

    token_var = (token_var or "ADO_WIKI_PAT").strip()
    env_pat = os.environ.get(token_var, "") if token_var else ""
    if env_pat:
        return env_pat

    config_path = os.environ.get(
        "AZURE_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "../../../../config/azure_devops_credentials.json")
    )
    try:
        return ConfigManager(config_path=config_path).get_pat()
    except Exception as e:
        logger.error(f"Failed to initialize ConfigManager: {e}")
        return None


def parse_wiki_url(url: str) -> tuple:
    """Parse Azure DevOps Wiki URL to extract organization, project, and wiki_id."""
    if not url:
        return None, None, None

    # Pattern 1: https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wikiId}
    m = re.search(r'dev\.azure\.com/([^/]+)/([^/]+)/_wiki/wikis/([^/?#\s]+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Pattern 2: https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wikiId}
    m = re.search(r'dev\.azure\.com/([^/]+)/([^/]+)/_apis/wiki/wikis/([^/?#\s]+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Pattern 3: https://{organization}.visualstudio.com/{project}/_wiki/wikis/{wikiId}
    m = re.search(r'(?:https?://)?([^/.]+)\.visualstudio\.com/([^/]+)/_wiki/wikis/([^/?#\s]+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Pattern 4: Simple dev.azure.com URL with organization and project
    parts = [p for p in url.split('/') if p]
    if 'dev.azure.com' in parts:
        try:
            idx = parts.index('dev.azure.com')
            org = parts[idx + 1] if len(parts) > idx + 1 else None
            proj = parts[idx + 2] if len(parts) > idx + 2 else None
            wiki_id = None
            if len(parts) > idx + 5 and parts[idx + 3] == '_wiki' and parts[idx + 4] == 'wikis':
                wiki_id = parts[idx + 5]
            return org, proj, wiki_id
        except Exception:
            pass

    return None, None, None


def to_bullet_points(text: str) -> str:
    """Format plain text or markdown paragraphs into a bulleted list."""
    if not text:
        return ""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    bullets = []
    for line in lines:
        if line.startswith(('-', '*', '•', '1.', '2.', '3.')):
            bullets.append(line)
        else:
            bullets.append(f"- {line}")
    return "\n".join(bullets)


def to_table(text: str) -> str:
    """Format structured text (JSON or CSV) into a Markdown table."""
    if not text:
        return "| Value |\n| --- |\n| (Empty) |"

    stripped_text = text.strip()
    try:
        # Try parsing as JSON list of dicts
        data = json.loads(stripped_text)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            headers = list(data[0].keys())
            header_line = "| " + " | ".join(headers) + " |"
            separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
            rows = []
            for item in data:
                row_vals = [str(item.get(h, "")) for h in headers]
                rows.append("| " + " | ".join(row_vals) + " |")
            return "\n".join([header_line, separator_line] + rows)
    except Exception:
        pass

    # Try parsing CSV/TSV style
    lines = [line.strip() for line in stripped_text.split('\n') if line.strip()]
    if not lines:
        return "| Value |\n| --- |\n| (Empty) |"

    delimiter = ',' if ',' in lines[0] else None
    if not delimiter and '\t' in lines[0]:
        delimiter = '\t'

    if delimiter:
        rows = []
        for i, line in enumerate(lines):
            parts = [p.strip() for p in line.split(delimiter)]
            if i == 0:
                headers = parts
                header_line = "| " + " | ".join(headers) + " |"
                separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
                rows.extend([header_line, separator_line])
            else:
                rows.append("| " + " | ".join(parts) + " |")
        return "\n".join(rows)

    # Fallback: single column table
    header_line = "| Value |"
    separator_line = "| --- |"
    rows = [f"| {line} |" for line in lines]
    return "\n".join([header_line, separator_line] + rows)


_SEP_CELL = re.compile(r':?-{3,}:?')


def _split_row(line: str) -> List[str]:
    """Split a markdown table row `| a | b |` into cell values. [] if not a row."""
    s = line.strip()
    if not s.startswith('|'):
        return []
    return [c.strip() for c in s.strip('|').split('|')]


def _is_separator_row(cells: List[str]) -> bool:
    """True if every cell looks like `---`, `:---`, `---:`, or `:---:`."""
    return bool(cells) and all(_SEP_CELL.fullmatch(c) for c in cells)


def _find_last_table(lines: List[str]):
    """Return (header_idx, end_idx) of the last markdown table (end exclusive),
    or None. A table is a run of `|`-prefixed lines whose 2nd line is a
    separator row. Non-table `|`-blocks are skipped and scanning continues up."""
    i = len(lines) - 1
    while i >= 0:
        if lines[i].strip().startswith('|'):
            end = i + 1
            start = i
            while start >= 0 and lines[start].strip().startswith('|'):
                start -= 1
            start += 1
            if end - start >= 2 and _is_separator_row(_split_row(lines[start + 1])):
                return start, end
            # A `|`-block without a separator row is not a real table; keep going.
            i = start - 1
        else:
            i -= 1
    return None


def merge_markdown_tables(existing_content: str, new_content: str) -> Optional[str]:
    """Append the new content's table data rows into the existing content's
    last markdown table, when their headers match (case/whitespace-insensitive).

    Returns the merged markdown, or None to signal the caller should fall back
    to the standard separator + run-header block append (headers differ, either
    side has no table, or the new table has no data rows). Any trailing prose
    after the existing table is preserved.
    """
    ex_lines = existing_content.splitlines()
    new_lines = new_content.splitlines()
    ex_tbl = _find_last_table(ex_lines)
    new_tbl = _find_last_table(new_lines)
    if not ex_tbl or not new_tbl:
        return None
    ex_headers = [h.lower() for h in _split_row(ex_lines[ex_tbl[0]])]
    new_headers = [h.lower() for h in _split_row(new_lines[new_tbl[0]])]
    if not ex_headers or ex_headers != new_headers:
        return None
    # Data rows = everything after header + separator, up to the table end.
    new_rows = new_lines[new_tbl[0] + 2:new_tbl[1]]
    if not new_rows:
        return None
    merged = ex_lines[:ex_tbl[1]] + new_rows + ex_lines[ex_tbl[1]:]
    return "\n".join(merged)


def format_orchestrator_results(results: List[Dict[str, Any]], format_opt: str, selected_queries: Optional[List[str]] = None) -> str:
    """Format structured SQL Orchestrator results into beautiful Markdown."""
    if not results:
        return "No query results returned."

    if selected_queries:
        # Normalize selected query names for case-insensitive matching
        normalized_selected = [q.lower().strip() for q in selected_queries]
        filtered_results = []
        for r in results:
            label = r.get('label') or ''
            query_id = r.get('query_id') or ''
            if label.lower().strip() in normalized_selected or query_id.lower().strip() in normalized_selected:
                filtered_results.append(r)
        results = filtered_results

    if not results:
        return "No matching query results found after applying filters."

    # If the user wants bullet points, format a clean list of metrics
    if format_opt == 'Bullet Points':
        bullets = []
        for r in results:
            label = r.get('label') or r.get('query_id')
            if not r.get('success'):
                bullets.append(f"- **{label}**: Failed — {r.get('error')}")
            else:
                rows = r.get('result', [])
                if not rows:
                    bullets.append(f"- **{label}**: Success (No rows returned)")
                elif len(rows) == 1:
                    if len(rows[0]) == 1:
                        val = list(rows[0].values())[0]
                        bullets.append(f"- **{label}**: **{val}**")
                    else:
                        details = ", ".join([f"{k}: **{v}**" for k, v in rows[0].items()])
                        bullets.append(f"- **{label}**: {details}")
                else:
                    bullets.append(f"- **{label}**: Success ({len(rows)} rows returned)")
        return "\n".join(bullets)

    # For 'Table' or 'Summary' / 'Raw Markdown', format a comprehensive report
    markdown_lines = []

    # Part 1: Overview Summary Table
    markdown_lines.append("## SQL Execution Overview\n")
    markdown_lines.append("| Metric / Query | Status | Details / Value |")
    markdown_lines.append("| --- | --- | --- |")
    for r in results:
        label = r.get('label') or r.get('query_id')
        status = "✅ Success" if r.get('success') else "❌ Failed"
        
        if not r.get('success'):
            summary = f"Error: {r.get('error')}"
        else:
            rows = r.get('result', [])
            if not rows:
                summary = "No rows returned"
            elif len(rows) == 1:
                if len(rows[0]) == 1:
                    val = list(rows[0].values())[0]
                    summary = f"**{val}**"
                else:
                    # Single row with multiple columns: format as key1: **val1**, key2: **val2**
                    summary = ", ".join([f"{k}: **{v}**" for k, v in rows[0].items()])
            else:
                summary = f"{len(rows)} rows returned"
                
        markdown_lines.append(f"| {label} | {status} | {summary} |")

    # Part 2: Detailed Individual Tables for queries returning rows (skip single-value metrics to keep report clean)
    markdown_lines.append("\n## Detailed Query Results\n")
    has_details = False
    for r in results:
        if not r.get('success'):
            continue
        rows = r.get('result', [])
        if not rows:
            continue
            
        # Only show detailed tables for multi-row or multi-column queries
        if len(rows) == 1 and len(rows[0]) == 1:
            continue
            
        has_details = True
        label = r.get('label') or r.get('query_id')
        markdown_lines.append(f"### {label}\n")
        
        # Build individual Markdown table for this query
        headers = list(rows[0].keys())
        header_line = "| " + " | ".join(headers) + " |"
        separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
        row_lines = []
        for row in rows:
            row_vals = [str(row.get(h, "")) for h in headers]
            row_lines.append("| " + " | ".join(row_vals) + " |")
            
        markdown_lines.extend([header_line, separator_line] + row_lines + [""])

    if not has_details:
        # Remove the empty "Detailed Query Results" header if no complex queries ran
        markdown_lines = markdown_lines[:-1]

    return "\n".join(markdown_lines)


@register("wiki")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Wiki output node.

    Extracts parameters, formats parent node's output, retrieves stored PAT,
    and publishes to the specified Azure DevOps Wiki.
    """
    node_id = node.get('id')
    node_data = {**node.get('data', {}), **node.get('params', {})}

    execution_id = context.get('execution_id')
    workflow_name = context.get('workflow_name')

    # 1. Retrieve connection and output parameters
    platform = node_data.get('platform', 'Azure DevOps Wiki')
    format_opt = node_data.get('format', 'Summary')
    wiki_url = node_data.get('wikiUrl', '').strip()
    page_path = node_data.get('pagePath', '').strip()
    project = node_data.get('project', '').strip()
    organization_param = node_data.get('organization', '').strip()
    write_mode = node_data.get('writeMode', 'Append').strip() or 'Append'

    if platform != 'Azure DevOps Wiki':
        return {
            "status": "failed",
            "error": f"Platform '{platform}' is not supported yet. Only Azure DevOps Wiki is supported."
        }

    # Parse organization and project from wikiUrl
    parsed_org, parsed_project, parsed_wiki_id = parse_wiki_url(wiki_url)

    # Explicit Organization/Project node fields take precedence over the URL.
    organization = organization_param or parsed_org
    if not organization:
        # Default or fallback organization can be read from config
        logger.warning("Could not parse organization from Wiki URL. Using standard defaults.")
        organization = "default_org"

    if not project:
        project = parsed_project
    if not project:
        return {
            "status": "failed",
            "error": "Missing Project name. Specify Project in node configuration or provide a valid Wiki URL."
        }

    if not page_path:
        # Auto-generate page path if empty
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        page_path = f"/WorkflowRuns/{workflow_name or 'unnamed'}_{timestamp}"
    elif not page_path.startswith('/'):
        page_path = f"/{page_path}"

    # 2. Gather upstream node output
    edges = []
    active = executor.active_executions.get(execution_id, {}) if executor else {}
    workflow = active.get('workflow', {})
    edges = workflow.get('edges', [])

    parent_node_id = None
    for edge in edges:
        if edge.get('target') == node_id:
            parent_node_id = edge.get('source')
            break

    # Parse selected queries filter from node data
    selected_queries_str = node_data.get('selectedQueries', '').strip()
    selected_queries = [q.strip() for q in selected_queries_str.split(',') if q.strip()] if selected_queries_str else None

    message_content = ""
    is_structured_results = False

    if parent_node_id and parent_node_id in context:
        p_res = context[parent_node_id]
        if 'results' in p_res and isinstance(p_res['results'], list):
            message_content = format_orchestrator_results(p_res['results'], format_opt, selected_queries)
            is_structured_results = True
        else:
            message_content = p_res.get('final_answer') or p_res.get('output') or ""
    else:
        # Fallback: scan context for successful nodes returning an answer or output
        for key, val in context.items():
            if isinstance(val, dict) and val.get('status') == 'success':
                if 'results' in val and isinstance(val['results'], list):
                    message_content = format_orchestrator_results(val['results'], format_opt, selected_queries)
                    is_structured_results = True
                    break
                ans = val.get('final_answer') or val.get('output')
                if ans:
                    message_content = ans
                    break

    if not message_content:
        return {
            "status": "skipped",
            "output": "No content found from upstream nodes to publish to Wiki."
        }

    # 3. Format content
    if is_structured_results:
        formatted_content = message_content
    else:
        if format_opt == 'Summary' or format_opt == 'Raw Markdown':
            formatted_content = message_content
        elif format_opt == 'Bullet Points':
            formatted_content = to_bullet_points(message_content)
        elif format_opt == 'Table':
            formatted_content = to_table(message_content)
        else:
            formatted_content = message_content

    # Ensure page content starts with a title if it doesn't already
    if not formatted_content.lstrip().startswith('#'):
        page_title = page_path.split('/')[-1].replace('-', ' ').replace('_', ' ')
        formatted_content = f"# {page_title}\n\n{formatted_content}"

    # 4. Retrieve PAT token: priority is node properties PAT, then env var, then global PAT
    pat = resolve_ado_pat(node_data.get('pat', ''), node_data.get('tokenVar', 'ADO_WIKI_PAT'))

    if not pat:
        return {
            "status": "failed",
            "error": "No Azure DevOps PAT configured. Please configure credentials on the node properties or in settings."
        }

    # 4b. Supervisor/human gate — wiki publish is a write-class external action
    # that bypasses the tool-layer ask-gate (it's an output-node handler, not an
    # agent tool). When enabled, route it through the same approval primitive so
    # it can be reviewed like any other high-risk write. Denied/timeout → skip.
    try:
        from app.config import settings as _settings
        if getattr(_settings, "wiki_publish_approval_enabled", False) and execution_id and executor:
            from app.harness.tool_permissions import request_action_approval
            _port = executor.get_execution_port()
            _summary = (
                f"Approve publishing to Azure DevOps wiki page '{page_path}' "
                f"({organization}/{project}, mode: {write_mode})?"
            )
            approved, reason, _decided_by = await request_action_approval(
                execution_id, _port, "wiki_publish",
                {
                    "page_path": page_path,
                    "organization": organization,
                    "project": project,
                    "write_mode": write_mode,
                },
                _summary,
                risk_tier="high",
            )
            if not approved:
                logger.info("wiki: publish to '%s' not approved — skipping", page_path)
                return {
                    "status": "skipped",
                    "error": reason or "Wiki publish was not approved.",
                    "page_path": page_path,
                }
    except Exception as _gate_exc:  # noqa: BLE001 — never let the gate crash the node
        logger.warning("wiki: approval gate error (%s) — proceeding without gate", _gate_exc)

    # 5. Initialize client and create/update the page
    try:
        async with AzureWikiClient(pat=pat) as client:
            wiki_id = parsed_wiki_id
            if not wiki_id:
                # Retrieve the default wiki for the project
                project_wiki = await client.get_project_wiki(organization, project)
                wiki_id = project_wiki.id

            # Parse page ID from the wiki URL (e.g. Compliance.wiki/31661/OnCall-wiki-test)
            page_id = None
            if wiki_url:
                m_page = re.search(r'_wiki/wikis/([^/]+)/([0-9]+)', wiki_url)
                if m_page:
                    page_id = m_page.group(2)

            # If a direct page ID exists in the URL, fetch its actual path from the API
            if page_id:
                try:
                    url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_id}/pages/{page_id}"
                    resp = await client._make_request("GET", url, params={"api-version": "7.1"})
                    page_data = resp.json()
                    resolved_path = page_data.get("path")
                    if resolved_path:
                        page_path = resolved_path
                        logger.info(f"Resolved page ID {page_id} to actual path: '{page_path}'")
                except Exception as e:
                    logger.warning(f"Failed to resolve page path for ID {page_id}: {e}")

            # Check if the page already exists at page_path to get its ETag (version) for update
            existing_etag = None
            existing_content = ""
            try:
                import urllib.parse
                encoded_path = urllib.parse.quote(page_path, safe="/")
                url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
                include_content_str = "false" if write_mode == "Overwrite" else "true"
                resp = await client._make_request(
                    "GET", url, 
                    params={"api-version": "7.1", "path": page_path, "includeContent": include_content_str}
                )
                if resp.status_code == 200:
                    etag = resp.headers.get("etag")
                    if etag:
                        existing_etag = etag.strip('"')
                        logger.info(f"Page '{page_path}' already exists. ETag resolved: {existing_etag}")
                    page_data = resp.json()
                    existing_content = page_data.get("content", "")
            except Exception as e:
                logger.info(f"Page '{page_path}' does not exist or ETag lookup skipped: {e}")

            # Create or update the page
            if existing_etag:
                if write_mode != "Overwrite":
                    # Extract the body of the new content without the main page title header
                    page_title = page_path.split('/')[-1].replace('-', ' ').replace('_', ' ')
                    title_prefix = f"# {page_title}"
                    body_only = formatted_content
                    if formatted_content.lstrip().startswith(title_prefix):
                        body_only = formatted_content.lstrip()[len(title_prefix):].lstrip()
                        
                    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    run_header = f"### Run Execution: {timestamp}"
                    
                    if write_mode == "Append":
                        # Smart table merge: if this run produced a plain Table and
                        # the existing page's last table has the same columns, add
                        # only the new data rows to it (no separator / run header).
                        # Orchestrator reports carry multiple tables, so skip them.
                        merged = None
                        if format_opt == 'Table' and not is_structured_results:
                            merged = merge_markdown_tables(existing_content, body_only)
                        if merged is not None:
                            formatted_content = merged
                            logger.info("Smart table append: merged new rows into existing table")
                        else:
                            formatted_content = existing_content + f"\n\n---\n\n{run_header}\n\n" + body_only
                    elif write_mode == "Prepend":
                        existing_body = existing_content
                        if existing_content.lstrip().startswith(title_prefix):
                            existing_body = existing_content.lstrip()[len(title_prefix):].lstrip()
                            if existing_body.startswith("---"):
                                existing_body = existing_body[3:].lstrip()
                                
                        formatted_content = f"{title_prefix}\n\n{run_header}\n\n{body_only}\n\n---\n\n{existing_body}"

                logger.info(f"Updating existing wiki page at '{page_path}' with version ETag: '{existing_etag}' (mode: {write_mode})")
                res = await client.update_wiki_page(
                    organization=organization,
                    project=project,
                    wiki_id=wiki_id,
                    path=page_path,
                    content=formatted_content,
                    version=existing_etag
                )
            else:
                logger.info(f"Creating new wiki page at '{page_path}'")
                res = await client.create_wiki_page(
                    organization=organization,
                    project=project,
                    wiki_id=wiki_id,
                    path=page_path,
                    content=formatted_content
                )

            if res.success:
                return {
                    "status": "success",
                    "output": f"Successfully published to Wiki: {res.url}",
                    "page_url": res.url,
                    "format": format_opt,
                    "platform": platform
                }
            else:
                return {
                    "status": "failed",
                    "error": f"Failed to publish to Wiki: {res.error_message}"
                }

    except Exception as e:
        logger.error(f"Wiki node execution failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "error": f"Wiki node execution failed: {str(e)}"
        }

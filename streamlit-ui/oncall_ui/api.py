"""HTTP client for agent-api.

One place for every REST call the UI makes, mirroring the React app's
``apiClient.js`` / ``agentApiClient.js`` / ``*Service.js`` modules. Methods
return parsed JSON; any non-2xx response raises :class:`ApiError` carrying the
backend's ``detail`` (or wrapped ``message``) so pages can show it verbatim.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests

from . import config


class ApiError(Exception):
    """A failed agent-api call. ``status`` is None for network errors."""

    def __init__(self, message: str, status: Optional[int] = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def _q(value: str) -> str:
    """Percent-encode one path segment (like ``encodeURIComponent``)."""
    return quote(str(value), safe="")


def error_message(payload: Any, fallback: str) -> str:
    """Pull a human-readable message out of an error body.

    FastAPI's default is ``{detail: str | obj}``; the app's error middleware
    wraps HTTPExceptions as ``{error, message: {message, errors[]}}``.
    """
    if isinstance(payload, dict):
        inner = payload.get("message", payload.get("detail"))
        if isinstance(inner, dict):
            errors = inner.get("errors")
            if isinstance(errors, list) and errors:
                return "\n".join(str(e) for e in errors)
            if inner.get("message"):
                return str(inner["message"])
        if isinstance(inner, str) and inner:
            return inner
        if isinstance(payload.get("error"), str):
            return payload["error"]
    if isinstance(payload, str) and payload:
        return payload
    return fallback


class AgentApi:
    """Thin wrapper over ``requests`` bound to the agent-api base URL."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        self.base_url = (base_url or config.api_base_url()).rstrip("/")
        self.session = session or requests.Session()
        key = config.api_key() if api_key is None else api_key
        if key:
            self.session.headers["X-API-Key"] = key

    # ── core ──────────────────────────────────────────────────────────────
    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def request(self, method: str, path: str, *, params: Optional[dict] = None,
                json: Any = None, files: Any = None, data: Any = None,
                timeout: Optional[float] = config.DEFAULT_TIMEOUT) -> Any:
        try:
            resp = self.session.request(
                method, self.url(path), params=params, json=json, files=files,
                data=data, timeout=timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"Cannot reach agent-api at {self.base_url}: {exc}") from exc
        if not resp.ok:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ApiError(error_message(body, f"HTTP {resp.status_code}"),
                           status=resp.status_code, detail=detail)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> Any:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw) -> Any:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)

    def open_stream(self, path: str, timeout: Optional[float] = None) -> requests.Response:
        """Open a long-lived SSE response (caller iterates and closes it)."""
        try:
            resp = self.session.get(self.url(path), stream=True, timeout=(10, timeout),
                                    headers={"Accept": "text/event-stream"})
        except requests.RequestException as exc:
            raise ApiError(f"Cannot open event stream: {exc}") from exc
        if not resp.ok:
            resp.close()
            raise ApiError(f"Event stream failed: HTTP {resp.status_code}", status=resp.status_code)
        return resp

    # ── health / status / settings ────────────────────────────────────────
    def health(self) -> dict:
        return self.get("/health", timeout=5)

    def status(self) -> dict:
        return self.get("/status")

    def settings(self) -> dict:
        return self.get("/settings")

    def update_general_settings(self, payload: dict) -> dict:
        return self.put("/settings/general", json=payload)

    def feature_flags(self) -> dict:
        return self.get("/settings/features")

    def update_feature_flags(self, updates: dict) -> dict:
        return self.put("/settings/features", json={"updates": updates})

    def clear_data(self, clear_jobs: bool = False) -> dict:
        return self.post("/clear", params={"clear_jobs": str(clear_jobs).lower()})

    # ── workflows ─────────────────────────────────────────────────────────
    def list_workflows(self) -> List[dict]:
        return self.get("/workflows") or []

    def get_workflow(self, name: str) -> dict:
        return self.get(f"/workflows/{_q(name)}")

    def create_workflow(self, workflow: dict) -> dict:
        body = {**workflow, "type": workflow.get("type") or "workflow"}
        return self.post("/workflows", json=body)

    def update_workflow(self, name: str, workflow: dict) -> dict:
        return self.put(f"/workflows/{_q(name)}", json=workflow)

    def delete_workflow(self, name: str, delete_scripts: bool = True) -> None:
        self.delete(f"/workflows/{_q(name)}", params={"delete_scripts": str(delete_scripts).lower()})

    def execute_workflow(self, name: str, *, background: bool = False, query: Optional[str] = None,
                         session_id: Optional[str] = None, history: Optional[list] = None,
                         output_mode: Optional[str] = None, permission_mode: Optional[str] = None,
                         timeout: Optional[float] = None) -> dict:
        """POST /execute. Foreground runs are synchronous and can take minutes,
        so the default timeout is None (the server owns the run's lifetime)."""
        params: Dict[str, Any] = {"background": str(background).lower()}
        if query:
            params["query"] = query
        if output_mode:
            params["output_mode"] = output_mode
        if permission_mode:
            params["permission_mode"] = permission_mode
        if session_id:
            params["session_id"] = session_id
        body = {"history": history} if history else None
        return self.post(f"/workflows/{_q(name)}/execute", params=params, json=body,
                         timeout=config.DEFAULT_TIMEOUT if background else timeout)

    def workflow_executions(self, name: str, limit: int = 50) -> List[dict]:
        return self.get(f"/workflows/{_q(name)}/executions", params={"limit": limit}) or []

    def workflow_stream(self, name: str) -> requests.Response:
        return self.open_stream(f"/workflows/{_q(name)}/stream")

    def sql_files(self, workflow_name: str) -> Any:
        return self.get(f"/workflows/{_q(workflow_name)}/sql-files")

    # ── executions ────────────────────────────────────────────────────────
    def list_executions(self, limit: int = 100, *, workflow_name: Optional[str] = None,
                        exclude_chat: bool = False) -> List[dict]:
        params: Dict[str, Any] = {"limit": limit}
        if workflow_name:
            params["workflow_name"] = workflow_name
        if exclude_chat:
            params["exclude_chat"] = "true"
        return self.get("/executions", params=params) or []

    def get_execution(self, execution_id: str) -> dict:
        return self.get(f"/executions/{_q(execution_id)}")

    def delete_execution(self, execution_id: str) -> None:
        self.delete(f"/executions/{_q(execution_id)}")

    def delete_all_executions(self) -> dict:
        return self.delete("/executions")

    def active_workflows(self) -> List[str]:
        return self.get("/executions/active", timeout=10) or []

    def clear_active_executions(self) -> dict:
        return self.delete("/executions/active")

    def cancel_workflow(self, name: str) -> dict:
        return self.delete(f"/executions/active/{_q(name)}")

    def approve_hitl(self, execution_id: str, request_id: str, approved: bool, reason: str = "") -> dict:
        return self.post(f"/executions/{_q(execution_id)}/approve",
                         json={"request_id": request_id, "approved": approved, "reason": reason})

    # ── chat sessions ─────────────────────────────────────────────────────
    def list_sessions(self, include_archived: bool = False, limit: int = 100) -> List[dict]:
        return self.get("/sessions", params={"include_archived": str(include_archived).lower(),
                                             "limit": limit}) or []

    def create_session(self, title: str = "New chat", workflow_name: Optional[str] = None,
                       model: Optional[str] = None) -> dict:
        return self.post("/sessions", json={"title": title, "workflow_name": workflow_name, "model": model})

    def get_session(self, session_id: str) -> dict:
        return self.get(f"/sessions/{_q(session_id)}")

    def update_session(self, session_id: str, *, title: Optional[str] = None,
                       archived: Optional[bool] = None, is_important: Optional[bool] = None) -> dict:
        body: Dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if archived is not None:
            body["archived"] = archived
        if is_important is not None:
            body["is_important"] = is_important
        return self.patch(f"/sessions/{_q(session_id)}", json=body)

    def delete_session(self, session_id: str) -> None:
        self.delete(f"/sessions/{_q(session_id)}")

    # ── MCP servers ───────────────────────────────────────────────────────
    def mcp_servers(self) -> Dict[str, dict]:
        return self.get("/mcp-config/servers") or {}

    def create_mcp_server(self, name: str, server: dict) -> dict:
        return self.post("/mcp-config/servers", json={"name": name, **server})

    def update_mcp_server(self, name: str, server: dict, new_name: Optional[str] = None) -> dict:
        body = {**server}
        if new_name and new_name != name:
            body["name"] = new_name
        return self.put(f"/mcp-config/servers/{_q(name)}", json=body)

    def delete_mcp_server(self, name: str) -> None:
        self.delete(f"/mcp-config/servers/{_q(name)}")

    def test_mcp_server(self, name: str, server: dict) -> dict:
        return self.post(f"/mcp-config/test/{_q(name)}", json=server, timeout=120)

    def mcp_input_values(self) -> Dict[str, Any]:
        return self.get("/mcp-config/input-values") or {}

    def update_mcp_input_value(self, input_id: str, value: str) -> dict:
        return self.patch(f"/mcp-config/input-values/{_q(input_id)}", json={"value": value})

    # ── LLM configs / model keys ──────────────────────────────────────────
    def llms(self) -> Dict[str, dict]:
        data = self.get("/llm-config") or {}
        return data.get("llms", {}) if isinstance(data, dict) else {}

    def create_llm(self, name: str, cfg: dict) -> dict:
        return self.post("/llm-config", json={"name": name, **cfg})

    def update_llm(self, name: str, cfg: dict) -> dict:
        return self.put(f"/llm-config/{_q(name)}", json=cfg)

    def delete_llm(self, name: str) -> None:
        self.delete(f"/llm-config/{_q(name)}")

    def bulk_delete_llms(self, names: Iterable[str]) -> dict:
        return self.post("/llm-config/bulk-delete", json={"names": list(names)})

    def test_llm(self, name: str) -> dict:
        return self.post(f"/llm-config/{_q(name)}/test", json={}, timeout=120)

    def discover_models(self, provider: str = "AWS Bedrock") -> dict:
        return self.post("/llm-config/discover/models", json={"provider": provider}, timeout=120)

    def add_discovered_models(self, models: List[dict], region: Optional[str]) -> dict:
        return self.post("/llm-config/discover/add", json={"models": models, "region": region})

    def model_keys(self) -> List[dict]:
        data = self.get("/model-keys") or {}
        return data.get("keys", []) if isinstance(data, dict) else list(data)

    def upsert_model_key(self, data: dict) -> dict:
        return self.post("/model-keys/upsert", json=data)

    def delete_model_key(self, provider: str) -> None:
        self.delete(f"/model-keys/{_q(provider)}")

    # ── certificates ──────────────────────────────────────────────────────
    def certificates(self) -> List[str]:
        return self.get("/certificates") or []

    def upload_certificate(self, filename: str, content: bytes, name: Optional[str] = None) -> dict:
        data = {"name": name} if name else None
        return self.post("/certificates", files={"file": (filename, content)}, data=data)

    def delete_certificate(self, filename: str) -> None:
        self.delete(f"/certificates/{_q(filename)}")

    # ── tools / profiles / skills / jobs ──────────────────────────────────
    def tools(self) -> dict:
        return self.get("/tools") or {}

    def agent_profile_catalog(self) -> dict:
        return self.get("/agent-profiles/catalog") or {}

    def skills(self) -> dict:
        return self.get("/skills") or {}

    def get_skill(self, name: str) -> dict:
        return self.get(f"/skills/fs/{_q(name)}")

    def create_skill(self, name: str, content: str) -> dict:
        return self.post("/skills/fs", json={"name": name, "content": content})

    def update_skill(self, name: str, content: str) -> dict:
        return self.put(f"/skills/fs/{_q(name)}", json={"content": content})

    def delete_skill(self, name: str) -> dict:
        return self.delete(f"/skills/fs/{_q(name)}")

    def indexing_status(self) -> dict:
        return self.get("/jobs/indexing/status") or {}

    # ── CloudWatch / AWS ──────────────────────────────────────────────────
    def aws_profiles(self) -> dict:
        return self.get("/log-watch/aws-profiles") or {}

    def discover_log_groups(self, prefix: Optional[str], region: str = "us-east-1",
                            limit: int = 200, profile: Optional[str] = None) -> dict:
        params: Dict[str, Any] = {"region": region, "limit": limit}
        if prefix:
            params["prefix"] = prefix
        if profile:
            params["profile"] = profile
        return self.get("/log-watch/discover-log-groups", params=params, timeout=60)

    # ── Azure DevOps wiki pickers ─────────────────────────────────────────
    def ado_organizations(self, pat: str = "", token_var: str = "ADO_WIKI_PAT") -> dict:
        return self.post("/wiki/organizations", json={"pat": pat, "tokenVar": token_var})

    def ado_projects(self, organization: str, pat: str = "", token_var: str = "ADO_WIKI_PAT") -> dict:
        return self.post("/wiki/projects", json={"organization": organization, "pat": pat,
                                                 "tokenVar": token_var})

    def ado_wikis(self, organization: str, project: str, pat: str = "",
                  token_var: str = "ADO_WIKI_PAT") -> dict:
        return self.post("/wiki/wikis", json={"organization": organization, "project": project,
                                              "pat": pat, "tokenVar": token_var})

    # ── code analyzer / codegraph ─────────────────────────────────────────
    def code_analyzer_repos(self, refresh: bool = False) -> dict:
        return self.get("/code-analyzer/repos", params={"refresh": "true"} if refresh else None) or {}

    def codegraph_repos(self) -> dict:
        return self.get("/codegraph/repos") or {}

    def codegraph_files(self, repo: str) -> dict:
        return self.get(f"/codegraph/repos/{_q(repo)}/files") or {}

    def codegraph_file_nodes(self, repo: str, file_path: str) -> dict:
        return self.get(f"/codegraph/repos/{_q(repo)}/file-nodes", params={"file": file_path}) or {}

    def codegraph_node(self, repo: str, qualified_name: str) -> dict:
        return self.get(f"/codegraph/repos/{_q(repo)}/node", params={"qualified_name": qualified_name})

    def codegraph_layout(self, repo: str, level: str = "overview", max_nodes: Optional[int] = None) -> dict:
        params: Dict[str, Any] = {"level": level}
        if max_nodes:
            params["max_nodes"] = max_nodes
        return self.get(f"/codegraph/repos/{_q(repo)}/layout", params=params, timeout=None) or {}

    def codegraph_reindex(self, repo: str) -> dict:
        return self.post(f"/codegraph/index/{_q(repo)}", timeout=None)

    def codegraph_delete_index(self, repo: str) -> dict:
        return self.delete(f"/codegraph/index/{_q(repo)}")

    def repo_branches(self, repo: str) -> dict:
        return self.get(f"/codegraph/repos/{_q(repo)}/branches")

    def repo_fetch(self, repo: str) -> dict:
        return self.post(f"/codegraph/repos/{_q(repo)}/fetch", timeout=None)

    def repo_checkout(self, repo: str, branch: str) -> dict:
        return self.post(f"/codegraph/repos/{_q(repo)}/checkout", json={"branch": branch}, timeout=None)


def get_api() -> AgentApi:
    """Process-wide client (``requests.Session`` is safe to share for this use)."""
    global _API
    if _API is None:
        _API = AgentApi()
    return _API


_API: Optional[AgentApi] = None

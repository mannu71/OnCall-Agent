"""Shared Streamlit helpers: data loaders, run-state tracking, flash messages, styling."""
from __future__ import annotations

import html
import time
from typing import Any, Callable, Dict, List, Optional, Set

import streamlit as st

from .api import ApiError, get_api

PENDING_GRACE_S = 20  # a just-triggered run counts as running until the API lists it

STATUS_COLORS = {
    "success": "#059669", "running": "#d97706", "failure": "#dc2626",
    "disabled": "#94a3b8", "indexing": "#d97706", "info": "#2563eb",
}

CSS = """
<style>
:root { --oc-accent: #dc2626; }
.block-container { padding-top: 2.2rem; }
.oc-eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  color: #64748b; margin-bottom: 4px; }
.oc-stat { font-size: 2.1rem; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.oc-hint { font-size: 12px; color: #94a3b8; }
.oc-trend-up { color: #059669; font-size: 12px; font-weight: 600; }
.oc-trend-down { color: #dc2626; font-size: 12px; font-weight: 600; }
.oc-pill { display: inline-flex; align-items: center; gap: 5px; padding: 1px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600; border: 1px solid; white-space: nowrap; }
.oc-dot { width: 8px; height: 8px; border-radius: 999px; display: inline-block; }
.oc-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.oc-muted { color: #94a3b8; }
.oc-feed-row { display: flex; align-items: center; gap: 10px; padding: 7px 0;
  border-bottom: 1px solid rgba(148,163,184,.18); }
.oc-feed-time { width: 42px; text-align: right; color: #94a3b8; font-family: ui-monospace, monospace;
  font-size: 11px; }
.oc-feed-title { font-weight: 600; font-size: 13px; }
.oc-feed-meta { font-size: 11px; color: #64748b; }
.oc-next-name { font-size: 20px; font-weight: 700; }
.oc-next-time { font-size: 28px; font-weight: 700; color: var(--oc-accent); font-variant-numeric: tabular-nums; }
.oc-step { font-family: ui-monospace, monospace; font-size: 12px; }
@media (prefers-color-scheme: dark) { .oc-feed-meta { color: #94a3b8; } }
</style>
"""


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    show_flash()


# ── flash messages (survive st.rerun) ─────────────────────────────────────
_ICONS = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}


def flash(message: str, kind: str = "info") -> None:
    st.session_state.setdefault("_flash", []).append((message, kind))


def show_flash() -> None:
    for message, kind in st.session_state.pop("_flash", []):
        st.toast(message, icon=_ICONS.get(kind, "ℹ️"))


def guarded(fn: Callable[[], Any], error_prefix: str = "", *, quiet: bool = False) -> Any:
    """Run an API call; show the error inline instead of a traceback."""
    try:
        return fn()
    except ApiError as exc:
        if not quiet:
            st.error(f"{error_prefix}{exc}" if error_prefix else str(exc))
        return None


# ── timezone ──────────────────────────────────────────────────────────────
def viewer_tz() -> str:
    """The viewer's browser zone, else the app's global timezone, else UTC."""
    tz = None
    try:
        tz = st.context.timezone
    except Exception:  # noqa: BLE001 — older Streamlit / bare mode
        tz = None
    if tz:
        return tz
    s = load_settings()
    return (s or {}).get("global_timezone") or "UTC"


# ── cached loaders (cleared after mutations) ──────────────────────────────
@st.cache_data(ttl=10, show_spinner=False)
def load_workflows() -> List[dict]:
    return get_api().list_workflows()


@st.cache_data(ttl=60, show_spinner=False)
def load_settings() -> Optional[dict]:
    try:
        return get_api().settings()
    except ApiError:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def load_llms() -> Dict[str, dict]:
    return get_api().llms()


@st.cache_data(ttl=30, show_spinner=False)
def load_mcp_servers() -> Dict[str, dict]:
    return get_api().mcp_servers()


@st.cache_data(ttl=30, show_spinner=False)
def load_skills() -> List[dict]:
    return (get_api().skills() or {}).get("filesystem", []) or []


@st.cache_data(ttl=120, show_spinner=False)
def load_code_repos(refresh: bool = False) -> dict:
    return get_api().code_analyzer_repos(refresh=refresh)


@st.cache_data(ttl=300, show_spinner=False)
def load_aws_profiles() -> List[dict]:
    return (get_api().aws_profiles() or {}).get("profiles", []) or []


@st.cache_data(ttl=4, show_spinner=False)
def load_active() -> List[str]:
    try:
        return get_api().active_workflows()
    except ApiError:
        return []


def invalidate_workflows() -> None:
    load_workflows.clear()
    load_active.clear()


# ── running state (active API list + locally-triggered pending runs) ─────
def _pending() -> Dict[str, float]:
    return st.session_state.setdefault("_pending_runs", {})


def mark_pending(name: str) -> None:
    _pending()[name] = time.time()
    load_active.clear()


def clear_pending(name: str) -> None:
    _pending().pop(name, None)


def running_set() -> Set[str]:
    active = set(load_active())
    now = time.time()
    pend = _pending()
    for name, ts in list(pend.items()):
        if name in active or now - ts > PENDING_GRACE_S:
            pend.pop(name, None)  # the API took over (or it never started)
    return active | set(pend)


def trigger_run(name: str) -> bool:
    """Fire a background run (Run now). Returns True when it started."""
    if name in running_set():
        flash(f"'{name}' is already running", "warning")
        return False
    mark_pending(name)
    try:
        res = get_api().execute_workflow(name, background=True)
    except ApiError as exc:
        clear_pending(name)
        flash(f"Failed: {exc}", "error")
        return False
    status = (res or {}).get("status")
    if status in ("already_running", "inactive"):
        clear_pending(name)
        flash((res or {}).get("message") or status, "warning")
        return False
    flash(f"'{name}' started", "success")
    return True


# ── small renderers ───────────────────────────────────────────────────────
def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def pill(label: str, color: str, *, dot: bool = True) -> str:
    d = f'<span class="oc-dot" style="background:{color}"></span>' if dot else ""
    return (f'<span class="oc-pill" style="color:{color};border-color:{color}55;'
            f'background:{color}14">{d}{esc(label)}</span>')


def stat_card(eyebrow: str, value: Any, hint: str = "", trend: Optional[float] = None) -> None:
    with st.container(border=True):
        trend_html = ""
        if isinstance(trend, (int, float)):
            cls = "oc-trend-up" if trend >= 0 else "oc-trend-down"
            trend_html = f'<span class="{cls}">{"▲" if trend >= 0 else "▼"} {abs(round(trend))}%</span> '
        st.markdown(
            f'<div class="oc-eyebrow">{esc(eyebrow)}</div>'
            f'<div class="oc-stat">{esc(value)}</div>'
            f'<div>{trend_html}<span class="oc-hint">{esc(hint)}</span></div>',
            unsafe_allow_html=True,
        )


def fmt_tokens_k(n: int) -> str:
    if n <= 0:
        return "—"
    return f"{n / 1000:.1f}K" if n >= 1000 else f"{n:,}"


def slug(name: Any) -> str:
    return "-".join(str(name or "workflow").strip().split()).lower()

"""Settings — general, MCP servers, models & credentials, certificates, feature flags."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

import streamlit as st

from oncall_ui import config
from oncall_ui.api import ApiError, get_api
from oncall_ui.ui import (
    flash, guarded, invalidate_workflows, load_llms, load_mcp_servers, load_settings, setup_page,
)

MCP_ICONS = ["🔧", "🎭", "🗄️", "📊", "🐘", "🔍", "📁", "☁️", "🌐", "📡", "⚡", "🔐", "📝", "🤖", "💾"]
LLM_ICONS = ["🧠", "⚡", "🤖", "✨", "🚀", "💡", "🎯", "🔮", "🌟", "💫", "🎓", "📚", "🔬", "🎨", "🌈"]
BEDROCK_MODEL_SUGGESTIONS = [
    "anthropic.claude-3-5-sonnet-20241022-v2:0", "anthropic.claude-3-5-haiku-20241022-v1:0",
    "anthropic.claude-3-opus-20240229-v1:0", "anthropic.claude-3-sonnet-20240229-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0", "meta.llama3-1-70b-instruct-v1:0",
    "meta.llama3-1-8b-instruct-v1:0",
]
REASONING_MODELS = ("o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o4-mini")
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1"]
INPUT_VAR_RE = re.compile(r"\$\{input:([^}]+)\}")

setup_page()
api = get_api()
ss = st.session_state
ss.setdefault("conn_status", {})      # MCP server name -> (ok, message)
ss.setdefault("llm_status", {})       # LLM name -> (ok, message)


def is_reasoning(model: str) -> bool:
    return bool(model) and model.lower().startswith(REASONING_MODELS)


def parse_args(text: str) -> list:
    """One arg per line; also splits the legacy single-line ``-y, pkg, url`` paste."""
    args = [a.strip() for a in text.splitlines() if a.strip()]
    if len(args) != 1 or ", " not in args[0] or not args[0].startswith("-y,"):
        return args
    first, _, rest = args[0].partition(",")
    parts, rest = [first.strip()], rest.strip()
    url_at, comma = rest.find("://"), rest.find(", ")
    if comma != -1 and (url_at == -1 or comma < url_at):
        parts.append(rest[:comma].strip())
        rest = rest[comma + 1:].strip()
    if rest:
        parts.append(rest)
    return parts if len(parts) >= 2 else args


def _status_line(status):
    if not status:
        return ""
    ok, msg = status
    return f":green[● {msg or 'Connected'}]" if ok else f":red[● {msg or 'Connection failed'}]"


def test_mcp(name: str, cfg: dict) -> None:
    try:
        res = api.test_mcp_server(name, cfg)
        ss.conn_status[name] = (bool(res.get("success")), res.get("message") if res.get("success") else res.get("error"))
    except ApiError as exc:
        ss.conn_status[name] = (False, str(exc))


st.title("Settings")
tab_general, tab_mcp, tab_models, tab_certs, tab_flags = st.tabs(
    ["General", "MCP servers", "Models", "Certificates", "Feature flags"])

# ── General ───────────────────────────────────────────────────────────────
with tab_general:
    st.caption("Look, feel, and default behavior of the OnCall Agent.")
    t0 = time.time()
    try:
        health = api.health()
        latency = round((time.time() - t0) * 1000)
    except ApiError as exc:
        health, latency = {"status": "error", "message": str(exc)}, None
    llms = guarded(load_llms, quiet=True) or {}
    keys = guarded(api.model_keys, quiet=True) or []
    bedrock_key = next((k for k in keys if k.get("provider") == "AWS Bedrock"
                        or "bedrock" in (k.get("provider") or "").lower().replace(" ", "")), None)
    has_creds = bool(bedrock_key and (bedrock_key.get("has_api_key") or bedrock_key.get("has_secret_key")
                                      or bedrock_key.get("has_access_credentials") or bedrock_key.get("endpoint")))
    g1, g2, g3 = st.columns([1, 1, 1])
    with g1.container(border=True):
        ok = health.get("status") == "healthy"
        st.metric("API", "Healthy" if ok else "Unreachable")
        st.caption(f"{latency} ms · {config.api_base_url()}" if ok else health.get("message") or "Check connection")
    with g2.container(border=True):
        st.metric("Models", f"{len(llms)} configured" if llms else "None")
        st.caption("Provider credentials configured" if has_creds and llms
                   else "Add provider credentials in Models" if llms else "None configured")
    with g3.container(border=True):
        st.metric("Scheduler", "Running" if health.get("scheduler_running") else "Stopped")
        st.caption(f"{health.get('active_workflows', 0)} active workflow(s)")
    if st.button("Run health check", icon=":material/monitor_heart:"):
        st.rerun()

    st.subheader("Workspace")
    ss.setdefault("workspace_name", "KYC Protect — Production")
    st.text_input("Workspace name", key="workspace_name", help="Shown in this browser session only.")
    settings = load_settings() or {}
    zones = sorted(z for z in available_timezones() if "/" in z or z == "UTC")
    current_tz = settings.get("global_timezone") or "UTC"
    if current_tz not in zones:
        zones.insert(0, current_tz)

    def _tz_label(z):
        try:
            off = datetime.now(ZoneInfo(z)).strftime("%z")
            return f"{z.replace('_', ' ').replace('/', ' / ')} (GMT{off[:3]}:{off[3:]})"
        except Exception:  # noqa: BLE001
            return z

    tz_pick = st.selectbox("Global timezone", zones, index=zones.index(current_tz), format_func=_tz_label,
                           help="Application-wide timezone for schedules and timestamps.")
    if tz_pick != current_tz:
        try:
            api.update_general_settings({"global_timezone": tz_pick})
            load_settings.clear()
            flash("Timezone updated", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to update timezone: {exc}")
    with st.expander("Runtime settings (read-only)"):
        st.json(settings)

# ── MCP servers ───────────────────────────────────────────────────────────
@st.dialog("MCP server", width="large")
def mcp_dialog(editing: str | None):
    servers = load_mcp_servers()
    cur = servers.get(editing, {}) if editing else {}
    name = st.text_input("Name", value=editing or "")
    types = ["stdio", "sse", "http"]
    ctype = st.selectbox("Connection type", types, index=types.index(cur.get("type", "stdio"))
                         if cur.get("type", "stdio") in types else 0)
    c1, c2 = st.columns([3, 1])
    command = c1.text_input("Command" if ctype == "stdio" else "URL / command", value=cur.get("command", ""))
    icon = c2.selectbox("Icon", MCP_ICONS, index=MCP_ICONS.index(cur["icon"]) if cur.get("icon") in MCP_ICONS else 0)
    args_text = st.text_area("Arguments (one per line)",
                             value="\n".join(cur.get("args") or []) if isinstance(cur.get("args"), list) else "",
                             height=120, placeholder="-y\n@modelcontextprotocol/server-postgres@0.6.2\npostgresql://…")
    input_vars = INPUT_VAR_RE.findall(args_text)
    values = {}
    if input_vars:
        existing = guarded(api.mcp_input_values, quiet=True) or {}
        st.caption("Input variables detected in the arguments")
        for var in input_vars:
            values[var] = st.text_input(f"${{input:{var}}}", value=str(existing.get(var, "")), type="password",
                                        key=f"inp_{var}")
    description = st.text_input("Description (optional)", value=cur.get("description", ""))
    env_text = st.text_area("Environment variables (JSON, optional)",
                            value=json.dumps(cur["env"], indent=2) if cur.get("env") else "", height=100,
                            placeholder='{"NODE_EXTRA_CA_CERTS": "/path/to/global-bundle.pem"}')
    # Validate on click: typed text only commits on blur, so a button disabled
    # on an empty field would swallow the first click after typing.
    if st.button("Save", type="primary", use_container_width=True):
        if not name.strip():
            st.error("Name is required")
            return
        try:
            env = json.loads(env_text) if env_text.strip() else {}
        except ValueError:
            st.error("Invalid JSON in environment variables")
            return
        cfg = {"command": command, "args": parse_args(args_text), "type": ctype, "icon": icon,
               "description": description, "env": env}
        try:
            for var, val in values.items():
                if val:
                    api.update_mcp_input_value(var, val)
            if editing:
                api.update_mcp_server(editing, cfg, new_name=name.strip())
                if name.strip() != editing:
                    _sync_mcp_rename(editing, name.strip())
            else:
                api.create_mcp_server(name.strip(), cfg)
        except ApiError as exc:
            st.error(f"Failed to save server configuration: {exc}")
            return
        load_mcp_servers.clear()
        test_mcp(name.strip(), cfg)
        flash(f"Saved server: {name.strip()}", "success")
        st.rerun()


def _sync_mcp_rename(old: str, new: str) -> None:
    """Point workflow MCP nodes (and legacy MCP tool nodes) at the renamed server."""
    for wf in guarded(api.list_workflows, quiet=True) or []:
        changed = False
        for n in wf.get("nodes") or []:
            p = n.get("params") or {}
            if n.get("type") == "mcp_server" and old in [s.strip() for s in str(p.get("servers", "")).split(",")]:
                p["servers"] = ",".join(new if s.strip() == old else s.strip()
                                        for s in str(p["servers"]).split(",") if s.strip())
                changed = True
            d = n.get("data") or {}
            if n.get("type") == "tool" and d.get("toolType") == "mcp-server" and d.get("label") == old:
                d["label"] = new
                changed = True
        if changed:
            guarded(lambda: api.update_workflow(wf["name"], wf), quiet=True)
    invalidate_workflows()


@st.dialog("Delete MCP server?")
def mcp_delete(name: str):
    st.write(f'Delete "{name}"?')
    if st.button("Delete", type="primary"):
        try:
            api.delete_mcp_server(name)
            load_mcp_servers.clear()
            ss.conn_status.pop(name, None)
            flash(f"Deleted server: {name}", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to delete server: {exc}")


with tab_mcp:
    st.caption("Data sources and tools the agent can reach over MCP.")
    servers = guarded(load_mcp_servers, "Failed to load MCP servers: ") or {}
    a, b, _ = st.columns([1, 1, 4])
    if a.button("Add server", icon=":material/add:", type="primary"):
        mcp_dialog(None)
    if b.button("Test all", disabled=not servers):
        with st.spinner(f"Testing {len(servers)} servers…"):
            for n, cfg in servers.items():
                test_mcp(n, cfg)
    if not servers:
        st.info("No MCP servers configured yet.")
    for n, cfg in servers.items():
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([6, 1, 1, 1], vertical_alignment="center")
            args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
            preview = " ".join([cfg.get("command") or ""] + args[:2]) + ("…" if len(args) > 2 else "")
            c1.markdown(f"**{cfg.get('icon') or '🔧'} {n}** · `{cfg.get('type') or 'stdio'}`"
                        + (" · :gray[disabled]" if cfg.get("enabled") is False else ""))
            c1.caption(cfg.get("description") or preview)
            if ss.conn_status.get(n):
                c1.markdown(_status_line(ss.conn_status[n]))
            if c2.button("Test", key=f"test_{n}"):
                with st.spinner("Testing connection…"):
                    test_mcp(n, cfg)
                st.rerun()
            if c3.button("Edit", key=f"edit_{n}"):
                mcp_dialog(n)
            if c4.button("Delete", key=f"del_{n}"):
                mcp_delete(n)

# ── Models ────────────────────────────────────────────────────────────────
@st.dialog("AWS Bedrock credentials")
def bedrock_dialog(existing: dict | None):
    ex = existing or {}
    st.caption("Stored server-side. Masked values are left unchanged unless you edit them.")
    akid = st.text_input("Access key ID", value=ex.get("access_key_id") or "")
    secret = st.text_input("Secret access key", value=ex.get("secret_access_key") or "", type="password")
    token = st.text_input("Session token (optional)", value=ex.get("session_token") or "", type="password")
    region_val = ex.get("region") or "us-east-1"
    regions = REGIONS if region_val in REGIONS else [region_val] + REGIONS
    region = st.selectbox("Region", regions, index=regions.index(region_val))
    desc = st.text_input("Label (optional)", value=ex.get("description") or "")
    c1, c2 = st.columns(2)
    if c1.button("Save", type="primary", use_container_width=True):
        data = {"provider": "AWS Bedrock"}
        for field, val in (("access_key_id", akid), ("secret_access_key", secret), ("session_token", token)):
            if val and val != (ex.get(field) or ""):
                data[field] = val
        data["region"] = region
        if desc:
            data["description"] = desc
        try:
            api.upsert_model_key(data)
            flash("AWS Bedrock credentials saved", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to save credentials: {exc}")
    if existing and c2.button("Delete credentials", use_container_width=True):
        try:
            api.delete_model_key("AWS Bedrock")
            flash("AWS Bedrock credentials removed", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to delete: {exc}")


@st.dialog("LLM")
def llm_dialog(editing: str | None):
    cur = (load_llms().get(editing) or {}) if editing else {}
    st.caption("Save a named (provider, model, params) combo. Workflows reference it by name.")
    icon = st.selectbox("Icon", LLM_ICONS, index=LLM_ICONS.index(cur["icon"]) if cur.get("icon") in LLM_ICONS else 0)
    suggestion = st.selectbox("Suggested Bedrock models", ["(type your own below)"] + BEDROCK_MODEL_SUGGESTIONS)
    model = st.text_input("Model (exact Bedrock model id)",
                          value=cur.get("model") or ("" if suggestion.startswith("(") else suggestion))
    temperature = None
    if is_reasoning(model):
        st.caption("Reasoning models do not support temperature settings.")
    else:
        temperature = st.slider("Temperature", 0.0, 1.0, float(cur.get("temperature") or 0.0), 0.05)
    if st.button("Save", type="primary", use_container_width=True):
        if not model.strip():
            st.error("Model is required")
            return
        cfg = {"provider": "AWS Bedrock", "model": model.strip(), "icon": icon}
        if temperature is not None:
            cfg["temperature"] = temperature
        try:
            if editing:
                api.update_llm(editing, cfg)
            else:
                api.create_llm(model.strip(), cfg)
        except ApiError as exc:
            st.error(f"Failed to save LLM configuration: {exc}")
            return
        load_llms.clear()
        flash(f"Saved LLM: {editing or model.strip()}", "success")
        st.rerun()


@st.dialog("Discover Bedrock models", width="large")
def discover_dialog(region: str | None):
    if st.button("Discover", type="primary"):
        try:
            with st.spinner("Listing foundation models…"):
                ss.discovered = api.discover_models("AWS Bedrock").get("models", [])
        except ApiError as exc:
            st.error(f"Failed to discover models: {exc}")
    models = ss.get("discovered") or []
    if not models:
        st.caption("Uses the stored Bedrock credentials to call ListFoundationModels.")
        return
    addable = [m for m in models if not m.get("already_exists")]
    picked = st.multiselect("Models to add", [m["name"] for m in addable], default=[m["name"] for m in addable])
    st.caption(f"{len(models) - len(addable)} already configured")
    if st.button(f"Add {len(picked)} selected", disabled=not picked):
        try:
            res = api.add_discovered_models([m for m in models if m["name"] in picked],
                                            models[0].get("region") or region)
            load_llms.clear()
            ss.discovered = []
            flash(f"Added {res.get('added_count', 0)} models, skipped {res.get('skipped_count', 0)} existing",
                  "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to add models: {exc}")


with tab_models:
    st.caption("Provider credentials and the named models your workflows reference.")
    keys = guarded(api.model_keys, quiet=True) or []
    bedrock_key = next((k for k in keys if k.get("provider") == "AWS Bedrock"
                        or "bedrock" in (k.get("provider") or "").lower().replace(" ", "")), None)
    with st.container(border=True):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        configured = bool(bedrock_key and (bedrock_key.get("has_api_key") or bedrock_key.get("has_secret_key")
                                           or bedrock_key.get("has_access_credentials") or bedrock_key.get("endpoint")))
        c1.markdown("**AWS Bedrock** · " + (f":green[configured] · region `{bedrock_key.get('region')}`"
                                            if configured else ":orange[not configured]"))
        if c2.button("Edit", key="bedrock_edit"):
            bedrock_dialog(bedrock_key)

    llms = guarded(load_llms, "Failed to load LLMs: ") or {}
    a, b, c, _ = st.columns([1, 1, 1, 3])
    if a.button("Add LLM", icon=":material/add:", type="primary"):
        llm_dialog(None)
    if b.button("Discover", icon=":material/travel_explore:"):
        ss.discovered = []
        discover_dialog((bedrock_key or {}).get("region"))
    selected = [n for n in llms if ss.get(f"sel_{n}")]
    if c.button(f"Delete {len(selected)}", disabled=not selected, icon=":material/delete:"):
        try:
            api.bulk_delete_llms(selected)
            load_llms.clear()
            for n in selected:
                ss.pop(f"sel_{n}", None)
            flash(f"Deleted {len(selected)} models", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to delete models: {exc}")
    if not llms:
        st.info("No models configured yet.")
    for n, cfg in llms.items():
        with st.container(border=True):
            c0, c1, c2, c3, c4 = st.columns([0.4, 6, 1, 1, 1], vertical_alignment="center")
            c0.checkbox("Select", key=f"sel_{n}", label_visibility="collapsed")
            c1.markdown(f"**{cfg.get('icon') or '🧠'} {n}**")
            c1.caption(f"{cfg.get('provider') or 'AWS Bedrock'} · `{cfg.get('model') or ''}`"
                       + (f" · temp {cfg['temperature']}" if cfg.get("temperature") is not None else ""))
            if ss.llm_status.get(n):
                c1.markdown(_status_line(ss.llm_status[n]))
            if c2.button("Test", key=f"tl_{n}"):
                with st.spinner("Testing…"):
                    try:
                        r = api.test_llm(n)
                        ss.llm_status[n] = (bool(r.get("success")), r.get("message") if r.get("success") else r.get("error"))
                    except ApiError as exc:
                        ss.llm_status[n] = (False, str(exc))
                st.rerun()
            if c3.button("Edit", key=f"el_{n}"):
                llm_dialog(n)
            if c4.button("Delete", key=f"dl_{n}"):
                try:
                    api.delete_llm(n)
                    load_llms.clear()
                    flash(f"Deleted LLM: {n}", "success")
                    st.rerun()
                except ApiError as exc:
                    st.error(f"Failed to delete LLM: {exc}")

# ── Certificates ──────────────────────────────────────────────────────────
with tab_certs:
    st.caption("Trusted CAs for secure database and service connections.")
    up = st.file_uploader("Upload certificate", type=["pem", "crt", "cer", "ca-bundle"],
                          key=f"cert_up_{ss.get('cert_up_n', 0)}")
    if up is not None:
        try:
            res = api.upload_certificate(up.name, up.getvalue())
            flash((res or {}).get("message") or "Certificate uploaded", "success")
        except ApiError as exc:
            flash(f"Upload failed: {exc}", "error")
        ss.cert_up_n = ss.get("cert_up_n", 0) + 1  # reset the uploader
        st.rerun()
    certs = guarded(api.certificates, "Failed to load certificates: ") or []
    if not certs:
        st.info("No certificates uploaded.")
    for cert in certs:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        c1.markdown(f"🛡️ `{cert}`")
        if c2.button("Delete", key=f"cert_{cert}"):
            try:
                api.delete_certificate(cert)
                flash(f"Deleted certificate: {cert}", "success")
                st.rerun()
            except ApiError as exc:
                st.error(f"Delete failed: {exc}")

# ── Feature flags ─────────────────────────────────────────────────────────
with tab_flags:
    st.caption("Turn optional agent capabilities on or off. Changes apply on the next run — no restart.")
    data = guarded(api.feature_flags, "Failed to load feature flags: ") or {}
    flags = data.get("flags") or []
    groups: dict = {}
    for f in flags:
        groups.setdefault(f.get("group") or "General", []).append(f)

    def _commit(key: str, value) -> None:
        try:
            api.update_feature_flags({key: value})
            flash("Feature flag updated", "success")
        except ApiError as exc:
            flash(f"Failed to update flag: {exc}", "error")

    for group, items in groups.items():
        st.subheader(group)
        for f in items:
            key, ftype, value, default = f.get("key"), f.get("type"), f.get("value"), f.get("default")
            modified = str(value) != str(default)
            label = (f.get("label") or key) + (" • modified" if modified else "")
            c1, c2 = st.columns([4, 2], vertical_alignment="center")
            c1.markdown(f"**{label}**")
            if f.get("help"):
                c1.caption(f["help"])
            if ftype == "bool":
                new = c2.toggle("on", value=bool(value), key=f"ff_{key}", label_visibility="collapsed")
                if new != bool(value):
                    _commit(key, new)
                    st.rerun()
            else:
                draft = c2.text_input("value", value=str(value if value is not None else ""), key=f"ff_{key}",
                                      label_visibility="collapsed")
                if draft != str(value if value is not None else ""):
                    try:
                        parsed = int(draft) if ftype == "int" else float(draft) if ftype == "float" else draft
                    except ValueError:
                        st.error(f"{key}: expected {ftype}")
                    else:
                        _commit(key, parsed)
                        st.rerun()

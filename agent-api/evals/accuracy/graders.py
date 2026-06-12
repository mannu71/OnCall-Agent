"""Deterministic graders — pure functions, no LLM, no app imports.

Each grader returns a float score in [0, 1] plus a short diagnostic string so
the report can explain every miss. These are the checks the headline accuracy
number is built from; they must be exact and reproducible.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm_path(p: str) -> str:
    """Normalise a path for suffix comparison: forward slashes, lowercased."""
    return (p or "").replace("\\", "/").strip().lower()


def path_matches(returned: str, expected_rel: str) -> bool:
    """True when *returned* (abs or repo-relative) ends with *expected_rel*.

    The crawler may return an absolute path, a repo-relative path, or a path
    with a leading repo-name segment. Suffix matching on normalised separators
    handles all three without false positives for distinct files.
    """
    r, e = _norm_path(returned), _norm_path(expected_rel)
    if not r or not e:
        return False
    return r == e or r.endswith("/" + e)


# ─────────────────────────────────────────────────────────────────────────────
# CodeCrawler: find_symbol
# ─────────────────────────────────────────────────────────────────────────────

def grade_find(result: Dict[str, Any], expected: Dict[str, Any]) -> Tuple[float, str]:
    """Score a crawler_find_symbol result against authored ground truth.

    Scoring (top-1):
      1.0  file + line + kind all correct
      0.7  file + line correct, kind wrong/missing
      0.5  file correct, line wrong
      0.0  file wrong or symbol not found

    Returns (score, diagnostic).
    """
    if result.get("error"):
        return 0.0, f"error: {result['error']}"
    results = result.get("results") or []
    if not result.get("found") or not results:
        return 0.0, "not found (expected file=%s line=%s)" % (
            expected.get("file"), expected.get("line"))

    top = results[0]
    exp_file = expected["file"]
    exp_line = int(expected["line"])
    exp_kind = (expected.get("kind") or "").lower()

    if not path_matches(str(top.get("file", "")), exp_file):
        got_files = [r.get("file") for r in results[:3]]
        return 0.0, f"wrong file: got {got_files}, expected {exp_file!r}"

    got_line = top.get("line")
    line_ok = got_line is not None and int(got_line) == exp_line
    if not line_ok:
        return 0.5, f"right file, wrong line: got {got_line}, expected {exp_line}"

    got_kind = (top.get("kind") or "").lower()
    kind_ok = (not exp_kind) or got_kind == exp_kind or _kind_alias(got_kind, exp_kind)
    if not kind_ok:
        return 0.7, f"file+line ok, kind mismatch: got {got_kind!r}, expected {exp_kind!r}"

    return 1.0, "exact match"


def _kind_alias(got: str, exp: str) -> bool:
    """Treat method/function as interchangeable (a method *is* a function def)."""
    fnlike = {"function", "method", "func", "def"}
    if got in fnlike and exp in fnlike:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# CodeCrawler: trace_path  (edge-set F1)
# ─────────────────────────────────────────────────────────────────────────────

def _edge_key(frm: str, to: str) -> Tuple[str, str]:
    return (str(frm).split(".")[-1].lower(), str(to).split(".")[-1].lower())


def grade_trace(result: Dict[str, Any], expected_edges: List[Dict[str, str]]) -> Tuple[float, str]:
    """Score crawler_trace_path edges as F1 against an authored edge set.

    Edges are compared on (from_leaf, to_leaf) symbol names (module/class
    qualifiers stripped) so the crawler's qualification style doesn't matter.
    F1 = 1.0 means the returned edge set exactly equals ground truth.

    Scope: this metric measures the **intra-repo (resolved) call graph**, which
    is exactly what the AST-derived ground truth represents. The crawler also
    emits ``confidence='external'`` edges for calls into stdlib / third-party
    symbols (e.g. ``str.split``); those are legitimate output but lie outside an
    intra-repo call-graph ground truth, so they are excluded from scoring rather
    than counted as false positives.
    """
    if result.get("error"):
        return 0.0, f"error: {result['error']}"
    edges = [e for e in (result.get("edges") or [])
             if str(e.get("confidence", "")).lower() != "external"]
    got = {_edge_key(e.get("from", ""), e.get("to", "")) for e in edges}
    want = {_edge_key(e["from"], e["to"]) for e in expected_edges}
    if not want:
        return (1.0, "no edges expected and none required") if not got else (0.0, "expected none")
    tp = len(got & want)
    prec = tp / len(got) if got else 0.0
    rec = tp / len(want)
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    missing = want - got
    extra = got - want
    diag = "F1=%.2f (p=%.2f r=%.2f)" % (f1, prec, rec)
    if missing:
        diag += f" missing={sorted(missing)}"
    if extra:
        diag += f" extra={sorted(extra)}"
    return f1, diag


# ─────────────────────────────────────────────────────────────────────────────
# CodeCrawler: get_body
# ─────────────────────────────────────────────────────────────────────────────

def grade_body(result: Dict[str, Any], expected: Dict[str, Any]) -> Tuple[float, str]:
    """1.0 when the returned line span contains the true definition line."""
    if result.get("error"):
        return 0.0, f"error: {result['error']}"
    start = result.get("handle_line_start")
    end = result.get("handle_line_end")
    exp_line = int(expected["line"])
    if start is None or end is None:
        # fall back to inspecting the numbered content map
        content = result.get("content") or {}
        keys = [int(k) for k in content.keys() if str(k).isdigit()]
        if keys and min(keys) <= exp_line <= max(keys):
            return 1.0, "def line within returned content"
        return 0.0, "no line span returned"
    if int(start) <= exp_line <= int(end):
        return 1.0, f"def line {exp_line} within [{start},{end}]"
    return 0.0, f"def line {exp_line} outside [{start},{end}]"


# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch: schema validity
# ─────────────────────────────────────────────────────────────────────────────

_SEVERITY = {"critical", "high", "medium", "low", "none"}
_CONFIDENCE = {"high", "medium", "low"}
_REQUIRED = ["headline", "severity", "key_findings", "primary_hypothesis",
             "recommended_actions", "confidence"]


def grade_schema(structured: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """1.0 when the structured analysis has all required fields + valid enums."""
    if not structured:
        return 0.0, "no structured_analysis (synthesis fell back to free-form)"
    missing = [f for f in _REQUIRED if f not in structured or structured.get(f) in (None, "", [])]
    if missing:
        return 0.0, f"missing/empty fields: {missing}"
    sev = str(structured.get("severity", "")).lower()
    conf = str(structured.get("confidence", "")).lower()
    if sev not in _SEVERITY:
        return 0.0, f"invalid severity {sev!r}"
    if conf not in _CONFIDENCE:
        return 0.0, f"invalid confidence {conf!r}"
    if not isinstance(structured.get("key_findings"), list) or not structured["key_findings"]:
        return 0.0, "key_findings not a non-empty list"
    return 1.0, "schema valid"


# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch: ID-grounding  (hallucination detector)
# ─────────────────────────────────────────────────────────────────────────────

# Tokens that look like identifiers an engineer would grep for: UUIDs, AWS
# request ids, trace ids, hex blobs, etc.
_ID_PATTERNS = [
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",  # uuid
    r"\b[0-9a-f]{16,}\b",                       # long hex (trace/span ids)
    r"\breq-[0-9a-zA-Z]{6,}\b",                 # req-xxxx
    r"\b1-[0-9a-f]{8}-[0-9a-f]{24}\b",          # aws xray trace id
]
_ID_RE = re.compile("|".join(_ID_PATTERNS))

# common English/hex false positives we never count as IDs
_STOPWORDS = {"deadbeef", "facade", "decade", "beaded", "defaced"}


def extract_ids(text: str) -> List[str]:
    """Pull ID-like tokens out of narrative text for grounding checks."""
    out: List[str] = []
    for m in _ID_RE.findall(text or ""):
        tok = m if isinstance(m, str) else next((g for g in m if g), "")
        if tok and tok.lower() not in _STOPWORDS:
            out.append(tok)
    return out


def grade_id_grounding(narrative: str, evidence_blob: str) -> Tuple[float, str]:
    """Fraction of ID-like tokens in the narrative that appear in the evidence.

    1.0 = every ID the model cited is present in the evidence bundle (no
    fabrication). Score = grounded/total. If the narrative cites no IDs,
    grounding is vacuously perfect (1.0).
    """
    ids = extract_ids(narrative)
    if not ids:
        return 1.0, "no IDs cited"
    ev = (evidence_blob or "").lower()
    grounded = [i for i in ids if i.lower() in ev]
    hallucinated = [i for i in ids if i.lower() not in ev]
    score = len(grounded) / len(ids)
    if hallucinated:
        return score, f"hallucinated {len(hallucinated)}/{len(ids)}: {hallucinated[:5]}"
    return 1.0, f"all {len(ids)} IDs grounded"


# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch: severity match
# ─────────────────────────────────────────────────────────────────────────────

_SEV_ORDER = ["none", "low", "medium", "high", "critical"]


def grade_severity(structured: Optional[Dict[str, Any]], expected_sev: str) -> Tuple[float, str]:
    """1.0 exact severity, 0.5 adjacent (±1 rank), 0.0 otherwise."""
    if not structured:
        return 0.0, "no structured output"
    got = str(structured.get("severity", "")).lower()
    exp = str(expected_sev).lower()
    if got == exp:
        return 1.0, f"severity exact ({got})"
    if got in _SEV_ORDER and exp in _SEV_ORDER:
        if abs(_SEV_ORDER.index(got) - _SEV_ORDER.index(exp)) == 1:
            return 0.5, f"severity adjacent: got {got}, expected {exp}"
    return 0.0, f"severity mismatch: got {got}, expected {exp}"


# ─────────────────────────────────────────────────────────────────────────────
# Agent trajectory: tool-selection / protocol / final-answer predicates
# ─────────────────────────────────────────────────────────────────────────────
#
# These grade what the *real* ReAct agent did, not a tool in isolation. Because
# the agent LLM is non-deterministic, every check is a PREDICATE over a SET (did
# the right family get called? did read precede a citation?) — never an exact
# call sequence. Each grader scores satisfied/total predicates so a near-miss
# degrades gracefully instead of collapsing to 0.
#
# Canonical call input is a list of TURNS, one per assistant message, each turn a
# list of tool names invoked in that message (a single message can fan out
# parallel tool_calls). A plain flat list of names is also accepted and treated
# as one-call-per-turn. ``first_any`` reads the FIRST turn as an any-of set so a
# parallel first move isn't penalised for ordering within the turn.

# A tool-name matcher: an fnmatch glob, with '|' meaning alternation, so
# "crawler_find_symbol|crawler_investigate_alert" matches either.
Pattern = str
# A turn is the set of tool names in one assistant message; the trajectory is the
# ordered list of turns. ``Sequence[str]`` (a flat name list) is also accepted.
Turns = Union[Sequence[str], Sequence[Sequence[str]]]


def _name_matches(name: str, pattern: Pattern) -> bool:
    """True when *name* matches *pattern* (fnmatch glob, '|' = alternation)."""
    name = name or ""
    return any(fnmatch.fnmatch(name, alt.strip())
               for alt in str(pattern).split("|") if alt.strip())


def _as_turns(calls: Turns) -> List[List[str]]:
    """Normalise input to a list-of-turns. A flat name list → one call per turn."""
    turns: List[List[str]] = []
    for item in (calls or []):
        if isinstance(item, str):
            turns.append([item])
        elif isinstance(item, (list, tuple)):
            turns.append([str(n) for n in item])
        else:
            turns.append([str(item)])
    return turns


def _flatten(turns: List[List[str]]) -> List[str]:
    return [n for turn in turns for n in turn]


def _score(predicates: List[Tuple[bool, str]]) -> Tuple[float, str]:
    """Fold a list of (passed, label) into (satisfied/total, diagnostic)."""
    if not predicates:
        return 1.0, "no checks"
    passed = sum(1 for ok, _ in predicates if ok)
    fails = [label for ok, label in predicates if not ok]
    score = passed / len(predicates)
    diag = "all %d checks passed" % len(predicates) if not fails \
        else "%d/%d failed: %s" % (len(fails), len(predicates), "; ".join(fails))
    return score, diag


def grade_tool_selection(calls: Turns, spec: Dict[str, Any]) -> Tuple[float, str]:
    """Did the agent reach for the right tool family for this kind of question?

    Spec keys (all optional):
      * ``first_any``: list of patterns — the FIRST turn must invoke at least one
        tool matching one of these (the right opening move).
      * ``must_call``: list of patterns — each must be invoked at least once.
      * ``must_not_call``: list of patterns — none may be invoked.
    """
    turns = _as_turns(calls)
    flat = _flatten(turns)
    predicates: List[Tuple[bool, str]] = []

    first_any = spec.get("first_any") or []
    if first_any:
        first_turn = turns[0] if turns else []
        ok = any(_name_matches(n, p) for n in first_turn for p in first_any)
        predicates.append((ok, "first_any %s (first turn was %s)" % (first_any, first_turn or "∅")))

    for p in (spec.get("must_call") or []):
        ok = any(_name_matches(n, p) for n in flat)
        predicates.append((ok, "must_call %r" % p))

    for p in (spec.get("must_not_call") or []):
        hit = [n for n in flat if _name_matches(n, p)]
        predicates.append((not hit, "must_not_call %r (called %s)" % (p, hit)))

    return _score(predicates)


def grade_protocol(calls: Turns, spec: Dict[str, Any]) -> Tuple[float, str]:
    """Did the agent follow the investigation protocol (read before citing, etc.)?

    Spec keys (all optional):
      * ``requires_before``: list of ``[before_pat, after_pat]`` — if any call
        matches ``after_pat`` then some EARLIER call must match ``before_pat``.
        Vacuously satisfied when ``after_pat`` never appears (the gated action
        was simply not taken).
      * ``forbidden``: list of patterns — none may be invoked (e.g. re-running the
        full scan when a pre-computed block was already supplied).
      * ``max_calls``: int — total tool calls must not exceed this (thrash cap).
    """
    turns = _as_turns(calls)
    flat = _flatten(turns)
    predicates: List[Tuple[bool, str]] = []

    for pair in (spec.get("requires_before") or []):
        before_pat, after_pat = pair[0], pair[1]
        after_idx = next((i for i, n in enumerate(flat) if _name_matches(n, after_pat)), None)
        if after_idx is None:
            ok = True  # vacuous: the gated action was never taken
        else:
            ok = any(_name_matches(flat[j], before_pat) for j in range(after_idx))
        predicates.append((ok, "requires %r before %r" % (before_pat, after_pat)))

    for p in (spec.get("forbidden") or []):
        hit = [n for n in flat if _name_matches(n, p)]
        predicates.append((not hit, "forbidden %r (called %s)" % (p, hit)))

    if "max_calls" in spec:
        cap = int(spec["max_calls"])
        predicates.append((len(flat) <= cap, "max_calls %d (made %d)" % (cap, len(flat))))

    return _score(predicates)


def _contains(haystack: str, needle: str) -> bool:
    """Case-insensitive, separator-normalised substring test (handles file paths)."""
    h = (haystack or "").replace("\\", "/").lower()
    n = (needle or "").replace("\\", "/").lower()
    return bool(n) and n in h


def grade_final_answer(answer: str, spec: Dict[str, Any]) -> Tuple[float, str]:
    """Did the final narrative state the right thing (deterministic substring checks)?

    Spec keys (all optional):
      * ``must_contain``: list of substrings — each must appear (file paths,
        symbols, line numbers, severity words). Separator/case-insensitive.
      * ``any_of``: list of groups — each group satisfied by >=1 of its members.
      * ``must_not_contain``: list of substrings — none may appear (e.g. forbid
        "critical" on a clean-logs case).
    """
    answer = answer or ""
    predicates: List[Tuple[bool, str]] = []

    for sub in (spec.get("must_contain") or []):
        predicates.append((_contains(answer, sub), "must_contain %r" % sub))

    for group in (spec.get("any_of") or []):
        ok = any(_contains(answer, alt) for alt in group)
        predicates.append((ok, "any_of %s" % (group,)))

    for sub in (spec.get("must_not_contain") or []):
        predicates.append((not _contains(answer, sub), "must_not_contain %r" % sub))

    return _score(predicates)

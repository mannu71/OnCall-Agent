"""Tree-sitter dispatcher and per-language extractors for the knowledge graph.

Public entry point::

    nodes, edges = parse_file("src/Services/Foo.cs", source_text)

* ``nodes`` — list of ``NodeRecord`` (one per class / function / method / ...).
* ``edges`` — list of ``EdgeRecord`` (one per call / import / inherit / ...).

Design notes
------------
* Qualified-name scheme follows ``tirth8205/code-review-graph``:
  ``<file_path>::<ParentClass>::<Member>``  — ``::`` is reserved separator.
* Languages: C# (primary, compliance-api), Python, TypeScript, JavaScript,
  Java, Kotlin, Go, Rust, Ruby.  Each gets a per-language extractor
  function selected by file extension.
* No LLM calls.  Pure deterministic AST walk.
* Parser instances are cached per language (tree-sitter parsers are
  thread-safe enough for our read-only use).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────
# Record types
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class NodeRecord:
    """One row destined for ``kg_nodes``."""

    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int                 # 1-indexed (Postgres-friendly)
    line_end: int
    col_start: int = 0
    col_end: int = 0
    language: str = ""
    parent_name: Optional[str] = None
    signature: Optional[str] = None
    docstring: Optional[str] = None
    exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False
    is_test: bool = False
    decorators: List[str] = field(default_factory=list)
    type_parameters: List[str] = field(default_factory=list)


@dataclass
class EdgeRecord:
    """One row destined for ``kg_edges``."""

    kind: str
    source_qname: str
    target_qname: str               # may be bare name (unresolved); resolver promotes later
    file_path: str
    line: int                       # 1-indexed
    col: int = 0
    confidence: str = "extracted"   # 'extracted' | 'resolved' | 'external'


# ───────────────────────────────────────────────────────────────────────────
# Language detection
# ───────────────────────────────────────────────────────────────────────────

_EXT_TO_LANG: Dict[str, str] = {
    ".cs":    "csharp",
    ".py":    "python",
    ".ts":    "typescript",
    ".tsx":   "typescript",
    ".js":    "javascript",
    ".jsx":   "javascript",
    ".mjs":   "javascript",
    ".cjs":   "javascript",
    ".java":  "java",
    ".kt":    "kotlin",
    ".kts":   "kotlin",
    ".go":    "go",
    ".rs":    "rust",
    ".rb":    "ruby",
}

SUPPORTED_LANGUAGES = tuple(sorted(set(_EXT_TO_LANG.values())))


def detect_language(file_path: str) -> Optional[str]:
    """Return the language key for ``file_path`` or ``None`` if unsupported."""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_TO_LANG.get(ext)


# ───────────────────────────────────────────────────────────────────────────
# Parser cache
# ───────────────────────────────────────────────────────────────────────────

_PARSER_CACHE: Dict[str, object] = {}
_PARSER_LOCK = Lock()

_LANG_LOADERS: Dict[str, Callable[[], object]] = {
    "csharp":     lambda: _build_parser("tree_sitter_c_sharp"),
    "python":     lambda: _build_parser("tree_sitter_python"),
    "typescript": lambda: _build_parser("tree_sitter_typescript", attr="language_typescript"),
    "javascript": lambda: _build_parser("tree_sitter_javascript"),
    "java":       lambda: _build_parser("tree_sitter_java"),
    "kotlin":     lambda: _build_parser("tree_sitter_kotlin"),
    "go":         lambda: _build_parser("tree_sitter_go"),
    "rust":       lambda: _build_parser("tree_sitter_rust"),
    "ruby":       lambda: _build_parser("tree_sitter_ruby"),
}


def _build_parser(module_name: str, attr: str = "language") -> object:
    """Import a tree-sitter language module and return a configured Parser."""
    import importlib
    from tree_sitter import Language, Parser  # type: ignore[import]

    mod = importlib.import_module(module_name)
    lang_fn = getattr(mod, attr, None) or getattr(mod, "language")
    language = Language(lang_fn())
    parser = Parser(language)
    return parser


def _get_parser(lang: str):
    """Return a cached parser for *lang* (None if the language module is missing)."""
    with _PARSER_LOCK:
        if lang in _PARSER_CACHE:
            return _PARSER_CACHE[lang]
        loader = _LANG_LOADERS.get(lang)
        if loader is None:
            return None
        try:
            parser = loader()
        except ImportError as exc:
            logger.warning("kg.parser: tree-sitter module for '%s' not installed: %s", lang, exc)
            _PARSER_CACHE[lang] = None  # type: ignore[assignment]
            return None
        _PARSER_CACHE[lang] = parser
        return parser


# ───────────────────────────────────────────────────────────────────────────
# Generic helpers
# ───────────────────────────────────────────────────────────────────────────


def _text(node, source_bytes: bytes) -> str:
    """Return UTF-8 decoded source span for *node*."""
    if node is None:
        return ""
    try:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""


def _line(node) -> int:
    """1-indexed start line (tree-sitter returns 0-indexed rows)."""
    return node.start_point[0] + 1


def _line_end(node) -> int:
    return node.end_point[0] + 1


def _col(node) -> int:
    return node.start_point[1]


def _col_end(node) -> int:
    return node.end_point[1]


def _qname(file_path: str, parts: List[str]) -> str:
    """Build a qualified name: ``<file>::<part1>::<part2>::...``."""
    if not parts:
        return file_path
    return f"{file_path}::" + "::".join(p for p in parts if p)


_TEST_NAME_RE = re.compile(r"^(test_.*|.*_test|.*test|.*spec|.*Spec)$", re.IGNORECASE)
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|\.tests?\.|\.spec\.", re.IGNORECASE)
_CS_TEST_ATTRS = ("[Test]", "[Fact]", "[Theory]", "[TestMethod]", "[TestCase]")


def _looks_like_test(file_path: str, name: str, decorators: List[str]) -> bool:
    if _TEST_PATH_RE.search(file_path):
        return True
    for d in decorators:
        if any(t in d for t in _CS_TEST_ATTRS):
            return True
        d_l = d.lower()
        if "pytest" in d_l or "unittest" in d_l:
            return True
    if name and _TEST_NAME_RE.match(name):
        return True
    return False


def _find_child_named(node, *types: str):
    """Return first direct child whose ``type`` is in *types* (or None)."""
    for c in node.children:
        if c.type in types:
            return c
    return None


def _find_field(node, field_name: str):
    """Safe wrapper around tree-sitter's child_by_field_name."""
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


# ───────────────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────────────


def parse_file(file_path: str, source: str) -> Tuple[List[NodeRecord], List[EdgeRecord]]:
    """Parse a source file and return (nodes, edges).

    Returns empty lists when:
      * extension is unsupported
      * tree-sitter language module is unavailable
      * the parser raises (caller logs and continues)
    """
    lang = detect_language(file_path)
    if lang is None:
        return [], []

    parser = _get_parser(lang)
    if parser is None:
        return [], []

    extractor = _EXTRACTORS.get(lang)
    if extractor is None:
        return [], []

    try:
        source_bytes = source.encode("utf-8", errors="replace")
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kg.parser: parse failed for %s: %s", file_path, exc)
        return [], []

    try:
        return extractor(tree.root_node, source_bytes, file_path, lang)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kg.parser: extractor failed for %s: %s", file_path, exc)
        return [], []


# ───────────────────────────────────────────────────────────────────────────
# C# extractor (primary — compliance-api)
# ───────────────────────────────────────────────────────────────────────────


def _extract_csharp(root, source_bytes: bytes, file_path: str, lang: str
                    ) -> Tuple[List[NodeRecord], List[EdgeRecord]]:
    nodes: List[NodeRecord] = []
    edges: List[EdgeRecord] = []

    def visit(node, parent_qname: Optional[str], parent_parts: List[str]):
        t = node.type

        if t == "using_directive":
            target = _text(node, source_bytes).strip().rstrip(";")
            target = re.sub(r"^using\s+(static\s+)?", "", target).strip()
            edges.append(EdgeRecord(
                kind="imports_from",
                source_qname=file_path,
                target_qname=target,
                file_path=file_path,
                line=_line(node),
                col=_col(node),
            ))

        elif t in ("class_declaration", "interface_declaration", "struct_declaration",
                   "enum_declaration", "record_declaration"):
            name_node = _find_field(node, "name") or _find_child_named(node, "identifier")
            name = _text(name_node, source_bytes) if name_node else "<anonymous>"
            kind_map = {
                "class_declaration":     "class",
                "interface_declaration": "interface",
                "struct_declaration":    "class",
                "enum_declaration":      "enum",
                "record_declaration":    "class",
            }
            kind = kind_map[t]
            qname = _qname(file_path, parent_parts + [name])
            modifiers = _csharp_modifiers(node, source_bytes)
            decorators = _csharp_attributes(node, source_bytes)

            nodes.append(NodeRecord(
                kind=kind,
                name=name,
                qualified_name=qname,
                file_path=file_path,
                line_start=_line(node),
                line_end=_line_end(node),
                col_start=_col(node),
                col_end=_col_end(node),
                language=lang,
                parent_name=parent_qname,
                signature=_csharp_header(node, source_bytes),
                exported="public" in modifiers,
                is_static="static" in modifiers,
                is_abstract="abstract" in modifiers,
                is_test=_looks_like_test(file_path, name, decorators),
                decorators=decorators,
            ))
            if parent_qname:
                edges.append(EdgeRecord(
                    kind="contains",
                    source_qname=parent_qname,
                    target_qname=qname,
                    file_path=file_path,
                    line=_line(node),
                ))

            base_list = _find_child_named(node, "base_list")
            if base_list is not None:
                for base in base_list.named_children:
                    base_text = _text(base, source_bytes).strip()
                    if not base_text:
                        continue
                    edge_kind = "implements" if (kind == "class" and re.match(r"^I[A-Z]", base_text)) else "inherits"
                    edges.append(EdgeRecord(
                        kind=edge_kind,
                        source_qname=qname,
                        target_qname=base_text,
                        file_path=file_path,
                        line=_line(base),
                    ))

            for child in node.children:
                visit(child, qname, parent_parts + [name])
            return

        elif t in ("method_declaration", "constructor_declaration",
                   "property_declaration", "destructor_declaration",
                   "operator_declaration"):
            name_node = _find_field(node, "name") or _find_child_named(node, "identifier")
            name = _text(name_node, source_bytes) if name_node else "<anonymous>"
            qname = _qname(file_path, parent_parts + [name])
            modifiers = _csharp_modifiers(node, source_bytes)
            decorators = _csharp_attributes(node, source_bytes)

            nodes.append(NodeRecord(
                kind="method",
                name=name,
                qualified_name=qname,
                file_path=file_path,
                line_start=_line(node),
                line_end=_line_end(node),
                col_start=_col(node),
                col_end=_col_end(node),
                language=lang,
                parent_name=parent_qname,
                signature=_csharp_header(node, source_bytes),
                exported="public" in modifiers,
                is_async="async" in modifiers,
                is_static="static" in modifiers,
                is_abstract="abstract" in modifiers,
                is_test=_looks_like_test(file_path, name, decorators),
                decorators=decorators,
            ))
            if parent_qname:
                edges.append(EdgeRecord(
                    kind="contains",
                    source_qname=parent_qname,
                    target_qname=qname,
                    file_path=file_path,
                    line=_line(node),
                ))
            for child in node.children:
                visit(child, qname, parent_parts + [name])
            return

        elif t == "invocation_expression":
            fn = _find_field(node, "function")
            callee: Optional[str] = None
            if fn is not None:
                if fn.type == "identifier":
                    callee = _text(fn, source_bytes)
                elif fn.type in ("member_access_expression", "generic_name"):
                    last_ident = None
                    for c in fn.children:
                        if c.type == "identifier":
                            last_ident = c
                    if last_ident is not None:
                        callee = _text(last_ident, source_bytes)
                    else:
                        callee = _text(fn, source_bytes).split(".")[-1]
            if callee and parent_qname:
                edges.append(EdgeRecord(
                    kind="calls",
                    source_qname=parent_qname,
                    target_qname=callee,
                    file_path=file_path,
                    line=_line(node),
                    col=_col(node),
                ))

        for child in node.children:
            visit(child, parent_qname, parent_parts)

    visit(root, None, [])
    return nodes, edges


def _csharp_modifiers(node, source_bytes: bytes) -> List[str]:
    return [_text(c, source_bytes).strip() for c in node.children if c.type == "modifier"]


def _csharp_attributes(node, source_bytes: bytes) -> List[str]:
    return [_text(c, source_bytes).strip() for c in node.children if c.type == "attribute_list"]


def _csharp_header(node, source_bytes: bytes) -> str:
    text = _text(node, source_bytes)
    brace = text.find("{")
    if brace > 0:
        return text[:brace].strip()
    semi = text.find(";")
    if semi > 0:
        return text[:semi].strip()
    return text.strip()[:200]


# ───────────────────────────────────────────────────────────────────────────
# Python extractor
# ───────────────────────────────────────────────────────────────────────────


def _extract_python(root, source_bytes: bytes, file_path: str, lang: str
                    ) -> Tuple[List[NodeRecord], List[EdgeRecord]]:
    nodes: List[NodeRecord] = []
    edges: List[EdgeRecord] = []

    def visit(node, parent_qname: Optional[str], parent_parts: List[str], in_class: bool):
        t = node.type

        if t == "import_statement":
            for c in node.children:
                if c.type == "dotted_name":
                    edges.append(EdgeRecord(
                        kind="imports_from",
                        source_qname=file_path,
                        target_qname=_text(c, source_bytes),
                        file_path=file_path,
                        line=_line(node),
                    ))
        elif t == "import_from_statement":
            module_node = _find_field(node, "module_name")
            module = _text(module_node, source_bytes) if module_node else ""
            for c in node.children:
                if c.type in ("dotted_name", "aliased_import"):
                    name = _text(c, source_bytes)
                    target = f"{module}.{name}" if module else name
                    edges.append(EdgeRecord(
                        kind="imports_from",
                        source_qname=file_path,
                        target_qname=target,
                        file_path=file_path,
                        line=_line(node),
                    ))

        elif t == "class_definition":
            name_node = _find_field(node, "name")
            name = _text(name_node, source_bytes) if name_node else "<anonymous>"
            qname = _qname(file_path, parent_parts + [name])
            decorators = _python_decorators(node, source_bytes)
            docstring = _python_docstring(node, source_bytes)

            nodes.append(NodeRecord(
                kind="class",
                name=name,
                qualified_name=qname,
                file_path=file_path,
                line_start=_line(node),
                line_end=_line_end(node),
                col_start=_col(node),
                col_end=_col_end(node),
                language=lang,
                parent_name=parent_qname,
                signature=_python_class_header(node, source_bytes),
                docstring=docstring,
                exported=not name.startswith("_"),
                is_test=_looks_like_test(file_path, name, decorators),
                decorators=decorators,
            ))
            if parent_qname:
                edges.append(EdgeRecord(
                    kind="contains",
                    source_qname=parent_qname,
                    target_qname=qname,
                    file_path=file_path,
                    line=_line(node),
                ))
            superclasses = _find_field(node, "superclasses")
            if superclasses is not None:
                for base in superclasses.named_children:
                    base_text = _text(base, source_bytes).strip().rstrip(",")
                    if base_text:
                        edges.append(EdgeRecord(
                            kind="inherits",
                            source_qname=qname,
                            target_qname=base_text,
                            file_path=file_path,
                            line=_line(base),
                        ))
            for child in node.children:
                visit(child, qname, parent_parts + [name], in_class=True)
            return

        elif t == "function_definition":
            name_node = _find_field(node, "name")
            name = _text(name_node, source_bytes) if name_node else "<anonymous>"
            qname = _qname(file_path, parent_parts + [name])
            decorators = _python_decorators(node, source_bytes)
            docstring = _python_docstring(node, source_bytes)
            is_async = any(c.type == "async" for c in node.children)
            kind = "method" if in_class else "function"

            nodes.append(NodeRecord(
                kind=kind,
                name=name,
                qualified_name=qname,
                file_path=file_path,
                line_start=_line(node),
                line_end=_line_end(node),
                col_start=_col(node),
                col_end=_col_end(node),
                language=lang,
                parent_name=parent_qname,
                signature=_python_function_header(node, source_bytes),
                docstring=docstring,
                exported=not name.startswith("_"),
                is_async=is_async,
                is_static=any("staticmethod" in d for d in decorators),
                is_abstract=any("abstractmethod" in d for d in decorators),
                is_test=_looks_like_test(file_path, name, decorators),
                decorators=decorators,
            ))
            if parent_qname:
                edges.append(EdgeRecord(
                    kind="contains",
                    source_qname=parent_qname,
                    target_qname=qname,
                    file_path=file_path,
                    line=_line(node),
                ))
            for child in node.children:
                visit(child, qname, parent_parts + [name], in_class=False)
            return

        elif t == "call":
            fn = _find_field(node, "function")
            callee: Optional[str] = None
            if fn is not None:
                if fn.type == "identifier":
                    callee = _text(fn, source_bytes)
                elif fn.type == "attribute":
                    attr = _find_field(fn, "attribute")
                    if attr is not None:
                        callee = _text(attr, source_bytes)
                    else:
                        callee = _text(fn, source_bytes).split(".")[-1]
            if callee and parent_qname:
                edges.append(EdgeRecord(
                    kind="calls",
                    source_qname=parent_qname,
                    target_qname=callee,
                    file_path=file_path,
                    line=_line(node),
                    col=_col(node),
                ))

        for child in node.children:
            visit(child, parent_qname, parent_parts, in_class)

    visit(root, None, [], in_class=False)
    return nodes, edges


def _python_decorators(node, source_bytes: bytes) -> List[str]:
    decs: List[str] = []
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        for c in parent.children:
            if c.type == "decorator":
                decs.append(_text(c, source_bytes).strip())
    return decs


def _python_docstring(node, source_bytes: bytes) -> Optional[str]:
    body = _find_field(node, "body")
    if body is None:
        return None
    for c in body.named_children:
        if c.type == "expression_statement":
            inner = c.named_children[0] if c.named_children else None
            if inner is not None and inner.type == "string":
                text = _text(inner, source_bytes).strip()
                for q in ('"""', "'''", '"', "'"):
                    if text.startswith(q) and text.endswith(q):
                        return text[len(q):-len(q)].strip()
                return text
        break
    return None


def _python_function_header(node, source_bytes: bytes) -> str:
    text = _text(node, source_bytes)
    colon = text.find(":")
    return (text[:colon].strip() if colon > 0 else text.strip()[:200])


def _python_class_header(node, source_bytes: bytes) -> str:
    text = _text(node, source_bytes)
    colon = text.find(":")
    return (text[:colon].strip() if colon > 0 else text.strip()[:200])


# ───────────────────────────────────────────────────────────────────────────
# Generic extractor (TypeScript, JavaScript, Java, Kotlin, Go, Rust, Ruby)
# ───────────────────────────────────────────────────────────────────────────

# (definition_node_type, kind)
_DEF_TYPES: Dict[str, List[Tuple[str, str]]] = {
    "typescript": [
        ("class_declaration",      "class"),
        ("interface_declaration",  "interface"),
        ("enum_declaration",       "enum"),
        ("function_declaration",   "function"),
        ("method_definition",      "method"),
        ("method_signature",       "method"),
        ("type_alias_declaration", "type_alias"),
    ],
    "javascript": [
        ("class_declaration",    "class"),
        ("function_declaration", "function"),
        ("method_definition",    "method"),
    ],
    "java": [
        ("class_declaration",       "class"),
        ("interface_declaration",   "interface"),
        ("enum_declaration",        "enum"),
        ("method_declaration",      "method"),
        ("constructor_declaration", "method"),
    ],
    "kotlin": [
        ("class_declaration",    "class"),
        ("object_declaration",   "class"),
        ("function_declaration", "function"),
    ],
    "go": [
        ("type_declaration",     "type_alias"),
        ("function_declaration", "function"),
        ("method_declaration",   "method"),
    ],
    "rust": [
        ("struct_item",   "class"),
        ("enum_item",     "enum"),
        ("trait_item",    "trait"),
        ("function_item", "function"),
        ("impl_item",     "class"),
        ("mod_item",      "class"),
        ("type_item",     "type_alias"),
    ],
    "ruby": [
        ("class",            "class"),
        ("module",           "class"),
        ("method",           "method"),
        ("singleton_method", "method"),
    ],
}

_IMPORT_TYPES: Dict[str, Tuple[str, ...]] = {
    "typescript": ("import_statement",),
    "javascript": ("import_statement",),
    "java":       ("import_declaration",),
    "kotlin":     ("import_header",),
    "go":         ("import_declaration", "import_spec"),
    "rust":       ("use_declaration",),
    "ruby":       (),  # require()/require_relative() — surface via 'call' walk
}

_CALL_TYPES: Dict[str, Tuple[str, ...]] = {
    "typescript": ("call_expression", "new_expression"),
    "javascript": ("call_expression", "new_expression"),
    "java":       ("method_invocation", "object_creation_expression"),
    "kotlin":     ("call_expression",),
    "go":         ("call_expression",),
    "rust":       ("call_expression", "macro_invocation"),
    "ruby":       ("call",),
}


def _generic_extractor(language: str) -> Callable:
    """Build an extractor function for a generic language."""
    defs = dict(_DEF_TYPES.get(language, []))
    imports = _IMPORT_TYPES.get(language, ())
    calls = _CALL_TYPES.get(language, ())

    def extract(root, source_bytes: bytes, file_path: str, lang: str
                ) -> Tuple[List[NodeRecord], List[EdgeRecord]]:
        nodes: List[NodeRecord] = []
        edges: List[EdgeRecord] = []

        def visit(node, parent_qname: Optional[str], parent_parts: List[str]):
            t = node.type

            if t in defs:
                kind = defs[t]
                name_node = _find_field(node, "name") or _find_child_named(node, "identifier", "type_identifier")
                name = _text(name_node, source_bytes) if name_node else "<anonymous>"
                qname = _qname(file_path, parent_parts + [name])

                full = _text(node, source_bytes)
                signature = (full.splitlines()[0].strip() if full else "")[:200]

                nodes.append(NodeRecord(
                    kind=kind,
                    name=name,
                    qualified_name=qname,
                    file_path=file_path,
                    line_start=_line(node),
                    line_end=_line_end(node),
                    col_start=_col(node),
                    col_end=_col_end(node),
                    language=lang,
                    parent_name=parent_qname,
                    signature=signature,
                    exported=_likely_exported(language, name, full),
                    is_test=_looks_like_test(file_path, name, []),
                ))
                if parent_qname:
                    edges.append(EdgeRecord(
                        kind="contains",
                        source_qname=parent_qname,
                        target_qname=qname,
                        file_path=file_path,
                        line=_line(node),
                    ))
                for child in node.children:
                    visit(child, qname, parent_parts + [name])
                return

            if imports and t in imports:
                target = _text(node, source_bytes).strip().rstrip(";")
                edges.append(EdgeRecord(
                    kind="imports_from",
                    source_qname=file_path,
                    target_qname=target,
                    file_path=file_path,
                    line=_line(node),
                ))

            if calls and t in calls:
                callee = _generic_callee(node, source_bytes)
                if callee and parent_qname:
                    edges.append(EdgeRecord(
                        kind="calls",
                        source_qname=parent_qname,
                        target_qname=callee,
                        file_path=file_path,
                        line=_line(node),
                        col=_col(node),
                    ))

            for child in node.children:
                visit(child, parent_qname, parent_parts)

        visit(root, None, [])
        return nodes, edges

    return extract


def _likely_exported(language: str, name: str, full_text: str) -> bool:
    """Best-effort exported/public heuristic per language."""
    head = full_text[:200]
    if language in ("typescript", "javascript"):
        return "export" in head
    if language == "java":
        return "public" in head
    if language == "kotlin":
        return "public" in head or ("internal" not in head and "private" not in head)
    if language == "go":
        return bool(name) and name[0:1].isupper()
    if language == "rust":
        return head.lstrip().startswith("pub")
    return True


def _generic_callee(node, source_bytes: bytes) -> Optional[str]:
    """Try to pull a callee name from a call-expression node."""
    fn = _find_field(node, "function")
    if fn is not None:
        if fn.type in ("identifier", "type_identifier", "field_identifier", "scoped_identifier"):
            return _text(fn, source_bytes)
        if fn.type in ("member_expression", "field_expression", "selector_expression", "navigation_expression"):
            ident = None
            for c in fn.children:
                if c.type in ("identifier", "field_identifier", "property_identifier"):
                    ident = c
            return _text(ident, source_bytes) if ident else _text(fn, source_bytes).split(".")[-1]
        return _text(fn, source_bytes).split(".")[-1]
    return None


# ───────────────────────────────────────────────────────────────────────────
# Extractor registry
# ───────────────────────────────────────────────────────────────────────────


_EXTRACTORS: Dict[str, Callable] = {
    "csharp":     _extract_csharp,
    "python":     _extract_python,
    "typescript": _generic_extractor("typescript"),
    "javascript": _generic_extractor("javascript"),
    "java":       _generic_extractor("java"),
    "kotlin":     _generic_extractor("kotlin"),
    "go":         _generic_extractor("go"),
    "rust":       _generic_extractor("rust"),
    "ruby":       _generic_extractor("ruby"),
}

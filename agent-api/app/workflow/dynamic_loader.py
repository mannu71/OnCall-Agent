from __future__ import annotations

import importlib.util
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

from app.workflow.graph_engine.workflow_graph import default_registry

logger = logging.getLogger(__name__)

# Security restrictions - patterns that are forbidden in dynamically compiled agent code
_FORBIDDEN_KEYWORDS = (
    r"\bimport\s+(os|sys|subprocess|socket|shutil|importlib|pty|ctypes|requests|httpx)\b",
    r"\bfrom\s+(os|sys|subprocess|socket|shutil|importlib|pty|ctypes)\b",
    r"(?<!def\s)(?<!\.)\beval\s*\(",
    r"(?<!def\s)(?<!\.)\bexec\s*\(",
    r"\b__import__\b",
    r"\bopen\s*\(",
    r"\bpathlib\b",
)

_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_KEYWORDS), re.IGNORECASE)


class SecurityException(Exception):
    """Raised when dynamically generated code violates safety guardrails."""
    pass


def dynamically_register_node(file_path: str, node_type_name: str) -> None:
    """Load a Python module from disk dynamically and register its GraphNode class.

    Args:
        file_path: Absolute path to the generated Python file.
        node_type_name: Registry key name (snake_case), e.g., 'auth_hex_resolver'.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Dynamic node module not found at: {file_path}")

    # 1. Load module spec
    module_name = f"app.workflow.dynamic_nodes.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to create module spec for dynamic node: {file_path}")

    module = importlib.util.module_from_spec(spec)
    
    # 2. Execute module in local memory
    spec.loader.exec_module(module)

    # 3. Resolve GraphNode class name (e.g. "auth_hex_resolver" -> "AuthHexResolverNode")
    class_name = "".join(part.capitalize() for part in node_type_name.split("_")) + "Node"
    
    node_class = getattr(module, class_name, None)
    if node_class is None:
        raise AttributeError(
            f"Module at {file_path} does not define the expected GraphNode subclass '{class_name}'"
        )

    # 4. Register class in default registry
    default_registry.register_class(node_type_name, node_class)
    logger.info(
        "Dynamically registered node '%s' (class '%s') from path '%s'",
        node_type_name, class_name, file_path
    )


class DynamicNodeCompiler:
    """Compiles and registers dynamically generated GraphNode modules."""

    def __init__(self, workspace_dir: Optional[str] = None) -> None:
        # Default workspace to app/workflow/dynamic_nodes
        if workspace_dir:
            self.workspace = Path(workspace_dir)
        else:
            self.workspace = Path(__file__).parent / "dynamic_nodes"
        
        self.workspace.mkdir(parents=True, exist_ok=True)

    def validate_code_safety(self, code: str) -> None:
        """Scan generated code against strict static safety guardrails."""
        match = _FORBIDDEN_RE.search(code)
        if match:
            trigger = match.group(0)
            raise SecurityException(
                f"Dynamic code validation failed: forbidden expression '{trigger}' detected. "
                f"Self-programming nodes cannot access system resources, execute arbitrary "
                f"subprocesses, or perform raw dynamic evaluation."
            )

    def compile_and_register(self, node_name: str, code_content: str) -> str:
        """Sanitize, write to disk, and dynamically register the node.

        Args:
            node_name: Registry key name (snake_case), e.g., 'auth_hex_resolver'.
            code_content: Standalone Python class source code.

        Returns:
            Absolute file path of the written module.
        """
        # 1. Enforce safety checks
        self.validate_code_safety(code_content)

        # 2. Format filename
        node_name_clean = re.sub(r"[^a-zA-Z0-9_-]", "", node_name).lower()
        file_path = self.workspace / f"{node_name_clean}.py"

        # 3. Write code to disk
        logger.info("Writing dynamically generated node code to: %s", file_path)
        with file_path.open("w", encoding="utf-8") as fh:
            fh.write(code_content)

        # 4. Dynamically register class in GraphNode Registry
        dynamically_register_node(str(file_path), node_name_clean)

        return str(file_path)

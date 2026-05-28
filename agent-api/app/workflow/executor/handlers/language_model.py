"""Language Model node handler — config provider for agent nodes.

The ``language_model`` node is purely a configuration node — it doesn't
execute anything, it just declares which LLM the connected agent
should use.  Resolution itself happens lazily in
:func:`app.workflow.llm_config.resolve_llm_config` when the ReactStrategy
needs a model.

This handler's job is therefore minimal: read the node config through
the shared :func:`read_llm_node` (so handler and resolver agree on
schema), and return a ``success`` status so the canvas live-status
overlay doesn't show the node as *skipped*.
"""
import logging
from typing import Any, Dict

from app.workflow.llm_config import read_llm_node

from . import register

logger = logging.getLogger(__name__)


@register("language_model")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the LLM node's config and echo it back as a success result."""
    cfg = read_llm_node(node)

    if not (cfg.config_name or (cfg.inline_provider and cfg.inline_model)):
        logger.warning(
            "language_model node %s: no LLM selected — agent will fall back "
            "to first available DB config",
            node.get("id"),
        )
        return {
            "status": "success",
            "output": "Language Model: no model selected (will use DB default)",
            "llm_config": None,
        }

    label = cfg.config_name or f"{cfg.inline_provider}/{cfg.inline_model}"
    temp  = cfg.temperature if cfg.temperature is not None else 0.1

    logger.info(
        "language_model node %s: config='%s' temp=%.2f", node.get("id"), label, temp,
    )

    return {
        "status": "success",
        "output": f"Language Model configured: {label} (temp={temp:.2f})",
        "llm_config": {
            "config_name": cfg.config_name,
            "provider":    cfg.inline_provider,
            "model":       cfg.inline_model,
            "temperature": temp,
            "system":      cfg.system,
        },
    }

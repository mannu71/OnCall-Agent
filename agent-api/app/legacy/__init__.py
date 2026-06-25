"""Legacy harness code, retained behind the ``harness=legacy`` flag.

The platform's default harness is now the official ``deepagents`` stack
(see ``app/harness/deep_agent.py``). The modules here implement the previous
in-house LangGraph ReAct construction and are kept only as a fallback /
reference while the deepagents migration completes. They are NOT on the default
code path. Shared, still-used helpers (e.g. ``compose_system_prompt``, tool
assembly, ``execute_agent``, the policy engine) intentionally remain in their
original locations because the deepagents path reuses them.
"""

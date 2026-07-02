-- 027_multiagent_profile.sql
-- Adds a generic orchestrator agent profile for multi-agent (supervisor-worker) workflows.
--
-- This profile provides the orchestrator role prompt only. Named subagents are
-- defined per-workflow on the agent node itself (the "Subagent Definitions" JSON field),
-- NOT hardcoded here — so each workflow can configure its own domain experts.
--
-- Users can:
--   a) Reference this profile by name on an agent node (profile = 'orchestrator')
--      to get the orchestrator role prompt, then define subagents on the node.
--   b) Skip the profile entirely and write a custom role prompt + subagents JSON
--      directly on the agent node.
--
-- Requires: 025_agent_profiles.sql

INSERT INTO agent_profiles (
    name,
    description,
    role_prompt,
    default_tools,
    output_schema,
    deep_features,
    builtin
)
VALUES (
    'orchestrator',

    'Generic orchestrator profile: delegates evidence-gathering to named subagents defined on the agent node, then synthesises their findings. Configure subagents via the "Subagent Definitions" field on the agent node.',

    'You are an orchestrator agent. Your job is synthesis, not evidence-gathering.

DELEGATION PROTOCOL
1. Decompose the request into separable tracks and delegate each to the matching subagent using the delegate_to_* tools available to you.
2. Issue all delegations before synthesising — wait for all subagents to report back.
3. Only call raw tools directly for a quick cross-check that a subagent missed or for a simple follow-up that does not warrant a full delegation.
4. Synthesise all subagent findings into one cohesive, evidence-backed response.

WHEN NO SUBAGENTS ARE CONFIGURED
If no delegate_to_* tools are present, handle the request yourself using the tools available.',

    '[]'::jsonb,

    'investigation',

    '{}'::jsonb,

    TRUE
)
ON CONFLICT (name) DO NOTHING;

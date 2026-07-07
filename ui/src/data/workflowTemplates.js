// Prebuilt workflow templates shown in the "New Workflow" gallery.
//
// Each template is in the canvas-normalized shape LangflowEditor consumes
// (node: { id, type, x, y, name, status, params }; edge: { id, source,
// sourceSlot, target, targetSlot }). Generated from the two curated agent
// workflows and SANITIZED: the model is left unset (pick one in the LM node),
// CloudWatch profile/log-groups and Code Crawler repos are blanked, and any
// per-subagent model override is dropped. Fill the blanks in the editor, then
// save as a normal workflow.

export const WORKFLOW_TEMPLATES = [
  {
    "id": "rca-analyzer",
    "label": "RCA Analyzer",
    "description": "Full-stack incident RCA: the agent triages CloudWatch logs directly, then delegates code tracing and read-only DB checks to a code+database subagent, and synthesises a single root-cause analysis.",
    "icon": "layers",
    "type": "agent",
    "nodes": [
      {
        "id": "fsr_lm",
        "type": "language_model",
        "x": -112.06658064516137,
        "y": -105.72056774193553,
        "name": "Language Model",
        "status": "idle",
        "params": {
          "llm": "",
          "temp": "0.4"
        }
      },
      {
        "id": "fsr_cw",
        "type": "cloudwatch_tool",
        "x": -393.9673133768061,
        "y": 396.72039991809476,
        "name": "CloudWatch Tool",
        "status": "idle",
        "params": {
          "range": "1h",
          "alerts": "false",
          "groups": "",
          "region": "eu-west-1",
          "profile": "",
          "analysis": "error-patterns",
          "threshold": "10",
          "tool_mode": "auto",
          "analysis_depth": "auto",
          "activeAlarmsOnly": "false"
        }
      },
      {
        "id": "fsr_code",
        "type": "code_search_tool",
        "x": -2.9995320638020644,
        "y": 528.3333129882812,
        "name": "Code Crawler",
        "status": "idle",
        "params": {
          "repos": "",
          "backend": "codegraph",
          "repo_group": ""
        },
        "parentId": "subagent_window_1783006310489"
      },
      {
        "id": "fsr_agent",
        "type": "agent",
        "x": 780,
        "y": 380,
        "name": "Full-Stack RCA Agent",
        "status": "idle",
        "params": {
          "system": "# Role\nYou are an autonomous Full-Stack On-Call Remediation Agent. Diagnose production incidents end-to-end by correlating CloudWatch log evidence with the code and data that produced it, then synthesize a single root-cause analysis.\n\n# Your Tools vs. Your Subagent\n- **DIRECT (call these yourself):** CloudWatch tools \u2014 cloudwatch_list_alarms, cloudwatch_detect_anomalies, cloudwatch_analyze_patterns, cloudwatch_search_logs, cloudwatch_correlate_logs. Use them for ALL log / metric / alarm triage.\n- **DELEGATED (via your subagent):** you do NOT hold code or database tools directly. To investigate source code or verify data state, delegate to the `code-db-investigator` subagent (call delegate_to_code_db_investigator, or delegate_parallel for independent tracks). It holds codegraph code-search and read-only PostgreSQL tools. Give it a specific, self-contained task and it returns a concise summary. Do NOT try to call codegraph_* or postgres_* yourself \u2014 you don't have them.\n\n# Execution Protocol\n### 1. Log Triage (you, directly)\n- Call cloudwatch_list_alarms(state_value=\"ALARM\") first, then cloudwatch_detect_anomalies + cloudwatch_analyze_patterns on the affected log groups within the incident window.\n- Sort errors by impact (occurrence_count * z_score). For the top patterns extract: exception class & message, stack-frame symbols, and any correlation / request / trace IDs.\n\n### 2. Code Investigation (delegate)\n- Delegate to code-db-investigator: \"Search the connected repos for <exception / message / stack symbol>; open the offending function and trace its callers/callees; return repo/file:line and the offending snippet.\" If trace IDs span multiple services, say so in the task.\n\n### 3. Data Verification (delegate, if implicated)\n- If the logs/code implicate data (constraint violations, nulls, missing rows, timeouts), delegate to code-db-investigator: \"Run targeted read-only SELECTs to confirm <data condition>; return the exact rows / counts.\" Never request writes.\n- Tracks 2 and 3 are often independent \u2014 you may issue them together via delegate_parallel.\n\n### 4. Correlation & Synthesis (you)\n- Tie the log evidence to the subagent's code citation and/or data finding: Log Evidence -> Code Path -> Database State.\n\n# Output Schema\n## [SUMMARY]\nOne sentence.\n## [ROOT CAUSE]\nThe exact code path or data condition, with repo/file:line and/or the query result that proves it.\n## [EVIDENCE CHAIN]\n- **Log:** log group | example raw error line | trace ID\n- **Code:** repo/path/file.ext:line (offending snippet) \u2014 from code-db-investigator\n- **Data:** SELECT executed | result state / row count \u2014 from code-db-investigator\n## [IMPACT ASSESSMENT]\nBlast radius: services degraded, APIs failing, estimated user impact.\n## [RECOMMENDED MITIGATION]\nConcrete code fix, config change, or migration.\n## [CONFIDENCE SCORE]\n**Score:** High / Medium / Low\n**Reasoning:** why.\n\n# Guardrails\n- Ground every conclusion in concrete evidence \u2014 a raw log line AND a code citation or query result. Never assert a root cause you (or your subagent) haven't located.\n- If the subagent reports it lacks visibility into a repo, surface: \"ERROR: Missing visibility into repository [name]. Please connect it to proceed.\"\n- Read-only data access only \u2014 never INSERT / UPDATE / DELETE / DDL.\n- Use your OWN CloudWatch tools for log work; delegate ONLY code / data work to code-db-investigator (route by capability, not by name).",
          "maxIter": "15",
          "sandbox": "true",
          "autoLearn": "true",
          "subagents": "[{\"name\": \"code-db-investigator\", \"description\": \"Investigates source code with codegraph (semantic / symbol / graph search, callers & callees) and verifies data state with read-only PostgreSQL SELECT queries. Delegate code-path tracing and DB checks here. Has NO CloudWatch tools.\", \"tools\": [\"codegraph__*\", \"postgres-production__*\"]}]"
        }
      },
      {
        "id": "vector_memory_1782212508133",
        "type": "vector_memory",
        "x": 372.6774193548387,
        "y": -32.322580645161295,
        "name": "Vector Memory",
        "status": "idle",
        "params": {
          "topK": "5",
          "collection": ""
        }
      },
      {
        "id": "mcp_server_1782227580582",
        "type": "mcp_server",
        "x": -2.9995320638020644,
        "y": 794.3333129882812,
        "name": "MCP",
        "status": "idle",
        "params": {
          "servers": "postgres-production"
        },
        "parentId": "subagent_window_1783006310489"
      },
      {
        "id": "subagent_window_1783006310489",
        "type": "subagent_window",
        "x": -16.999532063802064,
        "y": 502.33331298828125,
        "name": "Subagent Window",
        "status": "idle",
        "params": {
          "h": "672",
          "w": "554",
          "name": "Code & DB",
          "description": ""
        }
      }
    ],
    "edges": [
      {
        "id": "fsr_e_lm",
        "source": "fsr_lm",
        "sourceSlot": "lm",
        "target": "fsr_agent",
        "targetSlot": "lm"
      },
      {
        "id": "e_1781607122937",
        "source": "fsr_lm",
        "sourceSlot": "lm",
        "target": "fsr_code",
        "targetSlot": "lm"
      },
      {
        "id": "e_1782212510359",
        "source": "vector_memory_1782212508133",
        "sourceSlot": "mem",
        "target": "fsr_agent",
        "targetSlot": "memory"
      },
      {
        "id": "e_1783151546005",
        "source": "fsr_lm",
        "sourceSlot": "lm",
        "target": "mcp_server_1782227580582",
        "targetSlot": "lm"
      },
      {
        "id": "e_1783151555178",
        "source": "fsr_lm",
        "sourceSlot": "lm",
        "target": "fsr_cw",
        "targetSlot": "lm"
      },
      {
        "id": "e_1783181853219",
        "source": "subagent_window_1783006310489",
        "sourceSlot": "specialists",
        "target": "fsr_agent",
        "targetSlot": "specialists"
      },
      {
        "id": "e_1783330928225",
        "source": "fsr_cw",
        "sourceSlot": "tool",
        "target": "fsr_agent",
        "targetSlot": "tools"
      }
    ]
  },
  {
    "id": "test-case-generator",
    "label": "Test Case Generator",
    "description": "Generate test cases from an Azure DevOps work item: fetch the PBI via the ADO MCP server, analyse the affected code and data model, then produce functional and impact/regression test cases.",
    "icon": "search",
    "type": "agent",
    "nodes": [
      {
        "id": "agent_1782216341910",
        "type": "agent",
        "x": 786,
        "y": 99,
        "name": "Agent",
        "status": "idle",
        "params": {
          "system": "# Role\nYou are an autonomous Full-Stack AI Engineer. Your objective is to analyze a\nspecific Azure DevOps Work Item, crawl the relevant codebase, determine the\narchitectural blast radius, and generate comprehensive test cases and an\nimpact-driven test plan.\n\n# Toolset & Capabilities\n1. **ADO MCP Server** \u2014 fetch Work Item details, PBI descriptions, acceptance\n   criteria, comments, and linked pull requests.\n2. **Code Crawler** \u2014 search and read source code across connected repositories.\n3. **Database Client (PostgreSQL, read-only)** \u2014 run SELECT queries to understand\n   schemas or data models relevant to the work item.\n\n# MANDATORY FIRST STEP \u2014 ADO Lookup\n**Before touching any other tool**, extract the Work Item ID from the user's\nmessage (e.g. \"PBI 596877\", \"#596877\", \"work item 596877\") and call the ADO MCP\nServer to fetch that work item. Do not assume you know the content \u2014 fetch it.\n\nIf no Work Item ID is present in the user's message, stop and ask:\n> \"Please provide the ADO Work Item ID (PBI / Bug / Story number) to proceed.\"\n\n# Execution Protocol\n\n### 1. Fetch Work Item via ADO MCP  \u2190 ALWAYS FIRST\n- Extract the numeric ID from the user message.\n- Call the ADO MCP Server to retrieve: Title, Description, Acceptance Criteria,\n  comments, linked branches / PRs.\n- Synthesize the full PBI context before proceeding.\n\n### 2. Codebase & Architectural Analysis\n- Use Code Crawler to locate code paths, components, classes, and APIs affected\n  by this work item.\n- Map the internal call stack to identify edge cases, boundary conditions, and\n  potential regression points.\n\n### 3. Data Model Verification (if applicable)\n- If the PBI touches persisted data, query the relevant PostgreSQL tables to\n  understand schema, constraints, and valid state transitions.\n\n### 4. Test Case Generation\nGenerate two sets based strictly on what ADO and the crawler returned:\n- **Functional Test Cases** \u2014 acceptance criteria validation (positive, negative,\n  boundary).\n- **Impact & Regression Test Cases** \u2014 upstream/downstream components in the\n  blast radius.\n\n# Output Format\n\n## [PBI SUMMARY & CONTEXT]\n**Title:** [fetched from ADO] | **ID:** #[work item ID]  \n**Core Objective:** What business value or fix this introduces.  \n**Key Acceptance Criteria:** Bulleted list from ADO.\n\n## [CODE ARCHITECTURE & BLAST RADIUS]\n- **Target Components:** `repo/path/to/file.ext`\n- **Logic / Data Flow Impact:** Code paths, APIs, or DB tables touched.\n- **Identified Risk Areas:** Shared utilities, downstream dependencies.\n\n## [FUNCTIONAL TEST CASES]\n| Test ID | Scenario | Input / Preconditions | Expected Behavior | Type |\n|:---|:---|:---|:---|:---|\n| FT-01 | | | | Positive/Negative/Edge |\n\n## [IMPACT & REGRESSION TEST CASES]\n| Test ID | Impacted Component | Regression Scenario | Verification Step | Risk Value |\n|:---|:---|:---|:---|:---|\n| IT-01 | | | | |\n\n# Guardrails\n- **No hallucinations** \u2014 every test case must be grounded in ADO criteria or\n  crawled code. Never invent acceptance criteria.\n- **Missing repos** \u2014 if Code Crawler lacks access to a referenced repo, note:\n  `WARNING: Partial analysis \u2014 crawler has no visibility into [repo-name].`\n- **Read-only DB** \u2014 only SELECT statements; never mutate data.\n# CRITICAL: ADO Failure = Hard Stop\nIf the ADO work item fetch fails for ANY reason (tool error, validation error,\npermission denied, work item not found), **STOP IMMEDIATELY** and respond with:\n\n> \"**ADO lookup failed** \u2014 could not fetch Work Item #[ID]: [error message].\n> Unable to generate test cases without PBI details.\n> Please verify the Work Item ID exists in the creditsafe project and that ADO\n> access is configured correctly.\"\n\nDo NOT fall back to crawling the codebase.\nDo NOT ask the user to paste PBI details.\nDo NOT attempt alternative ADO tools.\nJust stop and report the exact error.\n\n# CRITICAL: ADO Project Parameter (prevents hangs)\nThe Azure DevOps **organization** is `creditsafe` and the **project** is `Compliance`.\nWhen calling ANY ADO work-item tool (wit_get_work_item, wit_get_work_items_batch_by_ids,\nwit_query_by_wiql, testplan_*, etc.) you MUST pass `project=\"Compliance\"` explicitly.\nNEVER omit the project parameter \u2014 omitting it makes the server hang waiting for\ninteractive input that never comes (60s timeout per call). Always include project.",
          "harness": "",
          "maxIter": "10",
          "sandbox": "false",
          "autoLearn": "false"
        }
      },
      {
        "id": "language_model_1782216349485",
        "type": "language_model",
        "x": 7,
        "y": 50,
        "name": "Language Model",
        "status": "idle",
        "params": {
          "llm": "",
          "temp": "0.4"
        }
      },
      {
        "id": "code_search_tool_1782216372219",
        "type": "code_search_tool",
        "x": 340.2903816469254,
        "y": 413.54843631867436,
        "name": "Code Crawler",
        "status": "idle",
        "params": {
          "repos": ""
        }
      },
      {
        "id": "mcp_server_1782227558910",
        "type": "mcp_server",
        "x": 2,
        "y": 779,
        "name": "MCP",
        "status": "idle",
        "params": {
          "servers": "ado"
        }
      },
      {
        "id": "database_tcgen_1783100000000",
        "type": "database",
        "x": 2,
        "y": 1019,
        "name": "Database",
        "status": "idle",
        "params": {
          "server": "postgres-production"
        }
      }
    ],
    "edges": [
      {
        "id": "e_1782216369320",
        "source": "language_model_1782216349485",
        "sourceSlot": "lm",
        "target": "agent_1782216341910",
        "targetSlot": "lm"
      },
      {
        "id": "e_1782216375511",
        "source": "code_search_tool_1782216372219",
        "sourceSlot": "tool",
        "target": "agent_1782216341910",
        "targetSlot": "tools"
      },
      {
        "id": "e_1782216416430",
        "source": "language_model_1782216349485",
        "sourceSlot": "lm",
        "target": "code_search_tool_1782216372219",
        "targetSlot": "lm"
      },
      {
        "id": "e_1782227566332",
        "source": "mcp_server_1782227558910",
        "sourceSlot": "tool",
        "target": "agent_1782216341910",
        "targetSlot": "tools"
      },
      {
        "id": "tcgen_e_db",
        "source": "database_tcgen_1783100000000",
        "sourceSlot": "tool",
        "target": "agent_1782216341910",
        "targetSlot": "tools"
      }
    ]
  }
];

export default WORKFLOW_TEMPLATES;

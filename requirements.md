# AI System Architecture: Orchestrator vs ReAct Agent

## 1. Overview

This document describes the architecture of a system that supports two execution models:

1. **Orchestrator (Non-Agent Based Execution)**
2. **ReAct Agent (Agent-Based Execution)**

The system is built using LangChain and LangGraph to support both deterministic workflows and dynamic reasoning-based execution.

---

## 2. High-Level Architecture Diagram

```
                        +----------------------+
                        |      Scheduler       |
                        +----------+-----------+
                                   |
                                   v
                        +----------------------+
                        |   Request Router     |
                        +----------+-----------+
                                   |
            +----------------------+----------------------+
            |                                             |
            v                                             v
+--------------------------+                +---------------------------+
|      Orchestrator        |                |        ReAct Agent        |
| (Deterministic Flow)     |                | (Reason + Act Loop)       |
+------------+-------------+                +-------------+-------------+
             |                                            |
             v                                            v
+--------------------------+                +---------------------------+
|   Predefined Workflows   |                |     Tool Selection        |
|  (SQL Parsing, ETL, etc) |                |   (Dynamic Reasoning)     |
+------------+-------------+                +-------------+-------------+
             |                                            |
             v                                            v
+--------------------------+                +---------------------------+
|   Data Sources / APIs    |                |   Tools (DB, APIs, etc.)  |
| (Postgres, File System)  |                +-------------+-------------+
+------------+-------------+                              |
             |                                            v
             v                                +---------------------------+
+--------------------------+                |     Observation Loop       |
|        Response          |<---------------+  (Iterative Reasoning)     |
+--------------------------+                +---------------------------+
```

---

## 3. Components

### 3.1 Request Router

* Determines whether the request should go to:

  * Orchestrator
  * ReAct Agent
* Decision can be based on:

  * Input type (e.g., SQL file vs natural language query)
  * Metadata
  * Feature flags

---

### 3.2 Orchestrator (LangGraph Flow)

**Characteristics:**

* Deterministic
* No reasoning loop
* Predefined execution steps

**Responsibilities:**

* Parse input (e.g., SQL file)
* Validate structure
* Execute queries
* Return results

**Implementation (LangGraph):**

* Nodes represent steps (parse → validate → execute → respond)
* Edges define fixed transitions

---

### 3.3 ReAct Agent (LangChain Agent)

**Characteristics:**

* Uses reasoning + action loop
* Dynamically selects tools

**Responsibilities:**

* Interpret user intent
* Decide which tools to use
* Execute actions iteratively
* Aggregate results

**ReAct Loop:**

1. Thought
2. Action
3. Observation
4. Repeat until completion

---

### 3.4 Tools Layer

Shared across both systems (but dynamically used only by agent):

* Database Tool (Postgres)
* File Reader Tool
* API Client Tool
* Transformation Tool

---

## 4. Execution Flow

### 4.1 Orchestrator Flow

1. User uploads SQL file
2. Router identifies deterministic task
3. Orchestrator executes:

   * Parse SQL
   * Validate
   * Query Postgres
4. Return response

---

### 4.2 ReAct Agent Flow

1. User submits query
2. Router sends to agent
3. Agent:

   * Understands intent
   * Chooses tool
   * Executes action
   * Observes result
   * Iterates
4. Final response returned

---

## 5. When to Use What

| Scenario                | Use Orchestrator | Use ReAct Agent |
| ----------------------- | ---------------- | --------------- |
| SQL file execution      | ✅                | ❌               |
| Structured ETL pipeline | ✅                | ❌               |
| Natural language query  | ❌                | ✅               |
| Multi-step reasoning    | ❌                | ✅               |
| Tool decision required  | ❌                | ✅               |

---

## 6. Technology Mapping

| Component       | Technology       |
| --------------- | ---------------- |
| Workflow Engine | LangGraph        |
| Agent Framework | LangChain        |
| LLM Provider    | OpenAI / Bedrock |
| Database        | Postgres         |

---

## 7. Future Enhancements

* Add memory to ReAct agent
* Hybrid mode (Orchestrator invoking agent)
* Observability (tracing via LangSmith)
* Retry & fallback strategies

---

## 8. Summary

This architecture cleanly separates:

* Deterministic workflows → Orchestrator
* Intelligent decision-making → ReAct Agent

This ensures scalability, maintainability, and flexibility in handling both simple and complex tasks.

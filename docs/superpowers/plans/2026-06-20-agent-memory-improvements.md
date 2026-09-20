# Agent Memory Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MiroFish-Offline's agent memory actually persist live simulation actions, preserve structured cross-platform records, and expose per-agent memory context back to agents during runs.

**Architecture:** Keep Neo4j graph memory as the long-term graph/RAG layer, but add a structured simulation-memory layer in each simulation directory for deterministic per-agent and cross-platform retrieval. The backend start route must pass the injected `GraphStorage` into the runner and report the actual memory updater status. The OASIS scripts will update structured memory from every logged action and append concise memory context to each active agent persona before `LLMAction`.

**Tech Stack:** Python 3.11, Flask, pytest, Neo4j `GraphStorage`, OASIS simulation scripts, JSONL file storage.

---

### Task 1: Live Graph Memory Wiring

**Files:**
- Modify: `backend/app/api/simulation.py`
- Modify: `backend/app/services/simulation_runner.py`
- Test: `backend/tests/test_simulation_start_memory.py`

- [ ] **Step 1: Write failing tests**
  - Test that `/api/simulation/start` passes `current_app.extensions['neo4j_storage']` into `SimulationRunner.start_simulation`.
  - Test that `SimulationRunner.start_simulation(..., enable_graph_memory_update=True, storage=None)` raises instead of silently disabling memory.

- [ ] **Step 2: Run red test**
  - Run: `cd backend && uv run pytest tests/test_simulation_start_memory.py -q`
  - Expected: FAIL because storage is not passed and missing storage is swallowed.

- [ ] **Step 3: Implement**
  - In `simulation.py`, fetch `storage = current_app.extensions.get('neo4j_storage')` when graph memory is enabled; return 503 if missing; pass `storage=storage`.
  - In `simulation_runner.py`, raise on missing storage and add `graph_memory_update_enabled` plus `graph_memory_update_error` to `SimulationRunState`.

- [ ] **Step 4: Verify**
  - Run: `cd backend && uv run pytest tests/test_simulation_start_memory.py -q`

### Task 2: Structured Memory Model

**Files:**
- Create: `backend/scripts/simulation_memory.py`
- Test: `backend/tests/test_simulation_memory.py`

- [ ] **Step 1: Write failing tests**
  - Test that actions are written to `memory/agent_memories.jsonl`.
  - Test that a follow/like/comment action records actor, target agent, platform, round, content, and searchable text.

- [ ] **Step 2: Run red test**
  - Run: `cd backend && uv run pytest tests/test_simulation_memory.py -q`
  - Expected: FAIL because `simulation_memory.py` does not exist.

- [ ] **Step 3: Implement**
  - Add `StructuredMemoryRecord` and `SimulationMemoryStore`.
  - Normalize `CREATE_POST`, `CREATE_COMMENT`, `LIKE_POST`, `DISLIKE_POST`, `REPOST`, `QUOTE_POST`, `FOLLOW`, `LIKE_COMMENT`, `DISLIKE_COMMENT`, `SEARCH_POSTS`, `SEARCH_USER`, and `MUTE`.
  - Persist records as JSONL and keep deterministic retrieval by `agent_id`, `round`, and `platform`.

- [ ] **Step 4: Verify**
  - Run: `cd backend && uv run pytest tests/test_simulation_memory.py -q`

### Task 3: Cross-Platform Per-Agent Retrieval

**Files:**
- Modify: `backend/scripts/simulation_memory.py`
- Test: `backend/tests/test_simulation_memory.py`

- [ ] **Step 1: Write failing tests**
  - Test that agent 1 retrieves both Twitter and Reddit memories.
  - Test that memories where agent 1 is the target are included even if another agent acted.

- [ ] **Step 2: Run red test**
  - Run: `cd backend && uv run pytest tests/test_simulation_memory.py -q`

- [ ] **Step 3: Implement**
  - Add `get_agent_memories(agent_id, limit, platform=None)` and `build_context_for_agent(agent_id, limit)`.
  - Sort by newest round/timestamp and produce concise prompt-safe bullet lines.

- [ ] **Step 4: Verify**
  - Run: `cd backend && uv run pytest tests/test_simulation_memory.py -q`

### Task 4: Simulation Integration

**Files:**
- Modify: `backend/scripts/run_parallel_simulation.py`
- Modify: `backend/scripts/run_twitter_simulation.py`
- Modify: `backend/scripts/run_reddit_simulation.py`
- Test: `backend/tests/test_simulation_script_memory_injection.py`

- [ ] **Step 1: Write failing tests**
  - Test that logged actions call `SimulationMemoryStore.add_action`.
  - Test that active agents receive memory context before `LLMAction`.

- [ ] **Step 2: Run red test**
  - Run: `cd backend && uv run pytest tests/test_simulation_script_memory_injection.py -q`

- [ ] **Step 3: Implement**
  - Instantiate one memory store per simulation directory.
  - After action logging, call `memory_store.add_action(...)`.
  - Before each `LLMAction`, append a bounded memory context to the agent persona/profile text if possible.

- [ ] **Step 4: Verify**
  - Run: `cd backend && uv run pytest tests/test_simulation_script_memory_injection.py -q`

### Task 5: End-to-End Verification Gates

**Files:**
- Modify: `AGENTS.md` only if useful for contributor memory commands
- Test: all tests above

- [ ] **Step 1: Run focused tests**
  - Run: `cd backend && uv run pytest tests/test_simulation_memory.py tests/test_simulation_start_memory.py tests/test_simulation_script_memory_injection.py -q`

- [ ] **Step 2: Run existing verification**
  - Run: `cd backend && uv run python scripts/test_profile_format.py`

- [ ] **Step 3: Run frontend build**
  - Run: `npm run build`

- [ ] **Step 4: Inspect git diff**
  - Run: `git diff -- backend/app/api/simulation.py backend/app/services/simulation_runner.py backend/scripts backend/tests docs/superpowers/plans/2026-06-20-agent-memory-improvements.md`

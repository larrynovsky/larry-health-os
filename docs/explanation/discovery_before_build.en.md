<!-- translation-of: docs/explanation/discovery_before_build.md sha256:abe6e6bda67e -->
**English** · [Русский](discovery_before_build.md)

# Discovery before building: why the oracle map was discarded and duplicates are caught through data

> ADR · 2026-07-23 · status: accepted (owner) · thread project-context
> Read this BEFORE "improving" project_context with a new map/digest
> that the agent must read — this path has been tried and closed by experiment.

## The problem

An agent with accumulated context creates duplicates and fails to see neighboring code: it writes a second
function with the same name without grepping def (F-106: `get_active_protocols` ×2 —
protocols_db.py + hai_hypotheses.py; corpus C-07), or recreates existing functionality
under another name. The key property: a duplicate under a DIFFERENT name is invisible
to grep-by-name but visible through the DATA (table/keys) it touches.
Capabilities are anchored to data, not names.

## The experiment that closed the oracle map (10/0, 2026-07-23)

The hypothesis was that a preflight digest (a structural subsystem map) that the agent
READS before a task prevents false paths. Test: 10 subagents
(7 fresh + 3 with genuinely accumulated context) on traps (duplicate / contract
change / reading) — ZERO false paths even WITHOUT the map. The problem did not recur
even under load; accumulated context HELPED rather than hindered. Preregistered
conclusion: the map-as-reading is not justified by prevention. Separately established: a blind evaluation
cannot measure this benefit in principle (construct validity: artificial "load"
does not reproduce the problem; self-blinding is impossible — demand characteristics).

What DID work live (T2): the L2 receipt gate stopped a commit by
an agent under load; it did not resort to `--no-verify`, but ran preflight; corpus
entry C-22 triggered an actual check of 586 callers. Formula: **enforcement
(gate) + a corpus of false paths work; a structural map-as-reading does not.**

## Decision: three layers instead of a map

1. **Global rule** (Cowork Global Instructions, cross-project, set by the owner):
   before writing a new function/module, find what already exists BY DATA
   (table/key), not only by name. The conditional part ("if a tool exists, call it")
   switches itself off in projects without the engine (RN-D).
2. **Discovery tool**: `capabilities <имя|таблица>` — by_name + by_data
   (the engine's W/R maps). Delivered through the MCP server `project_context/mcp_server.py`
   (owner's decision, 2026-07-23; CLI-first rejected: the agent does not call a shell command
   from memory, but sees a tool in the tool list; this is the carrot against RN-F).
3. **Duplicate gate at pre-commit** (`dupgate`): BLOCK for an exact cross-file duplicate
   name INTRODUCED by the staged diff; WARN for "a new function touches someone else's table"
   (a duplicate candidate by data). New names only — legacy is not flagged (RN-E).
   Enabled after positive and negative controls passed (tests/unit/test_dupgate.py, RN-B).

The L2 receipt gate STAYS (owner's decision: "keep l2"): it catches the class
"did not run the ritual"; the duplicate gate catches the result. These are different safeguards.

## Rejected (do not rebuild)

- **Oracle map / preflight digest as reading** — 10/0. The `preflight` code is alive,
  but as a receipt generator for the L2 gate, NOT as a "read it → protected" contract.
- **Blind evaluation as a measure of benefit** — does not measure it (see above).
- **CLI-first for discovery** — overridden by the owner's decision in favor of MCP.
- **L0a alert enrichment (`enrich.py`)** — dormant: embedded in test_failure_handler,
  not being developed. Decision to "leave dormant," 2026-07-23.
- **Generalization ahead of evidence** — health first, cross-project later.

## Measuring benefit: the catch log and its honest limits

The only remaining measure is observational: `docs/reference/duplicate_catch_log.md`,
a log of real cases (early=discovery / late=gate / missed=found manually /
false alarm=WARN not confirmed). Owner's decision, 2026-07-23: the log is the AGENT'S working MEMORY
(dup_allowlist provenance, corpus candidates); a person does not review it.
The consequence stated aloud: the system has no human measure of benefit; "missed" is
a censored category (only accidentally discovered misses are visible); the log
gives a lower bound on misses, not completeness.

## Limits

- Discovery sees only data/def capabilities; pure logic without a database is invisible
  (RN-A). The `capabilities` output carries a scope note; an empty result ≠ "no duplicates."
- The "call discovery" step cannot be enforced (no pre-tool hook in the environment; RN-F) —
  only the RESULT is enforced (dupgate at commit); wasted work on a duplicate
  caught late cannot be eliminated completely.
- by_name is a bidirectional substring match and is noisy (`protocols` → `cmd_protocols`);
  the gate therefore uses only EXACT name matches, and only module-level functions
  (class methods are interfaces, outside judgment).
- The gate's by_data heuristic sees only SQL literals in the new function's body;
  it does not see ORM/indirect access.
- During an intentional copy refactor (splitting a file), the gate flags an honest
  copy — this is a feature (code duplication); the way out is dup_allowlist or `--no-verify`.

## Onboarding layer (addendum, evening of 2026-07-23)

The same principle extended from capabilities to knowledge: a new agent receives
the system's existing knowledge by PULL, not through a pushed map. The `project_intent` tool (the same
MCP server): intent registry catalog (id, title, invariant statuses — non-holds
are visible: "what cannot be trusted" matters more than "what exists") → an explanatory page by id (byte-for-byte
from disk, without retelling; the sole source is the git tree, read on every call) →
a module→subsystem bridge (the data→code→intent chain closes with project_capabilities).
Pages outside the registry are returned with a "freshness is not guarded" note + mtime; id is a name,
not a path (traversal is rejected by contract). A pointer to intent was added to
preflight output — reusing the only proven enforcement moment (T2). Do NOT build on top of
this: generated summaries/overviews (oracle map v2) and pushed "read it — protected"
reading are ruled out by the same 10/0 experiment.

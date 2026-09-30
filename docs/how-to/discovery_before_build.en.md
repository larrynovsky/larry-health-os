<!-- translation-of: docs/how-to/discovery_before_build.md sha256:c42942cf25bf -->
**English** · [Русский](discovery_before_build.md)

# How-to: find what exists BEFORE building (discovery + duplicate gate)

## Before a new function/module

1. Define the task in terms of data: which table/key supports it?
2. Call discovery: the MCP tool `project_capabilities` (if the server is registered)
   or `/opt/homebrew/bin/python3.11 -m project_context capabilities <таблица|имя>`.
3. The main output is `by_data`: who already touches this data. Found something similar →
   open the source and reuse/extend it; do not build a parallel implementation.
4. An empty result ≠ “no duplicates”: the tool cannot see capabilities unrelated to data
   (scope note in the output, RN-A).

## The gate blocked the commit (exact duplicate name)

- The correct path: reuse the existing function (file:line in the message).
- Intentional duplicate (interface/transition period): add the name to `dup_allowlist`
  in `project_context.json` + a line in `docs/reference/duplicate_catch_log.md`.
- Last resort: `git commit --no-verify` (skips ALL hooks, leaves a trace in the log).

## WARN “touches a table that is already served”

A candidate, not a verdict: run `capabilities <таблица>` and compare the candidates.
Actual duplicate → reuse it. False positive → you can commit (WARN does not block);
record the outcome in the catch-log (false positives are input for tuning the heuristic, RN-B).

## Register the MCP server (owner's step, once)

In the Claude Desktop MCP server configuration (alongside Desktop Commander):

```json
"project-context": {
  "command": "/opt/homebrew/bin/python3.11",
  "args": ["-m", "project_context.mcp_server"],
  "env": {
    "PYTHONPATH": "~/health_scripts",
    "PROJECT_CONTEXT_ROOT": "~/health_scripts"
  }
}
```

Verify: the `project_capabilities` tool appears in a new session; a call with
`query="protocols"` returns `get_active_protocols` in `by_data`.

## New project (cross-project)

The engine is domain-agnostic. On the same machine, do NOT copy the package; reuse it
through `PYTHONPATH=~/health_scripts` (one copy of the engine; duplicate prevention
does not start life with a duplicate of itself): use this in both pre-commit and discovery
(the MCP tool has a `root` parameter; one server serves all repos).
Create `project_context.json` in the project root (`{}` is fine), and add the
dupgate section to pre-commit (fail-open: no engine → skip). A separate
machine without health_scripts: then copy the package. No manifest → neutral;
discovery and the gate still work. Deployment reference: a neighbour project
(`scripts/hooks/macbook-pre-commit.sh`, 2026-07-23).

## Get oriented in an existing project (project_intent)

New session, unfamiliar project: `project_intent` without arguments → subsystem catalog
(a menu, not knowledge) → `project_intent id=<подсистема>` → intent + invariants with
statuses (non-holds = do not trust the claim) + a maintained page → then code_anchors.
If you arrived from a file rather than a question, `project_intent module=<имя.py>` tells you which
subsystem it belongs to. Pages “outside the registry” have no freshness guard: check against the code and look at mtime.

Why it works this way: `docs/explanation/discovery_before_build.md` (ADR).

## If the tool says “project root does not exist”

This is a failure, not an answer: the server is looking at a directory that does not exist. Both tools
(`project_capabilities` and `project_intent` in all forms) fail on a nonexistent root
instead of returning an empty result; emptiness was read as knowledge: “no duplicates,”
“no intent registry has been set up.” On 2026-09-22, `project-context-mm` pointed to a deleted
a neighbour project iCloud path and answered an intent question with “no registry” while the registry existed.

What to do: fix the server's `PROJECT_CONTEXT_ROOT` in
`~/Library/Application Support/Claude/claude_desktop_config.json` and immediately
restart Claude Desktop, or pass `root=<путь>` as a call argument.
A change made while the application is running and not picked up by a restart may
be overwritten: on September 22, the `mcpServers` section reverted byte for byte to the copy from before the
September 14 edit (the cause is a hypothesis, not proven).

Guard: `python3 -m project_context roots-check [<путь к конфигу>]` — exit 1 and the
server name if any root is nonexistent. Called from MacBook post-commit on every commit.

What the failure does NOT mean: an existing root without `subsystem_intent.yaml` is valid;
it still answers “no intent registry has been set up,” and that is true.

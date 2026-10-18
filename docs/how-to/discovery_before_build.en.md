<!-- translation-of: docs/how-to/discovery_before_build.md sha256:535b7c180a0e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](discovery_before_build.md)

# How-to: find what already exists before building (discovery + dupe gate)

## Before a new feature/module

1. Frame the task through data: which table/key serves it?
2. Call discovery: MCP tool `project_capabilities` (if the server is registered)
   or `/opt/homebrew/bin/python3.11 -m project_context capabilities <table|name>`.
3. The main output is `by_data`: who already touches that data. Found something similar →
   open the source and reuse/extend it, do not build a parallel.
4. An empty result ≠ "no duplicates": non-data capabilities are not visible to the tool
   (scope note in the output, RN-A).

## The gate blocked a commit (exact name duplicate)

- Correct path: reuse the existing function (file:line in the message).
- Intentional duplicate (interface / transition period): name in `dup_allowlist`
  in `project_context.json` + a line in `docs/reference/duplicate_catch_log.md`.
- Last resort: `git commit --no-verify` (skips ALL hooks, leaves a trace in the log).

## WARN "touches a table already served by"

A candidate, not a verdict: run `capabilities <table>`, compare the candidates.
Real duplicate → reuse. False positive → safe to commit (WARN does not block);
record the outcome in the catch-log (false positives are raw material for heuristic tuning, RN-B).

## Registering the MCP server (owner step, one time)

In the Claude Desktop MCP servers config (next to Desktop Commander):

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

Verification: in a new session the tool `project_capabilities` appears; calling it with
`query="protocols"` returns `get_active_protocols` in `by_data`.

## New project (cross-project)

The engine is domain-agnostic. On the same machine do NOT copy the package — reuse it
via `PYTHONPATH=~/health_scripts` (one copy of the engine; the anti-dupe system
does not begin its life as a duplicate of itself): this applies in pre-commit and in discovery
(the MCP tool has a `root` parameter — one server serves all repos).
In the project root create `project_context.json` (can be `{}`), add the
dupgate section to pre-commit (fail-open: no engine → skip). A separate
machine without health_scripts — then a copy of the package is appropriate. No manifest → neutral;
discovery and the gate work regardless. Reference deployment: neighboring project
(`scripts/hooks/macbook-pre-commit.sh`, 2026-07-23).

## Onboarding into an existing project (project_intent)

New session, unfamiliar project: `project_intent` with no arguments → subsystem catalog
(this is a menu, not knowledge) → `project_intent id=<subsystem>` → intent + invariants with
statuses (not-holds = do not trust that assertion) + warm page → then into code_anchors.
Arrived from a file rather than a question — `project_intent module=<name.py>` will tell you
which subsystem owns it. Pages "outside the registry" — no freshness guard: verify against the code, check mtime.

Why it is structured this way: `docs/explanation/discovery_before_build.md` (ADR).

## If the tool responds "project root does not exist"

This is a failure, not an answer: the server is looking at a directory that does not exist. Both tools
(`project_capabilities` and `project_intent` in all forms) fail on a dead root
rather than responding with emptiness — emptiness was being read as knowledge: "no duplicates",
"no intent registry found". On 22.09.2026 `project-context-mm` was pointing at a deleted
iCloud path of a neighboring project and in response to a query about intent was returning "no registry" when a live registry existed.

What to do: correct `PROJECT_CONTEXT_ROOT` for the server in
`~/Library/Application Support/Claude/claude_desktop_config.json` and immediately
restart Claude Desktop, or pass `root=<path>` as a call argument.
An edit made while the application is running and not picked up after a restart may
be overwritten: on 22.09 the `mcpServers` section returned byte-for-byte to the copy that existed before the 14.09 edit (reason — hypothesis, not proven).

Guard: `python3 -m project_context roots-check [<path to config>]` — exit 1 and the server name
if any root is dead. Called from the MacBook post-commit hook on every commit.

What a failure does NOT mean: a live root without `subsystem_intent.yaml` is a legitimate case;
it still responds "no intent registry found", and that is accurate.

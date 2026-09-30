<!-- translation-of: docs/explanation/llm_egress.md sha256:0b8e4de477bf -->
**English** · [Русский](llm_egress.md)

# Outbound access to an external LLM API: why one home

> **Document type:** Explanation (Diátaxis). The rule is in `CLAUDE.md §19` (in the private part).
> What to do when the guard blocks a call: [`docs/how-to/llm_guard_blocked.md`](../how-to/llm_guard_blocked.md).

## What came before

Measurement on 2026-08-03: the Anthropic client was constructed **27 times across 21 modules**. Each read the key itself from the hardcoded path `~/.health_secrets/anthropic_key` — twenty-seven copies of the knowledge of “where the secret lives,” none of which went through `secrets_paths`. Meanwhile, `secret_guard` was called **manually in three places within `doc_agent` alone**.

Protection depended on the author remembering to call it, rather than on the construction itself.

## Why this is not “just add the guard to two more modules”

The initial plan was to extend the guard to `hai_*` and `lab_recognizer` — the two paths that SEC-21 identified as uncovered. Measurement showed that this would treat a symptom: two new manual calls would become the third and fourth copies of the solution, and a month later they would drift from the original.

This pattern had already occurred three times in the project: the `BLOOD` constant diverged between two peer writers to the canonical store; the handwritten module dictionary in `arch_guard` diverged from the tree; the backup description diverged from reality by three months. Shared knowledge held as copies by peer writers drifts — this is §17 of the rule set.

So there is one home: `llm_client`.

## What the guard sees and does not see

The request's **text** blocks are scanned, including `system`. Images are not: a text search cannot find a secret in pixels, and dragging hundreds of kilobytes of base64 into the scan means paying for an illusion of coverage. The decision is recorded in one place instead of being implicit in twenty.

**Blindness differs from a finding.** Until 2026-08-03, “the guard found a secret” and “the guard could not run” returned the same value. With one consumer, the difference is cosmetic; with twenty, “everything is blocked because an environment variable is not set” is indistinguishable from “everything is blocked because a secret is leaking” — yet they require opposite remedies. These are now `SecretLeakBlocked` and `GuardUnavailable`.

The blindness is real, not hypothetical: `secrets_dir()` deliberately raises an exception in a tenant process without `HEALTH_SECRETS_DIR` — this protects against cross-tenant leakage (SEC-31). In production, the variable is set in all five partner plists; in a manual run or a new plist, it is not.

## A coverage boundary you need to know

The Anthropic key lives at the **owner's** path and is not tenant-specific: it is the project's key, not the patient's. Meanwhile, `secret_guard` scans the **current** tenant's secrets directory.

As a result, in a partner process the set of search needles does not contain the owner's key, and a leak of that key through the partner's path **will not be detected** by the guard. The guard is present and appears to work — exactly the case §14 describes as “liveness ≠ correctness.”

The owner's decision on 2026-08-03: leave it this way. Extending the scan to someone else's directory would give the partner process the owner's search needles — weakening multitenant isolation for coverage. Isolation matters more. The blind spot is documented here, not forgotten.

In Tanenbaum's terms (§7.2.1): the guard's conit is narrower than the client's conit — the unit used to measure safety does not cover the unit on which the protected component operates.

## Why the scope is narrow

`llm_client` does not choose a model, set `max_tokens`, retry, or log prompts. The temptation to collect all of this in a “client layer” is strong, but retries and the guard are logically independent axes. In one home, they start synchronizing unnecessarily: changing the retry policy would require touching the guard's code, and eventually no one would dare change either. This is false sharing (Tanenbaum §7.2.1) — the same mechanism as in CPU cache lines.

## How to prevent it from drifting back

Two sensors run nightly. `check_llm_direct_constructors` is a ratchet: no more than 27 constructors bypassing the factory; the baseline is lowered manually along with migration. `check_llm_guard_blocks_reported` counts blocks over the past day: without it, a block would disappear into the caller's log, and neither a false positive nor a real finding would be visible to a person.

The remaining modules migrate **when touched**, rather than as a separate project: when you touch a module for another reason, switch it to the factory and lower the baseline.

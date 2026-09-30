<!-- translation-of: docs/explanation/disposable_modules_refactor.md sha256:157854d569d8 -->
**English** · [Русский](disposable_modules_refactor.md)

# Refactoring for disposable modules

*Document type: explanation. Why read this: to understand why large modules were split behind a facade and what "a module behind a contract" means.*

---

## The task

AI writes and edits the system. The main risk of this work is "fixed it here, broke it there": a change in one place silently breaks dependent code. The protection is to make modules **disposable**: a module can safely be rewritten from scratch if it is hidden behind a testable contract. The contract makes a module disposable, not its size. This is not microservices — it is a modular monolith.

## The A / B / C method

A large module is treated in three steps.

**A — contract.** Record the module's public surface (functions called by others) in the contract registry. Until the surface is under contract, splitting is dangerous: dependent code can break silently.

**B — characterization.** Pin down behavior "as it is" with tests (not "as it should be"). This is the safety net beneath the split. Characterization often exposes real bugs — but captures them as they are and fixes them separately.

**C — split (strangler).** Extract cohesive parts into separate modules, leaving a facade that re-exports what was extracted. Importers do not notice the split — they still call the facade. Boundaries become visible gradually, without a breaking overhaul.

## What has been treated

`health_db` (formerly 4,818 lines, 54 importers) was split into 28 domain modules behind a facade; the core retained the connection, schema, and migrations.

`gp_agent` (1,783 lines) was treated with A/B/C: the context-building cluster (22 functions) was moved to `gp_context.py` behind a facade, reducing the module to 1,155 lines. A reverse dependency was also fixed — the `health_db` core no longer imports high-level code for a freshness constant (it moved to the domain layer).

`hai_core` (the language-model access hub, 28 importers) is small, with nothing to split, but lacked its own contract; a contract was added. This protected the last load-bearing hub.

## Where to stop

The size and connectivity audit showed something important: there is no "next health_db." The remaining large modules are leaves (nobody imports them) with dense test coverage. Splitting them for size alone is polishing, not load-bearing work. Effort is proportional to size multiplied by connectivity; for leaves, this product is small. Structural decomposition is honestly complete at this point.

## The principle that remains

A foundation lasts for years through invariants that guard themselves, not through one-time cleanliness. The splits therefore come with guards: a contract registry with a reverse check (an undeclared public function is an error), characterization pins, and a test-fixture completeness guard. Cleanliness without a guard degrades over quarters.

## How this became a rule (2026-07-26)

For a month and a half, the criterion lived here — in prose, in an explanation. Neither a person at
design time nor a machine at commit time read it: the rule existed on paper and was absent from
practice. Three things closed the gap, each with its own job.

**The checklist became a reference, not prose.** The six items and their numbers moved into
`project_context/disposability.json` — a machine-readable home inside the engine, because the disposability
methodology is domain-agnostic and the same for any project using this engine.
The numbers, however, can be overridden by a specific repository's policy: the items drift with the methodology
(rarely, globally); the thresholds drift with the project and stack (more often, locally). Read only through
`indexer.disposability_policy()`, which returns one merged result; direct file reads that bypass
the reader are caught by a source guard.

**The contract moved next to the module.** Instead of an entry in the shared `check_contracts.py` dictionary, each module
carries `contracts/<модуль>.json`: surface, neighboring contracts, verdict. The reason is not cosmetic:
a shared dictionary would mean that every new module edits a shared file — shared state in work across
multiple sessions; at two hundred sixty modules, the dictionary would become a God file that
could not itself be rewritten. This also fulfilled the promise the 06-26 plan called "Stream E":
the contract is the module's reference documentation.

**The gate sits at commit and judges only new files.** `dispgate` checks for a sidecar,
agreement between the declared and actual surface, verdict completeness, a test that
imports the module and calls its public name, and the absence of access to other modules' private names.
Size is only a warning: blocking on size would reproduce the original plan's third trap
(more modules — more joints where the generator can err).

The "new files only" perimeter was chosen not out of caution, but because it is the only total
predicate: "has the module been rewritten" would have to be measured by a diff percentage. A side benefit: the reverse
check "an undeclared public function is an error," which the 06-26 plan did not dare enforce
strictly against 286 existing leaks, can be kept strict here for free: legacy is outside the perimeter,
so false positives are absent by construction.

### What the gate fundamentally does not check

Disposability cannot be automated — and this is not "we have not written the linter yet." In Bach's distinction,
a check must have an explicit, algorithmic oracle, while in a test the main role belongs to
a tacit oracle: recognition, experience, "I can see that this cannot be rebuilt." The question "can I rewrite this
from scratch" relies on precisely such an oracle. The machine therefore checks the carriers of judgment, while a person
or agent signs the judgment itself; the gate blocks its absence, not its sign. Two checklist items —
one domain and extracted shared invariants — are not checked at all and live only in the verdict.

This also determines the form of feedback. A negative verdict is a legitimate answer, but it carries a stable
reason key. One reason surfacing in three different calendar weeks stops being an engineering detail
and becomes an architectural question; the lesson is then printed in `preflight` for whoever goes to work
there, and the person is notified once. Weeks, not modules: splitting `health_db` produced thirty-seven
new modules in one day with the same reason — a module-based counter would mistake one piece of work
for a recurring pattern.

### What guards the gate itself

A guard that dies silently is worse than no guard: people rely on it. The gate therefore has positive
controls for each item separately, and K1–K4 have been mutation-tested — disabling a check must
fail its own test, otherwise it is decoration. Hook wiring is guarded from two machines, because
pytest runs on MacBook and nightly integrity runs on Studio. Liveness is a behavioral probe, not a
timestamp: the gate fires only when a commit contains a new module, and "has not fired for a long time" is indistinguishable
from "broken," so the check supplies a known violator and requires the gate to name it.

It matters here not to overestimate one's own tooling, and the second external review round showed exactly that.
The nightly probe is **liveness, not coverage**: it supplies an obvious violator without a sidecar or
test, which is stopped by the very first check, so it will not notice the death of the second, third,
or fourth check. Targeted pytest checks at commit distinguish those; these are different layers with different strengths.
Conflating a cheap nightly pulse with a substantive mutation suite promises more than is actually guarded.
The check of the boundary "engine from the package, content from the index" is separately weak: it greps the
source and catches spelling rather than behavior — the same boundary written in different words would leave
it green. Both limitations are intentional and stated aloud; an acknowledged weakness costs less than
false confidence, and fixing both would cost more than it provides with one trusted writer.

Another lesson from the same round concerns not an individual check but the form of failure. The gate had a broad
exception handler that printed "skipping" and returned zero: an error inside the tool
became permission to commit. Two review findings were particular cases — valid JSON with a field
of the wrong type crashed internally and opened the door — but the property itself needed fixing, not two inputs.
The gate already expresses ENVIRONMENT failures (git unavailable) structurally, so an exception means a defect
in the gate itself, and the correct response to a defect in protection is to stop, not allow passage. Otherwise every
future bug in this code would be silent by default.

### What is judged: commit content versus the judging tool

The first version of the gate read five sources from two stores: it took the new module from the index, but
sidecars, tests, project policy, and the module index from the working tree. This looked like
a minor detail, but actually meant that the gate's read set did not belong to one session: half the
answers concerned the future commit, half the current state of the disk. This created two bypasses that
required no changes to the commit's content — changing the tree alone could flip
the verdict.

The resolution is a boundary based on meaning, not convenience. The module, its sidecar, its tests, and **project**
policy are REPOSITORY CONTENT: they are judged, so they come from the index. The engine checklist
`project_context/disposability.json` is the judging TOOL: it is not judged, it judges, and is read
from the installed package. The temptation to "take everything from the index" looks neater, but would break the shared
engine in a project where the package lives outside the repository — there would simply be nothing to read.

The asymmetry is recorded deliberately in the `project_context/staged.py` contract: without it, the next reader
will mistake it for carelessness and "fix" it. The other side of the boundary is that tool failure no longer
stays silent: if the checklist cannot be read, the gate blocks, because judging with an empty methodology is worse
than not judging at all, and a person does not read a WARN among other WARNs.

The remedy was also chosen deliberately. It would have been possible to compare the tree with the index at runtime and
warn about discrepancies — but that would add a "warned and allowed" mode, another
silent bypass in place of the removed one. Instead, the conflict is eliminated by construction: the index
is materialized in a disposable directory, and the gate simply has no second store.

## Related

- `ARCH_SNAPSHOT.md` — module registry (what lives where), autogenerated.
- `TESTING_CONTRACTS.md` — test contracts and oracles.
- `BACKLOG.md` — bugs found during characterization (BL-GP-1, BL-GP-2, etc.).

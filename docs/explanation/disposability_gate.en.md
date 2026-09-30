<!-- translation-of: docs/explanation/disposability_gate.md sha256:12168787bcc1 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](disposability_gate.md)

# Disposability gate: a new module behind a testable contract — why thinking out loud matters before you save

## What changed

- **The wording of the `project_layer_bounded` invariant has been clarified.** The claim was adjusted; the direction of the change was not determined mechanically — only the fact that the wording changed is recorded, without drawing any conclusion about whether it was strengthened or weakened.

**Updated:** 2026-09-12


## Why it exists

There is one well-familiar pattern in software development: something small is written quickly, it works, nobody touches it — and a month later it turns out that touching it is no longer possible. Not because the code is bad, but because nobody wrote down what exactly that piece does, where its edges are, what it promises its neighbors. Deleting it is scary, rewriting it — unknown how, because "what if something breaks."

The disposability gate exists precisely so that this moment does not pass in silence.

It does not evaluate whether a module is good. It requires one thing: that before code enters history, someone has rendered a judgment and written it down. The judgment may be negative — that is legitimate. What is not legitimate is its absence. The gate does not let anything "slip through" without reflection: either the person sat down and thought about the boundaries of the new piece, or the commit does not pass.

This is not bureaucracy for bureaucracy's sake. It is an attempt to catch that exact moment when the boundary is still visible to the author — and record it before it becomes invisible.

## What it does, in plain terms

When a developer tries to save a new module into history, the gate looks at what exactly ended up in the "parcel" — a snapshot of what has been staged for the commit, not everything that sits in the working directory. This is fundamental: if something was not explicitly included in the commit, the gate does not see it and does not count it as done. A partially assembled commit — where the module has been added but the accompanying files have not yet — does not pass.

The gate checks three things in substance.

First: alongside the new module there must be a so-called sidecar — a separate file that describes the module's public surface and its agreements with its neighbors. Without it there is no way to know what the module promises or to whom.

Second: there must be a test that explicitly calls something by its public name from that module. Not just "tests exist somewhere," but specifically a call by name — syntactic evidence that the boundary has been named and recorded. The gate does not know whether the test will actually pass, whether it contains any assertions, or whether the required code branch is reachable — it only sees that the call has been written.

Third: a verdict must have been rendered on six points. What exactly those points consist of is determined by the judgment tool built into the system itself and cannot be replaced by the contents of the commit. A project may configure certain things — for example, adding its own directories to the perimeter or naming its own canon — but there are things a project cannot override. If the project configuration file is malformed, the gate does not stay silent and does not guess — it stops and names the exact file where the problem is.

If any of this is absent, the commit is cut. If something inside the gate itself goes wrong — it also stops the commit rather than passing it silently. The logic here is simple: a silent failure would make every future failure silent.

The gate also notices module size, but here it only warns; it does not block.

## What is honest to say about its limits

The gate does exactly what is described — and honestly does not do the rest. This is important to understand.

**Judgment is not proof.** The gate proves that a judgment was rendered and recorded. It does not prove that the judgment is correct, that the contract is accurate, that the test works, or that the program is correct. These are fundamentally different things, and conflating them is dangerous.

**Two known limits that are not yet closed.**

First: there is a nightly check that makes sure the gate is alive and functioning at all. But it presents a very crude violator — one that is stopped by the very first check. If any one of the internal rules quietly breaks, the nightly check will not notice. This is an open place, and it is named out loud.

Second: in the system, several different places independently ask git what has been staged for the commit. They do this in different ways, and the inconsistency between them is a source of potential problems. The gate that watches for capability duplication does not see this particular class of duplication, by design of its model. The work of bringing these places to a uniform form has not started.

**Known bypasses are named out loud.** The gate does not conceal that it can be bypassed — for example, by writing a verdict formally or a call by name without a real check. This is not a bug in the gate; it is the boundary of what can be verified automatically at all. The meaningfulness of the judgment remains the human's responsibility.

## Where this lives in the system

The gate's logic lives in `project_context/dispgate.py`. Also there: index snapshot handling, sidecar verification, the call-by-name detector, and project policy processing.

The intent of the entire subsystem — why it is structured the way it is, what trade-offs were accepted, and what it principally does not undertake to do — is described in `subsystem_intent.yaml`.

A coherent account of how this decision emerged and what stood behind the choice of approach is collected in `docs/explanation/disposable_modules_refactor.md`.

<!-- translation-of: docs/explanation/multitenancy.md sha256:6c3056e3d679 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](multitenancy.md)

# Multi-tenancy: tenant isolation: why every patient has their own boundary

## What changed

- **The wording about digest gate behavior across different runtimes has been clarified.** The claim that the system blocks sending when a tenant verdict is absent has been adjusted — specific wording has changed, but the direction of the edit cannot be determined programmatically. Whether the guarantee became stricter or softer does not follow from the anchor; read this as a clarification, not as a strengthening or softening of any guarantee.

**Updated:** 2026-09-24

**Updated:** 2026-10-05


## Why it exists

Imagine: one computer, one application — but two patients living inside it. The device owner and their partner both keep medical records, both receive notifications, both histories stored side by side. Convenient. Also dangerous.

Medical data is perhaps the most intimate thing that exists about you. Diagnostic hypotheses, symptoms, patterns — all of it belongs to a specific person and to no one else. When that information accidentally reaches another patient, it is not a technical failure in the ordinary sense. It is a breach of trust that cannot be rolled back.

This has already happened: at one point, a partner's hypotheses were sent to the owner. Not through malicious intent — simply because code in one place did not ask "whose is this?" and instead took whatever was closest. The tenant isolation subsystem grew out of that specific lesson: defaults cannot be trusted, every piece of data and every secret must be explicitly tied to its patient.

## What it does, in plain terms

The subsystem holds one core rule: **nothing is taken "by default"**.

When a process wants to retrieve data or secrets for a specific patient, it must explicitly state whose. If it does not, the system does not guess and does not take "the first available." It stops loudly. Not quietly, not invisibly — loudly, so that it is impossible to miss.

This works in two directions.

**On the data side:** if the system is running in multi-tenant mode and a process has not specified which patient's data it is working with, it fails with an error. It does not continue operating on some random patient's data — it fails. Inconvenient? Yes. But incomparably better than a silent leak.

**On the secrets side** (tokens, chat identifiers, everything that allows the bot to send a message to exactly the right person): if the tenant's secrets folder is not specified explicitly, the system refuses — and does not fall back on another patient's secrets as a "backup option." A closed refusal is better than open access to the wrong place.

There is one more important principle, born from that same incident: the question "whose data is this?" and the question "what are the access rights?" are two separate questions, and they are kept separate deliberately. Previously they were mixed together: code determined the origin of the data and used that same determination to decide who to send what to. That conflation is exactly where the error hid. Now, answering "whose data" never opens access to another patient's tokens or secrets.

To verify that the boundary holds not only in theory, the system runs a sensor: it periodically scans both patients' databases looking for traces of one inside the other. If anything has leaked through — the sensor will notice and raise an alert, not stay silent.

Message delivery is also structured this way: every message takes its recipient from the tenant resolver — a mechanism that knows which chat identifier belongs to which patient. The system leaves no direct paths that bypass this mechanism.

**A separate note about files on the machine.** There is one boundary the system consciously does not close: if the partner sends the bot a path to a file on the shared machine, the bot may read the owner's file as well — because at the operating-system level it has the necessary permissions. This is a known and accepted boundary. Isolation holds on data and secrets inside the system, not on the computer's file system. People who share a machine trust each other at that level by definition — that decision was made by the owner.

## What is honest to say about its limits

Most of the rules described hold and have been verified. But one place remains honestly open, and it is important to say so plainly.

**The weekly digest and the boundary between runtimes.** When the system prepares the combined weekly text, it checks whether all tenants have given approval through their verdict files. If any tenant's verdict is blocked or corrupted, the text does not go to anyone. This is correct.

The problem arises in a specific configuration: since late September the owner runs in a container, and the partner runs on the host. These are two different runtimes, and they do not see each other's verdict files directly. The tenant from the other runtime simply does not place its verdict file where the system expects it. The system in this case does not wait and does not block — it moves forward. The decision was made by the owner consciously ("do not wait; if there are problems, we will deal with them"), but it means: full digest isolation between runtimes is not currently guaranteed. This is an open limitation, not a plugged hole.

Additionally, isolation at the machine's file-system level is not part of this subsystem and is not planned to be addressed by it. This is not an oversight — it is a boundary set by agreement.

## Where this lives in the system

The isolation rules live primarily in **`secrets_paths.py`** — it describes how the system locates the secrets folder for a specific tenant and what it does when it cannot find one (refuses, does not guess). The separation between "whose data" and "whose rights" is also established there.

The overall intent of the subsystem — why it exists, what problems it solves, and which principles are considered load-bearing — is recorded in **`subsystem_intent.yaml`**. This is not code; it is an explanation for people: why exactly this way and not another.

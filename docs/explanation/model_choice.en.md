<!-- translation-of: docs/explanation/model_choice.md sha256:93be1b9636c7 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](model_choice.md)

# Model Exit: One Guard, One Translator, Model Switch Only to a Verified One — Explanation

## What Changed

The meaning did not change; the page was regenerated.

**Updated:** 2026-11-08


## Why It Exists

When the system asks a language model something about health, it does so in 75 different places — recognizing test results, assembling a brief, preparing questions for a doctor, and much else. Each of those places is an exit point to an external server.

The problem is not that something will break loudly. The problem is that everything here breaks quietly.

A provider discontinues a model — and test results stop being recognized that same day. But there is no error, just silence. Or a provider silently substitutes the model with a fallback — responses keep coming, they look reasonable, but they misread a number from a form. Or the translator between formats decides that a truncated response is a finished one, and tasks simply vanish without a trace. Or a reasoning model starts thinking and spends more in a single request than the owner is prepared to spend in a month.

None of this is theory. Each of these things has either happened or was reproduced deliberately.

The model-exit subsystem exists because 75 independent exit points are 75 places where each of these quiet failures can happen in its own way. One exit — one place where everything can be kept under control.

## What It Does, in Plain Language

All 75 places in the system that need to ask the model something speak one language and go through one channel — `llm_client.guarded_client`. This is the only point through which requests leave.

**The guard stands first.** Before any text goes to the model — whether Anthropic or anyone else — it passes a secrets check. This happens before any translation and before sending, always, identically for all providers. Otherwise there would be a way to bypass the check through a different branch.

**One translator, ours.** Anthropic and other providers speak slightly different formats. There are multiple ways to translate between them, and the seam in that translation is where things get lost. So there is one translator here, written inside the system, and its seams are covered by our tests. In particular: if a provider truncated a response at a limit, the translator says so explicitly — in a way that makes it clear to anyone reading the code that the JSON may be incomplete. Otherwise data loss would look like success.

**Money is reserved before the call.** Before calling the model, the system estimates what it will cost on a pessimistic count. If it does not fit within the owner's monthly limit, the request does not go out at all, with an explanation. Not after the money is spent.

**A model is only switched to a verified one.** If the current model stops responding or behaves badly, the system can switch on its own — but only to the next model in the chain of admitted models. A model enters the chain only through admission, and only at the end. The order of the chain is the owner's decision; the system does not change it. If all models in the chain have failed — the chain stays as is; the system does not invent a replacement.

Admission is a separate verification procedure. For an external provider it runs only on synthetic data. The owner's own forms are sent during admission only to Anthropic — the same destination they go to in real operation. An external provider does not see them.

If a provider reports that one model responded, but the behavior clearly indicates it is a different one — this is counted as the required model being absent, not as success. This matters: otherwise the system would record a verdict from a different model, and then would not notice when the required model disappears.

**Two passes over test results — always different models.** Lab result photos are read twice, by two different models. This is not redundancy for its own sake — it is protection against one model consistently making the same kind of error. If the same model ends up on both passes from any source, the recognizer refuses on its own.

**A person's name does not go into the model's context.** Context builders — the parts of the system that gather information before sending it to the model — write age and location, but not name and not exact date of birth. The model judging health does not need a name. This does not resolve the matter fully — a name may appear inside the documents themselves or in the person's own words in the chat, and the model sees it there. But what the system assembles on its own does not contain a name.

## What to Say Honestly About Its Limits

Everything described above holds — in the sense that it has been verified and works. But "holds" and "verified everywhere" are different things, and it is important to state that difference honestly here.

**The admission table for a partner: lag between verification and release.** A partner installation and a GitHub installation do not run their own admission — they read the table written by a session on the owner's MacBook. Between the owner running admission and the partner receiving the updated table, there is a lag — until the next thread close. The system notices this lag and writes about it to the log, but does not close the lag itself. The guarantee holds — verified after deploy `0045cd9` — but only within the bounds of this lag.

**Name in context: the ratchet sees keys, not the full profile.** The protection against transmitting a name works like this: the system checks whether code accesses the name field by key. This means that if a profile is passed into a prompt in full — that path is caught only by a behavior test, and only for those builders listed in it. Beyond that list there is no guarantee. In addition, a name inside the document itself — a lab form, a discharge summary — and in the person's own words in the chat is still visible to the model. The rule applies to what the system assembles, not to the content of documents. Verified on the owner's database after deploy `7b4aa4c`: in the brief, GP header, reviewer, lifestyle profile, and consilium package — zero matches with name or date of birth. In chat context, two matches were found — both in calendar event titles, meaning in content.

These limits are not fine print. This is what the system knows about itself and states directly.

## Where This Lives in the System

All the subsystem's logic lives in `llm_client.py`. Also there: `_GuardedClient`, which wraps both the native Anthropic client and the external provider's client — the guard stands in one place for all of them.

The subsystem's intent — why it is structured this way at all — is described in `subsystem_intent.yaml`. This is not a technical file in the usual sense: it explains the decisions that were made, and why those decisions rather than others.

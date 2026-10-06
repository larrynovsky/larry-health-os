<!-- translation-of: docs/explanation/model_choice.md sha256:7a2eb4de2d93 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](model_choice.md)

# Exit to the Model: One Guard, One Translator, Model Switching — Only to a Verified One

## What Changed

The meaning did not change; the page was regenerated.

**Updated:** 2026-10-06


## Why It Exists

The system calls the language model in seventy-five different places — reading lab results, assembling a brief, preparing questions for a doctor, calculating request budgets. All these places are different, but in every one of them the same quiet failure can happen: something goes wrong, and nobody finds out.

Silent failures here have a particular shape. A provider takes a model offline — and lab results stop being recognized that very day. The person finds out first, not the system. A model is swapped for a fallback without verification — responses keep coming, look plausible, but contain errors: a number on the form is read incorrectly, and this is invisible to the eye. The translator between formats reads a truncated response as complete — a task list is lost, and no error is raised. A reasoning model counts money after the call, not before — and a single request jumps over the monthly budget.

This layer exists precisely because failures of this kind do not announce themselves by nature. They need to be caught in advance — by design, not by reaction.

## What It Does, in Plain Terms

Everything the system wants to say to a model goes through one exit point — `llm_client.guarded_client`. Not through several parallel paths, not through an external intermediary, but through one of its own.

**The guard stands first.** Before the request text goes anywhere — to Anthropic or to any other provider — it passes a secrets check. Uniformly, without exceptions. If a translator for an external provider stood before the guard, it would become a way around the protection. That is why the order is strict: guard first, then format translation, then transport.

**The translator is ours, one.** Anthropic speaks in its own message format; an external provider may speak in another. The translation is done by one translator of our own — not an external library, not an intermediary. This matters: if we do the translation ourselves, we also verify ourselves what it does with a truncated response. When a response is cut off at a provider's limit, the translator does not pretend the response is complete — it says "truncated", and readers in the system use that marker to distinguish incomplete JSON from complete.

**A model switches only to a verified one.** If the current model stops responding, the system switches on its own — but only to the next one in a pre-composed chain. A model does not simply end up in this chain: it goes through clearance — verification against real tasks. Only a model that has passed clearance can appear in the chain, and only at the end, not anywhere. The order in the chain is decided by the owner; clearance does not rearrange it.

The owner's own forms during the clearance process go only to Anthropic — to the same destination they go to in normal operation. An external provider during clearance sees only synthetics. If a role for an external provider has not passed clearance, it does not fail silently — the system refuses explicitly, with an intelligible reason.

**Money is reserved before the call.** Before a request goes to the model, the system checks whether it fits within the budget. For reasoning models, the reasoning reserve is accounted for as well. If it does not fit — the call does not happen, and the reason is stated directly.

**Name and date of birth do not go into the model's context.** Context assemblers — the brief, the visit header, the lifestyle profile, and others — write age and location, but not the name and not the exact date of birth. A model that reasons about health does not need a name. This does not mean the model cannot encounter a name at all — if the person wrote it in chat or it appears on a lab form, the model sees it. But the context assemblers do not deliberately place it there.

**Two passes — two different models.** Lab result photos are read twice: first by one model, then by another. If both models turn out to be the same one — the recognizer refuses to work on its own. A provider that does not have a second model for reading images is left without photo recognition — but does not get one model for both passes.

## What Is Honest to Say About Its limits

Everything described above holds. But "holds" and "verified everywhere" are different claims, and it is important to be honest here.

**The clearance table for partners and new installations.** When someone deploys the system without their own clearance — a partner, an installation from GitHub — the system appends models from the release table, which the owner compiles on their own machine, to the chain. This works and is confirmed by measurement. But between when the owner ran clearance and when the updated table reaches a specific installation, there is a delay. The system notices this and writes it to the log — but does not close the delay on its own. This is a boundary, not fine print.

**Name in the model's context.** The check is designed to see when a context assembler reads a name by known keys, and catches that. But if a name is passed inside a structure as a whole — for example, a profile is passed into a prompt as an object — that path is caught only by a behavior test, and only for the listed assemblers. If a new assembler appears, it needs to be checked separately. A name inside documents — on a lab form, in a discharge summary — and in the words of the person themselves in chat, the model still sees: the rule is about assemblers, not about content.

**Model substitution at an external provider.** Some providers silently serve an unknown model name with one of their own. The system catches this during a normal call and treats such a response as the absence of the requested model. But streaming calls are not covered by this check — because there are no streaming calls in the code at present. If they appear, the check will need to be extended to cover them.

## Where This Lives in the System

The central point is `llm_client.py`: this is where `guarded_client` lives, through which all calls pass, and where the guard and translator reside.

The intentions and decisions that led to this design — why one exit, why an in-house translator, why clearance before the chain — are described in `subsystem_intent.yaml`. This is not code and not an instruction; it is an explanation of why the system is built the way it is, and not some other way.

<!-- translation-of: docs/explanation/protocol_fit.md sha256:a65277ec2a71 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](protocol_fit.md)

# Will the new protocol fit into life — the owner's assistant answer in the protocol card: explanation

## What changed

- **An open question about the dialogue format has been removed.** The invariant `live_fit_format_unverified_open` has been deleted: the separate unresolved item "whether the specific request format to the assistant has been verified" is no longer tracked as a standalone issue.
- **A new invariant has been added: the assistant's answer in their own words reaches the card (`answer_in_own_words_reaches_card`, status holds).** This claim is now recorded — but within exactly the stated limits: that live words are passed to the card as-is, without classification.
- **A new open question has been added: end-to-end verification on the deployed system has not been completed (`live_answer_to_card_unverified_open`, status open).** It was found that an answer received before the mail parsing fix was applied would have landed in the card with a false "did not respond" label. The fix has been made, but live end-to-end verification remains open.

**Updated:** 2026-11-11


## Why it exists

When the system proposes a new protocol — say, taking something three times a day or doing something in the mornings — it relies on data: test results, metrics, history. But data does not know that the person travels every Tuesday, that they skip breakfast, or that their morning looks nothing like what any algorithm assumes.

This mismatch is not theoretical. A trial showed that the system might consider one and the same protocol perfectly reasonable — while the one who maintains the owner's calendar and talks to them every day immediately sees the conflict.

That "one" is the owner's assistant. Not a person, but a language-model bot that knows the schedule, habits, and trips. It is the assistant — not the protocol generator — who can say: "This does not fit into your life, and here is why."

The subsystem exists so that this voice reaches the owner before they make a decision about the protocol — not in a separate message, not off to the side, but directly inside the new protocol's card.

## What it does, in plain words

When a new protocol appears, the owner's assistant receives a question: will this fit into the owner's life? The assistant responds in their own words — the way they would answer someone they know well. That response lands in the protocol card that the owner sees.

It is important to understand what this response is and what it is not.

The assistant's response is a piece of evidence, not an action. It does not change the protocol and does not remove it. It does not vote and does not decide. It simply says what it sees — and after that, the owner decides.

The card arrives exactly once. Either with the assistant's response, or — if no response arrived within the allotted time — with a "did not respond" label. There is no third option: the card will not be held back, re-sent, or waited on indefinitely.

If there is no response at all — the assistant is unavailable, the message was not sent — the card is dispatched immediately, as it worked before, without delay. A message delivery failure is not silenced: the system signals loudly.

The assistant's response is visible not only to the owner at the moment the card is received. When the hypothesis that gave rise to the protocol is later evaluated by a council, the assistant's words are present there as well — attributed as the assistant's words, not mixed with other data.

One nuance lies in how the system recognises a response. The owner's assistant writes in natural language, in their own words. The system does not attempt to classify those words or parse them by rules — it looks at something simpler: whether the protocol number under open review is named in the message. If it is — that is a response, and the assistant's words go into the card as-is, without wrapping. A message without the number is not counted as a response.

## What is honest to say about its limits

It is important not to over-promise here.

**What is not working as intended right now.**

The main open item: that a live response from the assistant in their own words actually reaches the owner's card has not been verified on the deployed version. The response that arrived on November 11 was received before the mail parsing fix was applied. That means at that point it would have landed in the card with a false "did not respond" label — and this would have gone unnoticed. The fix has been made, but end-to-end verification on the live system has not yet been completed. This is an open question, not a resolved one.

**What the assistant does not do — and this is intentional.**

The assistant does not change the protocol. It cannot reject or adjust it. This is not a shortcoming or a temporary limitation — it is a deliberate design: the decision remains with the owner, the assistant only gives them information.

There is no separate echo of the conversation in Telegram — this is also a decision, not an omission.

**What the system does if something goes wrong.**

If the card was not sent on time — the nightly sensor turns red. This is not a silent failure. But the sensor signals a delivery delay, not anything about the content of the response — the system does not verify content.

## Where this lives in the system

The mechanics live in `protocol_fit.py`. That is where the response wait is held, where the sent flag is set only after sending has succeeded, and where the assistant's message is processed and their words are passed to the card.

The meaning and boundaries of why all of this is arranged this way are recorded in `subsystem_intent.yaml` — this is not code but an explanation of intent: what the subsystem is supposed to do, and what is intentionally outside its scope.

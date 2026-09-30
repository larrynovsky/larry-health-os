<!-- translation-of: docs/how-to/redo_onboarding.md sha256:41fffd44e2ca -->
**English** · [Русский](redo_onboarding.md)

# How to repeat onboarding or correct an answer

> Document type: How-to (Diátaxis). Where each answer is stored — [onboarding_answers](../explanation/onboarding_answers.md).

Send the bot:

```
/about
```

The bot will resume interrupted onboarding where you left off or start over.
Where an answer is already recorded, you will see a "Correct: …" button — press it if nothing needs to change,
or give a new answer.

- Stop onboarding: `/stop`. What you have already said stays recorded.
- Skip an optional question: the "Skip" button.
- You cannot ask your own question during onboarding: the bot will reply "Looks like a question…" and
  return to the current one. Ask after onboarding or interrupt it with `/stop`.
- A health problem is added to the problem list only after you use `/approve <номер>` on its
  card. To decline, use `/reject <номер>`.

You can also correct a single field in conversation: "I weigh 80 kg" — the bot will record your weight if the number
is present in your words.


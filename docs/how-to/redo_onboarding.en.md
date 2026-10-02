<!-- translation-of: docs/how-to/redo_onboarding.md sha256:3893e9878632 -->
**English** · [Русский](redo_onboarding.md)

# How to redo the introduction or correct an answer

> For people who use the bot. Where each answer goes (for developers) —
> [onboarding_answers](../explanation/onboarding_answers.md).

The "introduction" is the bot's first questions about you: name, date of birth, height, weight,
habits, chronic conditions, medicines, morning report time. From the answers the system works out
your norms and schedule.

## Continue or start over

Send the bot:

```
/about
```

- If you left the introduction halfway, the bot continues from the question where you stopped.
- If you already finished it, the bot starts again from the first question.

Where an answer is already saved, the bot shows it on a button: "Correct: 175". The answer is right
— tap the button and it stays. It is wrong — just type a new one: it **replaces** the old one rather
than being added to it. This way you can page through to the question you need with "Correct"
buttons.

- An optional question can be skipped with the "Skip" button.
- To stop: `/stop`. Everything you have answered, including corrections, stays saved.
- To start from the first question in the middle of an unfinished introduction: first `/stop`,
  then `/about`.
- In the middle of the introduction the bot does not answer your questions: it replies "That's a
  question for me. Please ask it again after the introduction" and repeats its own.

## Correct one value without the introduction

Write to the bot in a plain sentence, for example "my height is 178 cm" or "I weigh 80 kg". This
way the bot understands name, date of birth, sex, height, weight, language, smoking, alcohol,
coffee, diet, allergies and a few more habits. The sentence is read by a model, so it does not
always work. The reliable way is `/about` and a new answer to the question you need. You can see
all saved answers in the dashboard, in the "Profile" section.

## Conditions and medicines from the introduction

The bot does not add your answer about chronic conditions to the list of health concerns by itself.
It sends a card "I suggest updating your health concerns" with the buttons "✅ Apply" and
"✖ Reject" — tap the one you mean. Lost the card — send `/approve` and the bot sends all pending
cards again. All concerns with their numbers are in `/problems`.

This is the exception to "a new answer replaces the old one": conditions and medicines named in a
repeated introduction are added to the earlier ones. How to remove a medicine you no longer take is
not described yet. Remove a concern you no longer have in the dashboard: the "Problems" section,
the "remove from monitoring" button on the entry (it moves to the archive at the bottom of the page
and becomes "resolved").

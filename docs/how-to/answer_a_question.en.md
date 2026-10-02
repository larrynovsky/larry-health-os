<!-- translation-of: docs/how-to/answer_a_question.md sha256:1fe41fb46bf8 -->
**English** · [Русский](answer_a_question.md)

# How to answer a question from the system

> For people who use the bot. How it works inside (for developers) —
> [task_reminders_flow](../explanation/task_reminders_flow.md).

This is not for anything urgent: your answer waits for the next review. If you feel unwell now,
call your doctor or emergency services.

Sometimes the bot asks you a health question — for example, whether you still take a medicine or
how a doctor's visit went. The question arrives in Telegram as a separate message that starts like
this: "❓ Question #192: …". Each question has its own number.

## How to answer

Answer that exact message, not just at the bottom of the chat:

1. Press and hold the question message (on iPhone you can also swipe it left; on a computer,
   right-click the message).
2. Choose "Reply" — a grey strip with the start of the question appears above the input field.
3. Write your answer in your own words and send it.

The bot confirms: "✅ Your answer to question #192 is saved. I'll take it into account in the next
review." No such line — the answer is not saved.

Don't know the answer — say so, for example "I don't remember, I'll ask my doctor": that is an
answer too, and it closes the question. There is no hurry — the question waits as long as needed;
the bot sends no reminders.

## If you answered with an ordinary message

An ordinary message at the bottom of the chat does not count as an answer, especially a short
"yes" or "no". Sometimes the bot asks back "Is this your answer to question #192?" — then tap
"Yes". If it did not ask and did not write "✅ … is saved", answer again using "Reply".

## Answer with a command

If quoting is inconvenient, send the bot `/done`, a space, the question number without the hash
sign, a space and your answer, for example: `/done 192 the visit moved to next month`.

See all open questions and their numbers: `/tasks`. Each question there has a "Reply" button — it
sends the question again so you can answer it as above.

## Where the answer goes and who reads it

Your answer is read not by a human doctor but by the "family doctor" inside the system — the model
that writes the weekly and monthly review. The answer closes the question and is stored in the
database on the computer where the system runs. To take it into account, the text of your answer,
like everything you write to the bot, is sent to the model provider (Anthropic by default).

An answer that has been sent cannot be edited directly: write the bot an ordinary message about
what changed ("I no longer take …") — it goes into memory.

## If you don't want to answer

`/dismiss 192` closes the question without an answer. Note: if the same question comes up again in
the next review, the bot may ask it again.

You cannot close a question with a tick — in iPhone Reminders or on the system's page in the
browser: a tick does not carry your words, and the review needs exactly those.

<!-- translation-of: docs/how-to/night_repair_respond.md sha256:5cb658bb0623 -->
<!-- Hand translation (thread night-1314); form is judged by tests/consistency/test_doc_translations.py. -->

**English** · [Русский](night_repair_respond.md)

# How-to: a commit to main is blocked — handle the night repairs

A recipe for an agent session. A commit or a merge into `main` was refused with
«⛔ commit в main заблокирован: ночной ремонт ждёт решения сессии» (exit code 15). Here is what to do.
Why the lock works this way — the `night_decision_loop` record in `subsystem_intent.yaml`.

The owner takes no part in this chain: the session reads and handles the repairs. He gets only
what he alone can do or decide (an action on his phone, a change of a rule).

## See what is pending

```bash
python3.11 night_repair.py --gate; echo $?
python3.11 night_repair.py show <id>
```

`show` prints the card, the night author's diagnosis, the review and the patch. The author's and
the reviewer's words are hypotheses: measure whatever the decision rests on. Read evidence in the
owner's live database (the container), not in the frozen copy on the host.

## A repair of kind "code"

```bash
python3.11 night_repair.py land <id>
git commit
python3.11 night_repair.py close <id>
```

`land` runs the patch's tests on the stand twice — without the change (must be red) and with it
(must be green) — and stages the change in the main copy. `close` — after the commit has reached
Studio.

If the review says "rework", or the negative-control receipt was obtained by a run other than the
one it names, do not merge it as is:

```bash
python3.11 night_repair.py reject <id> "почему; доделывается в нити <имя>"
scripts/thread_start.sh <имя>
```

The patch stays on Studio in `~/health/night_repair/rejected/<id>/fix.patch` — take it as the base.
The card is closed by the thread's change, not by the rejection.

## A repair of kind "environment"

There is no change: `NIGHT_FIX.md` holds steps. Do them, then close with words — what was measured
and what was done:

```bash
python3.11 night_repair.py close <id> "что измерено и что сделано"
```

If only the owner can do a step, hand him one action in plain words, at once; in the closing words
name what remains with him.

## Check that the lock is open

```bash
python3.11 night_repair.py --gate; echo $?
```

Zero and empty output — `main` accepts commits from all sessions again.

## What not to do

- Do not bypass the lock with `--no-verify`: it stands by the owner's decision of 30.09.
- Do not close a repair with the author's words — only with your own measurement.
- Before `close` and `reject` re-read the list: there is no "taken by a session" mark, and a
  neighbouring session may have handled the same id.

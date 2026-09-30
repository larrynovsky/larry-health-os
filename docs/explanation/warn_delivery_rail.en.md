<!-- translation-of: docs/explanation/warn_delivery_rail.md sha256:008c672dfc40 -->
**English** · [Русский](warn_delivery_rail.md)

# The warn delivery rail: why it died for 13 days

> Diátaxis: **explanation**. Answers “why it works this way and what went wrong,” not “what to click.”
> Incident response: `HOWTO_warn_delivery.md`. Spine structure: `sensor_delivery_spine.md`.
> Written on 2026-07-26 following the incident of 2026-07-13…26.

## What happened

From July 13 to 26, 2026, not a single warning (`warn`) from `integrity_tests` reached the owner.
Thirteen days. Every sensor — including five validation-gate sensors built specifically so “a failure
will be noticed” — was detecting into a void.

Failure mechanics:

- `run_checks.sh --scheduled` (07:50) computes checks, writes `logs/integrity_latest.json`, and on
  `WARN>0` **deliberately does not send Telegram**. The code says exactly that: “No Telegram — triage_agent
  will handle it during morning_report (08:00).”
- `triage_agent.run_triage_guarded` was called from exactly one place: `morning_report.__main__`.
- On July 13, `morning_report` was removed from the schedule — its function (the morning brief) was transferred
  to `gp_agent.generate_daily_report`. The decision was correct and deliberate.
- But triage was a **passenger** on that carrier. The carrier left — and the passenger left with it.
  No one noticed, because a silent channel looks exactly like a channel with
  nothing to report.

## Error class: passenger dependency

Mechanism A lives inside mechanism B's schedule without its own pulse. When B is retired —
for entirely correct reasons and with a full understanding of what B does — A dies
silently, because no one is thinking about its presence inside B at that moment.

This is related to the “exception → silence” class that the fdr-online thread fought on July 25–26, but
more dangerous: there, one sensor was silent; here, the entire channel was silent at once — the **condition supporting
claims about all sensors**. Every “the sensor reaches Telegram” in the thread's snapshots since July 13 was
false, and neither of the two external review rounds caught it: the reviewer checked
`classify_warnings` as a function, and it worked — no one was calling it.

## Observability that imitated life

It is worth recording separately why a quick glance did not reveal the incident.

`logs/triage.log` showed fresh entries every day, including the day of investigation. They were
`FATAL: triage упал: RuntimeError('boom')` with a traceback through
`tests/unit/test_triage_delivery.py`. The unit test `test_run_triage_guarded_alerts_on_crash` actually
invokes the guard, and the guard writes to the shared production log. In other words, **the test fabricated daily signs
of life for a channel** that was dead.

Someone looking at `tail logs/triage.log` and seeing today's date concludes “it works.” I myself
nearly reached that conclusion. The real signs were `DONE:` and `triage_done_<дата>.flag`; the latest
were from July 13.

## What was done (2026-07-26)

1. **Its own carrier**: `com.larry.health.triage` (08:00) calls `triage_agent.py --send`.
   The owner's decision was a separate agent, not embedding it in `run_checks.sh`: cleaner
   responsibilities, at the cost of a new point of silent failure, covered by item 2.
2. **A guard for the rail itself**: `integrity_tests.check_triage_delivery_liveness` — if
   `integrity_latest.json` contains warnings and the latest `triage_done_*.flag` is more than two days old,
   this is a **FAIL**, not a warn. The level was chosen deliberately: a message about the rail's death cannot travel
   on that same rail — FAIL uses the 🚨 channel of `run_checks.sh` directly, bypassing triage. The independent
   terminus is the healthchecks dead-man (email); it already exists, so no new channel is being added.
3. **`__main__` calls the guarded version**: a channel failure must not be silent regardless of
   who starts it.
4. **The test no longer writes to the production log**: the path was extracted into the module constant `LOG_FILE`,
   and the test redirects it to `tmp`.

## What this teaches beyond the incident

The question to ask when retiring any mechanism: **who was riding inside
it?** Not “what did it do” — people usually remember that — but “what was called from its `__main__`, its cron
line, its handler.” Passengers are undocumented precisely because their presence seemed
like an implementation detail of the carrier.

And a second question: a delivery channel must have its own pulse, independent of what it delivers.
Otherwise, “detection without delivery = no sensor” turns from a principle into an inscription.

## Cadence by meaning: one basket for four questions (2026-08-31)

The rail delivered everything — and that was its second illness, the opposite of the first. The ten-line
morning message “your decision is needed” went unread for over a month, and by
the time it was read, one line was already false (the curator's proposals had been closed
the day before), four were engineering mechanics (units, producer registry, CVEs
in pip, the `lab_triage` queue), one was a dated state with no action (a probe
with no material), and only three were real choices for the owner. As long as this is one basket,
the person skips all four meanings at once (§13, banner blindness): delivery without
distinction is not delivery but noise with a receipt.

The owner's decision: a warning's class is WHAT it asks of the reader, and that
determines cadence. `decide` (the only oracle is a person: medicine, methodology,
canonical data) arrives daily as before; `fix` (engineering worklist) and `standing`
(dated state) arrive in a Monday digest as a separate message, without
the word “decision.” The default is `decide`: ignorance is not an exemption; a mute-list, not
a whitelist. The classification's home is `triage_agent.WARN_CLASSES`, by label substring:
`warn()` carries no class, and sensor names do not enter the artifact. The cost of this form is
a second copy of the label text; it is guarded by `test_warn_classes_match_live_labels`
(the substring must occur in sensor source code, or the class is dead).

The specific case that gave rise to the rule: the “nothing to assess” probe
(exit 3) had shouted daily since 12.08 by the owner's decision (option A) — 33 days
of one line with no action. It now has two labels: evidence younger than
`intent_registry.PROBE_STALE_EVIDENCE_DAYS` goes into the digest; older evidence arrives daily as
an overdue promise. The labels deliberately differ in text: cadence follows the substring.

## Two homes for one fact: how cadence woke a dormant defect (2026-09-07)

The cadence above did more than cure the basket — it woke a defect that had been in
the rail since July 29 and could not manifest before then.

A triage run left TWO traces. The marker `logs/triage_done_<дата>.flag` was set at
the end of every successful run: it answered “did triage run?” The receipt
`logs/triage_delivery_latest.json` was written inside `if needs_user` and carried the channel: it
answered “did delivery happen, and through what?” The rail sensor compared their dates
and, if the receipt lagged, said: “the delivery channel did not report for the latest
run.”

As long as decide warnings occurred daily, both traces were written daily and the reasoning
worked. Splitting cadence on 31.08 created a new kind of day — **quiet**: warnings exist, but
they are all `fix`/`standing` and go into the Monday digest, while the person has nothing to decide.
On such a day, the marker was set but the receipt was not, and the next morning the sensor reported
a channel failure to the owner that had not happened. There had been two quiet days since the receipt
was introduced (04.09 and 06.09), and both produced a false warning the next morning: 2 out of 2.
The frequency of this noise would grow in proportion to the system's recovery — the fewer substantive
decisions the owner had, the more often the sensor would shout that the channel was dead.

But something else was worse than the noise. The sensor contained

```python
assert stale_receipt or via not in (None, "", "none")
```

While the receipt was “behind,” the left operand was true — and the channel was not checked at all. In
other words, **the day after every quiet day, the guard for the final delivery step was disabled**:
both channels could go down, and instead of a FAIL through the 🚨 channel bypassing triage, the owner would receive
an ordinary warn through the very rail that was not working. Reproduced by execution before
the fix: the same state with `via='none'` did not produce a FAIL — the sensor returned
`delivered_via='none'` and stayed silent. This was the second time the guard for the final delivery step
had been neutralized: the first time (before 07.08), a test overwriting the production
receipt silenced it; now, its own false signal did.

The class is the same one that led to deleting `BLUEPRINT.md`: **two homes for one fact with different
write conditions diverge** (§15, §18). Formally, this is staleness deviation with an incorrectly
chosen boundary: the reader judged one replica's freshness by another's clock, although
that replica had a different pulse.

The treatment was not invented from scratch: the `owner_nag` bell had already solved the same problem correctly —
“always writes a pulse receipt, even when silent, otherwise ‘did not ring’ and ‘died’ cannot
be distinguished” (§14). Now the same applies here. The receipt is written EVERY run, BEFORE
the marker, and carries: how much should have been delivered (questions and digest separately), which
channel carried each, whether anything was sent at all (`sent` — a run without `--send` is honestly
marked), and the day of the last PROVEN delivery. The marker remains what it always essentially was —
an idempotency latch inside `run_triage` — and is no longer a judge.
There is now one artifact under assessment.

A separate gap opened by this construction was also closed: if proof of the channel
is required only when there is something to send, the channel can be dead for weeks and be discovered
on the first real warn. So `last_proven` ages on its own, and silence extending past
the last Monday digest (which guarantees delivery once a week; before
23.09 the threshold here was 9 days = a week + two days of margin; the owner's “general
rule” decision on 23.09 was an alert after the first missed delivery) is a FAIL, not a warn. The level
follows the same rule as the entire rail: a message saying “the channel may be dead” cannot
travel over that channel.

The lesson to take beyond this rail: **two traces of one event must
be written under the same condition — or one of them cannot serve as an oracle**. Checking
this is cheap: ask each artifact under what conditions it will NOT be written, and
compare the answers. If they differ and a reader compares the artifacts against each other, divergence
is not hypothetical — it is a matter of time.

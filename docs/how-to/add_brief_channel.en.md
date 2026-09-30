<!-- translation-of: docs/how-to/add_brief_channel.md sha256:8ae5651a211c -->
**English** · [Русский](add_brief_channel.md)

# How to add a new channel to the morning brief

You are adding a new source to `gp_agent.generate_daily_report` and have run into
`tests/consistency/test_brief_channel_registry.py`. Here is the procedure.

A channel is any block that appends to `context_lines`, meaning it puts something
into the morning brief's prompt. The registry does not forbid adding a channel without a repeat suppressor.
It forbids adding one **without noticing** that the suppressor is missing.

## Steps

**1. Place a marker above the block.** Put it one line above the top-level statement — the one
that contains `context_lines.append`:

```python
# channel: my_thing
try:
    context_lines.append(...)
except Exception as e:
    log.warning("...")
```

The marker is looked for on the line immediately above the statement (blank lines are skipped).
Use only lowercase characters matching `[a-z_]`.

**2. Add an entry to `gp_agent.BRIEF_CHANNELS`** with the same key and name the suppressor.
The value is a string, and it must answer the question “what will prevent this channel
from saying the same thing twice?”

**3. Run** `pytest tests/consistency/test_brief_channel_registry.py`.

## Which suppressor to choose

There are three valid answers. Choosing is the actual work; the rest is mechanical.

### “card gate: …”

The channel builds cards through `brief_pipeline.assemble_cards` and is filtered by `_shown`.
This fits when the content has a **stable claim identifier** — a `semantic_key` such as
`drift:rem_min:down`. Everything then already works: cooldown, bands, slots, and a receipt after
delivery.

Check yourself: can you write a key that will match today's key tomorrow
if it refers to the same thing? If you cannot, this is not a card channel.

### “custom: …”

The channel remembers what it has already said. Live examples:

- `location_signal` — a watermark that advances only after confirmed delivery;
- `sleep_absent` — a count of consecutive nights plus a threshold from the config;
- `chat_notes` — provenance: what the owner said is never retold.

This fits when “repeat” is defined by state rather than the name of a finding — crossing
a boundary, a sequence, or the source of a fact.

### “none: <reason>”

This is valid and often correct. But a reason is required and its length is checked by a test, because
an empty “none” turns a gap into the norm.

Valid reasons, all already in the registry:

- **the content changes by design** — `workouts` (yesterday's workouts),
  `step_target` (recalculated), `evening_checkin` (tied to a date);
- **this is an instruction to the model, not content** — `observation_window`, `missing_agents`:
  it does not appear in the brief's text, so there is nothing to repeat.

“Have not gotten to it yet” is an invalid reason. It makes a gap look like the norm. Instead, write
“none: debt, <link to task>”.

## What to do if there is no suppressor and no reason either

Add the channel with an honest “none:” and state what is missing in the reason. The registry will accept it,
and this is not sloppy work: the next person to open `BRIEF_CHANNELS` will see the full list of gaps.
This is exactly how `lifestyle_agents` is currently recorded — the brief's largest channel, with only
sleep covered by a gate.

Only silent absence is worse than an honest “none”: on August 2–4, the brief opened for three consecutive days
by retelling news that the owner had shared with the system, and there was nothing to detect it.

## What this guard does NOT prove

It proves that the question about a suppressor was asked and the answer recorded. It **does not** prove
that the suppressor works: you can write any string in the registry. This is the same class as
a disposability verdict (§15) — the quality of the judgment cannot be checked mechanically.

Other mechanisms check whether the suppressor works: `integrity_tests.check_brief_does_not_retell_owner`
on the delivered text, and unit tests for the specific channel.

## Related

- Why the suppressor runs after the LLM rather than before — [`docs/explanation/brief_repeat_boundary.md`](../explanation/brief_repeat_boundary.md)
- How the card gate works — `brief_gate.py`, `brief_state.py`
- Channel registry — `gp_agent.BRIEF_CHANNELS` (the single home; there is deliberately no copy in `docs/`)

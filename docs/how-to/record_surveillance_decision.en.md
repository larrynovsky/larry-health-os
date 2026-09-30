<!-- translation-of: docs/how-to/record_surveillance_decision.md sha256:b61987c6dcaf -->
**English** · [Русский](record_surveillance_decision.md)

# How to record a doctor's surveillance decision

When a doctor says “do not do X until Y” / “do X once a year,” record the decision.
Hypothetical failure: if a deferral is absent from the context, an agent may suggest
that examination again. The active decision must reach every reader that makes suggestions.

On Studio (`~/health_scripts`, health.db exists only there):

```bash
/opt/homebrew/bin/python3.11 -c "
import health_db as db
print(db.record_surveillance_decision(
    topic='example_imaging',                # topic slug — the previous decision is retired by it
    title='Examination X (hypothetical example)',
    decision='defer',                       # defer | do | stop
    decided_by='attending physician',
    source='owner_word:YYYY-MM-DD',         # provenance: owner_word:<date> | doc:<file> | encounter:<id>
    decided_on='YYYY-MM-DD',                # decision date; if not given — the record date
    valid_until='YYYY-12-31',               # valid until; None = indefinitely
    rationale='the doctor said not to do it this year'))"
```

Check what readers see: `python3.11 -c "import gp_context as g; print(chr(10).join(g._build_surveillance_decisions_block()))"`.
Expired decisions (`valid_until` in the past) automatically drop out of the active set — the topic is then open to agents again;
this is intentional: a doctor's decision has a time limit, not an eternal lifespan.


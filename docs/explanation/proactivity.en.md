<!-- translation-of: docs/explanation/proactivity.md sha256:4c8d630b5cd3 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](proactivity.md)

# Proactivity: a trend, not a spike — understanding the intent

## Why it exists

A system that stays silent until you ask is useful — but only when everything is fine and you're simply curious. The moment something starts going wrong is exactly when you least want to go check things yourself. You need a system that comes to you first.

But proactivity has a mirror-image trap. If the system nudges you at every rustle — every bad metric, every restless night — you start ignoring it very quickly. First you dismiss the notification, then you stop reading, then you turn it off. And on the day when the signal would have mattered, you won't see it.

That is why this layer exists: so the system speaks rarely, but to the point. So that an alert is a signal, not background noise.

## What it does, in plain words

The system does not look at today's number in isolation. It looks at it in comparison with what your normal picture used to look like — your baseline. One bad day is just a bad day. Life is arranged so that they happen.

An alert is raised only when bad days come in a row. Not a single dip, but a series. That is the difference between a spike and a trend: a spike is a random event, a trend is a direction.

There is another discipline as well: if there is little data — for example, the system has known you for only a very short time — it will not draw a picture of a multi-day trend where there is nothing to build it from. Silence is better than a confident conclusion invented from nothing. Up to roughly a week of observations, multi-day trends are simply not described.

In summary: the system is proactive, but restrained. It comes to you first — and for that very reason it tries to come only when there is something to say.

## What is honest to say about its limits

Something uncomfortable needs to be said here, and said plainly.

All of the logic around "when to raise an alert" rests on specific settings: how low a metric has to be to count as alarming, how many consecutive days constitute a series. These settings were chosen manually, on the basis of common sense and experience — but they have not been validated against real outcomes. No one has measured whether this particular configuration catches what matters to catch, or whether it creates unnecessary noise in the process.

This does not mean the choice is bad. It means it remains an assumption, not a proven optimum. The balance between "disturbing too often" and "missing something important" is an engineering judgement that has not yet been confirmed in practice. The task is open, and it is most honest to know that.

## Where this lives in the system

The logic that decides when and about what to speak first lives in `gp_agent.py` — this is the central agent through which the system's proactive activity passes.

The intent of the subsystem — why it is structured this way, what problem it solves, and what mistakes it tries to avoid — is described in `subsystem_intent.yaml`. The verifiable promises are recorded there as well, along with what is honestly marked as unresolved.

The overall architecture into which this layer is embedded, and how it relates to the other parts of the system, are described in `CLAUDE.md`.

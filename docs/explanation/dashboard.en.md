<!-- translation-of: docs/explanation/dashboard.md sha256:80390d16787e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](dashboard.md)

# Dashboard: a read-only window into the DB from outside the tailnet perimeter — how it works and why

## What changed

- **Added invariant `lan_ingest_write_only` (status: holds):** it is established that the LAN data-ingestion process operates write-only and serves no pages — it rejects browser requests. This is the boundary within which the claim is proven, no wider.

**Updated:** 2026-10-02


## Why it exists

The system has one place where everything is stored — a database with health history. But looking at the raw database is inconvenient and unsafe: something can be accidentally broken, data from different places will start to diverge, and it becomes unclear what to trust.

The dashboard solves exactly one problem: to provide a convenient way to *see* what is already in the database — and occasionally correct an individual record right here, without going anywhere else. It does not try to become a second source of truth. It is a window into the single source that already exists.

## What it does, in plain terms

**Reads from one database, writes to the same database.** The dashboard keeps no separate copy of state. When you open a page — it goes to the database, fetches from there, and displays it. When you edit something — it writes to the same place, through the same shared layer. This is fundamental: if the dashboard had its own copy, sooner or later it would diverge from the original, and it would become unclear which of the two versions is correct.

**The main page computes, it does not remember.** The "Getting Started" section, every time the page is opened, looks into the database and keys and recomputes the current state of each system capability. It does not store a flag "this section is configured". If data stops arriving — the page will see this on its own, because the source goes silent. If there is not enough data yet — the page will say so, because it looks at what is actually there.

**Does not start in write mode where it should not.** The system is designed so that the real, "primary" copy of the database lives on one device. If the dashboard were to be started somewhere else and began writing — the data would diverge through synchronisation, and the resulting confusion would be difficult to escape. Therefore the dashboard is structured so that if it is not where it is supposed to be — it stops immediately at startup, without waiting for something to go wrong.

**Accessible only inside the private network.** The dashboard does not expose itself to the open internet. It listens only inside a closed network (tailnet) — something like a personal VPN for its own devices. It has no public address. This is the perimeter boundary: not a password on a page, but the network itself.

**There is a separate process for receiving data from the phone.** When the phone sends data over the home network — this does not go through the dashboard, but through a separate process on a separate port. It accepts only data and serves no pages. It rejects browser requests. This separation is intentional: the read tool and the write tool live apart and do not interfere with each other.

**Lab result review is a special case.** There is one sanctioned path where the dashboard behaves somewhat differently: a reviewer reads data from a temporary intermediate store and, upon approval, moves it into the primary database. This is a deliberate agreement, not an accidental exception to the rules.

## What to honestly say about its limits

**The dashboard has no authentication — and this is an incompleteness that matters to understand.** The perimeter is the closed network itself: the dashboard listens only on an address inside the tailnet, with no public access. For family use this is a deliberate decision. But for medical data this is an assumption, not a guarantee. This means literally: whoever has found themselves inside this network sees everything, with no additional identity verification. This question remains open. The network perimeter should not be treated as adequate protection for medical data.

**LAN data ingestion runs without encryption.** Inside the home Wi-Fi, data from the phone is transmitted in the clear. And if the phone attempts to send data with the same address outside the home — that will also be without encryption. This is stated explicitly, as an accepted risk, not as an unnoticed gap.

## Where this lives in the system

The core dashboard logic lives in `dashboard.py`. The subsystem's intent — what it does and why — is described in `subsystem_intent.yaml`. The main page with its computed capability state relies on `getting_started.board` and the `methodology/getting_started.yaml` catalogue, which must cover every entry in the capability registry — or explicitly name the reason why a given capability is omitted.

The dashboard is not the centre of the system and not its brain. It is secondary by design: it reads what already exists, presents it in a convenient form, and is strict about not becoming a source of confusion where confusion is unacceptable.

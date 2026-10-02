<!-- translation-of: docs/explanation/install_path.md sha256:424bfe8aee58 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](install_path.md)

# Installation path: a tutorial that executes, and an image that the owner releases

## What changed

- **The boundary of the `tutorial_executes` invariant has been clarified.** It is now explicitly fixed: Telegram in CI is fake, real message delivery is not checked. The bot's path after a token failure — `/start`, onboarding, cards — is a separate invariant, not part of the tutorial run. This is a restriction, not an expansion of the check.
- **The `first_contact_executes` invariant has been added with status `open`.** The mechanism is described and the intent is recorded, but the check is not performed. There is currently no automatic confirmation that a person will go through bot onboarding without errors. When it appears, it will also have its own limits.

**Updated:** 2026-09-30

**Updated:** 2026-10-02


## Why it exists

When a person installs an unfamiliar program for the first time, they read the instructions — and trust that the instructions describe what is in front of them. Usually this is not the case. The instructions are written once, the code lives its own life, and by the time the third unfamiliar person opens the page, the service name is different, a key is missing, the template count is different. They follow every step, nothing works, they quietly close the tab. Nobody notices.

This subsystem exists precisely so that such a gap does not accumulate unnoticed. Not to make installation easy — that is a separate task. But to ensure that the tutorial and the real installer speak about the same thing, and that this can be verified rather than merely promised.

The second task is the image. An image is not just a build: it carries everything that lives in the public tree. It is a second publication of the same data, just in a different form. That is why who releases the image and when is not a technical question, but a question of what goes out and whose responsibility it is.

## What it does, in plain terms

**The tutorial carries a stamp.** The installation page contains a hidden marker — a hash of what the installer knows about itself: which services it creates, which keys it expects, what it configures by default. When any of this changes, the stamp stops matching. The test notices this and reports which page needs to be regenerated. The stamp prevents a code change from slipping past the document unnoticed.

**The tutorial executes.** Tutorial blocks that are specifically marked for checking are run in CI on a clean machine — exactly as written, with an image from the same code state. After this, the system must respond, the required services must come up, and the bot must reach the expected failure point on the fake token. If something is wrong — the tutorial is red.

**The image is released only by the owner.** The image is built by a separate process, only on the owner's command, only from the open repository, and only if the tutorial passed the check on that specific commit. The image is not published from the working repository. The package in the public registry also becomes public manually — not automatically.

**A newcomer installs from two files.** A ready-made image and docker compose — no cloning, no building. On Windows — via WSL2, and this is honestly marked in the tutorial as not checked.

## What to say honestly about its limits

It is important to distinguish three things here: what holds, what holds but is not fully checked everywhere, and what is not yet done.

---

**The installation page stamp holds — but it has a limit that must be named.**

The stamp says: "the page was regenerated after the installer's facts changed." It does not say: "the page text is correct." The correctness of the text is checked only by the tutorial run in CI — and only for marked blocks. A gap between the stamp and reality is possible where blocks are not marked.

In addition, the installer's facts are what the installer knows about itself: service names, keys, ports in the configuration. Whether a service is actually listening on a port after startup is outside the facts. That is checked by the run, not the stamp.

---

**The tutorial run holds — but only on Linux, only for marked blocks, and with restrictions.**

The Mac steps (Colima, launchctl) and the Windows/WSL2 branch are not run in CI. Windows is explicitly marked in the tutorial as not checked. Telegram in CI is fake: real message delivery, markup on a phone, and timings are not seen by the check. The bot's path beyond the token failure — cards, documents, usage scenarios — is a separate story, and the tutorial run does not touch it.

CI lives only in the open repository: in the owner's working repository the tutorial does not execute automatically — a different guard is there.

---

**First contact is what is not yet done, and this must be named directly.**

The intent: the user's first conversation with the bot — /start, onboarding, /help — should be run in CI just as automatically as the tutorial. The mechanism is described, the intent is recorded. But this invariant is currently open: it is not performed. This means there is currently no automatic check that a person will actually complete onboarding without errors on their own machine. When the check appears, it will also have its own limits: Telegram is faked, only one path through onboarding is checked, the "skip" and "repeat" branches are not run, the model is not called.

---

**The image holds — but carries the entire public tree.**

This is not just a build. Everything that has entered the public tree goes into the image layers as well. If something got there by inattention — it will go into the image too. The protection here is review and reading before publication, but not the build process itself.

Restriction by fork: the guard is a string in the workflow. A fork of the open repository with a modified workflow will build its own image under its own name. This will not occupy the name in the registry, but it should be understood that the mechanism goes no deeper than this.

## Where this is in the system

The installer lives in `scripts/install.py` — it knows about itself what goes into the stamp: services, ports, keys, volumes, default settings. The facts that the installation pages are checked against come from it.

The intent of the subsystem — what it should do and why — is recorded in `subsystem_intent.yaml`. This is not a technical configuration file, but a record of a decision: why this mechanism exists, what it promises, and where its boundary is.

The stamp, tests, tutorial run, and image release are all parts of one chain. They do not exist separately: a stamp without a run gives no confidence in correctness, a run without a green status gives no tag, a tag without the owner's command gives no public image. The chain is intentionally not fully automated — the last step is always human.

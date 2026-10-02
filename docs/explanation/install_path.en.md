<!-- translation-of: docs/explanation/install_path.md sha256:ef6203b9114e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](install_path.md)

# Installation path: a lesson that executes, and an image that the owner releases

## What changed

- **The `first_contact_executes` invariant has moved from `open` to `holds`.** The first-contact check with the bot now runs automatically — the script walks the `/start` path, the onboarding flow, and the handlers exactly as a real person does on first launch. This is a confirmed status change, not a restatement.

**Updated:** 2026-10-02


## Why it exists

Installation documentation ages quietly. A developer renames a service, adds an environment variable, changes something in the configuration — and the lesson stays as it was. A new person downloads the files, follows the instructions step by step, and somewhere in the middle hits an error that is explained nowhere. Most of the time they simply leave.

This subsystem exists precisely so that does not happen — or at least so that a divergence between the lesson and the installer does not remain invisible.

The second reason is tied to the system image — a packaged snapshot that can be downloaded and run. The image is public, which means everything that ends up in the project tree ends up in it too. This calls for particular care: not every commit should become a public release, and a release must not happen automatically or without an explicit decision by the owner.

## What it does, in plain terms

Imagine the installation lesson is not just text, but text with a stamp. The stamp is a short fingerprint of the facts the installer knows about itself: which services it starts, which ports it uses, which environment variables it expects, which recognition languages are connected by default. If any of that changes, the fingerprint changes too — and the system immediately notices that the lesson was not updated. The developer sees a warning and the command that will refresh the page. The lesson cannot silently diverge from the installer: the divergence becomes visible before any outsider sees it.

But an updated text is still not a guarantee that it is correct. That is why some blocks in the lesson are marked in a special way: those exact steps are executed as-is — in a clean environment, automatically, on every code change. If a block does not pass, the lesson is considered broken.

Next comes the first contact with the system through the bot. That path is also checked automatically: a script walks the same steps a real person would walk on first launch — through the same handlers, the same owner filter, the same error handler. Telegram is simulated: there is no real network, but the logic is the same.

Finally, the public image. It is built only on an explicit command from the owner, only from the public repository, and only if the lesson passed all checks on that commit. The image is not published on its own — not on branch merges, not on a schedule.

## What is honest to say about its limits

Every boundary needs to be named directly here, not buried in fine print.

**The facts stamp checks that an update happened, not that the content is correct.** If the lesson was updated after the facts changed — the stamp is satisfied. But whether the text itself is accurate, the stamp does not know. That is checked only by running the marked blocks in CI — and only for those blocks.

**The facts are what the installer knows about itself.** Whether a service is actually listening on the right port after startup, whether the container behaves as written — that is outside the facts. The stamp makes no judgment about that.

**Only marked blocks are executed, and only on Linux.** The Mac steps and the Windows/WSL2 branch are not run automatically. Windows is explicitly marked in the lesson as unverified. This is an honest label, not an oversight: those paths are simply outside automatic checking right now.

**Telegram is simulated in the check.** Message lengths, button behavior, delivery to a phone, races between user actions — none of that is visible to the check. Real Telegram is not used in CI.

**Onboarding is checked along one path.** A typical first launch, the first button, ordinary text — that is covered. The "skip" branch, a question mid-onboarding, repeating the command — no.

**The image carries everything that ended up in the public tree.** If something slipped past the content check, it will travel into the image layers as well — a second publication of the same thing. Protection against an accidental image exists; protection against accidental content is a separate task.

**The image name protection in the registry is a line in a file, not a lock.** A fork of the public repository with a modified build file will build its own image under its own name. It will not be able to claim the owner's name in the registry — but this is worth understanding.

## Where this lives in the system

The installer — `scripts/install.py` — knows everything about itself that is needed for the stamp: services, ports, volumes, environment variables, OCR languages, startup flags. It is the source of facts against which the lesson text is verified.

The intent and limits of the subsystem are described in `subsystem_intent.yaml` — alongside the descriptions of the other parts of the system. This is not configuration and not code: it is a record of what the subsystem is supposed to do and why in exactly that way.

Image building, lesson execution, and the first-contact check live in the automated build files and are not run manually by the owner on every change — they run on their own, and it is their result that decides whether a new release can go out.

<!-- translation-of: docs/explanation/install_path.md sha256:8cdedb9a27c9 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](install_path.md)

# Installation Path: a Tutorial That Runs, and an Image That the Owner Releases

## What Changed

First version of this section.

**Updated:** 2026-09-30


## Why It Exists

Documentation breaks quietly. The owner renames a service or adds a new key to the configuration — and the installation tutorial stays the same. Nobody shouts. Nobody notices. The first person to see it is a stranger on an unfamiliar machine: they follow the instructions, something doesn't add up, and they silently leave.

This subsystem exists precisely so that the gap between "what is written" and "what works" does not accumulate unnoticed. Not because someone is insufficiently careful, but because such a gap is a natural property of any living system if it is not deliberately tracked.

The second problem is the image. When a system is packaged into an image and published publicly, it is not merely a convenience for a newcomer. It is a second publication of everything that lives in the repository: every file from the tree ends up in the image layers as well. That means the image demands the same deliberate decisions as the repository itself — and it must be released by the owner, intentionally, not by automation under some accidental set of circumstances.

## What It Does, in Plain Words

The subsystem holds three things at once.

**The tutorial is synchronized with the installer.** The installer is a script that knows the truth about itself: which services it starts, which keys it expects, exactly what it does. From this truth a control stamp is computed — a mark that is embedded directly into the tutorial page. If anything in the installer changes, the stamp stops matching, and this is immediately visible: tests go red and say plainly that the page needs to be updated. The stamp does not check whether every paragraph is written correctly — it checks that the page was regenerated at all after the changes. Correctness of the text is checked by the next mechanism.

**The tutorial runs exactly as written.** Parts of the tutorial that are specially marked for verification are literally executed in a clean environment — as if a newcomer who has just downloaded two files is sitting at the keyboard. After that the system must respond: the dashboard opens, the services are running, the bot reaches a predictable failure on a fake key. If anything at all is wrong — the release does not happen. The image does not receive a tag until the tutorial has passed on the exact code from which the image is built.

**The image is released only by the owner, only from the public repository.** Building and publishing the image is triggered exclusively by an explicit command from the owner, on a tag, and only from a specific public repository. This is not merely a convention — it is a verifiable condition embedded in the build process. The image does not go to the public registry from the working repository.

## What Is Honest to Say About Its Limits

All three promises currently hold. But "holds" and "verified everywhere" are different claims, and it is important not to conflate the two here.

**The stamp judges the fact of an update, not the correctness of the text.** It sees that the page was regenerated after the installer's facts changed. It does not see whether every step is described correctly. Correctness of the steps is the job of the tutorial run — and only for marked blocks. Furthermore, the facts are what the installer knows about itself; whether the service is actually listening on the right port in a specific environment is outside the stamp's scope.

**The tutorial runs only on Linux, only marked blocks.** The Mac steps and the Windows/WSL2 branch are not run in CI. Windows is explicitly marked in the tutorial as not verified. The bot's path after the Telegram failure — onboarding, working with cards — is also not run in CI: only the fact of failure on a fake token is checked. Also: CI lives in the public repository; in the owner's working repository the tutorial is not run automatically — the stamp and tests work there, but not the full run.

**The image carries everything that made it into the public tree.** If something entered the repository unnoticed — through transcription or reading — it will appear in the image layers as well. This is not a separate vulnerability of this subsystem, but a property of publication as such: the image is a second copy of the same thing. Finally, the protection against a third party building an image under our name is a line in the build file; a fork with a modified file will build its own image under its own name in the registry, but cannot take our name.

## Where This Lives in the System

The actual truth about the installer is held by `scripts/install.py` — it computes what are called the installation facts: services, ports, keys, volumes, and so on. From these facts the stamp is born, and it lives in the tutorial pages.

The intent and boundaries of the entire subsystem are described in `subsystem_intent.yaml` — a declaration of what the subsystem promises and why it is structured the way it is.

The connections to the rest of the system are direct: the facts stamp is built on the same logic as the intent stamps in other subsystems — a unified way of tracking the freshness of living documents. The tutorial in CI is part of the same chain that guards the release: the image does not go out into the world until the tutorial has passed on that exact code.

<!-- translation-of: docs/how-to/release.md sha256:9a503ae0d0f3 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](release.md)

# How to release a new image version

> **Document type:** How-to (Diátaxis). For the owner. Release — only on their command
> (decision 30.09): a new version immediately reaches everyone who updates via the tutorial.

A release is a tag `vX.Y.Z` in the public repository. The tag triggers `.github/workflows/release.yml`:
the image is built on GitHub's native runners for amd64 and arm64, pushed to `ghcr.io` under the version
number and `latest`, and the GitHub release receives three files that the tutorial downloads: `compose.yaml`
(with this image's name), `health.env`, and the Colima auto-start agent.

## 1. Push and wait for the tutorial

```bash
cd ~/health_scripts
python3.11 scripts/public_mirror.py --push
```

The push triggers `tutorial.yml` on GitHub: the first-install tutorial runs on a clean machine
with the image from this commit. Wait for a green run:
`gh run list --repo larrynovsky/larry-health-os --workflow tutorial.yml --limit 1`.

## 2. Release

```bash
python3.11 scripts/public_mirror.py --release v0.1.0
```

The command will refuse if the mirror clone does not match GitHub, if such a tag already exists, if the tutorial
on this commit is not green, if CodeQL has not yet analyzed this commit, or if there are open findings
of critical/high severity on it, or if Dependabot has an open dependency vulnerability of the same severity
(owner decisions 01.10). A false finding is dismissed on the Security tab with a reason — it will then not
block the release; a vulnerable package is updated per `dependency_updates.en.md`. Then it waits for `release.yml` (about half an hour, arm64 takes the longest) and
verifies the release files. The version number cannot be overwritten: made a mistake — release the next one.

## 3. First release: make the repository public, then the package

The tutorial fetches release files from the repository page and the image from the GHCR package; if either
is private — the tutorial fails for others (files — 404, image — `denied`). Only the owner can make them public,
manually, in this order:

1. Repository: Settings → Danger Zone → Change visibility → Public. Reversible.
2. Package: GitHub → Your profile → Packages → `larry-health-os` → Package settings → Change
   visibility → Public. **Irreversible**: GitHub does not allow making a public package private again. That is why it goes second —
   once the public repository has already been reviewed.

Verification — the tutorial as a new user would experience it, without being logged in to GitHub and GHCR, on a machine with no local copy of the image:
`docker logout ghcr.io; python3 scripts/tutorial_run.py docs/tutorials/first_install.md --release`.

## If settings have changed

The tutorial on update downloads only `compose.yaml`: a person's `.env` stores their time zone.
If the composition of `.env` has changed in the release (a new key), write this in the GitHub release description
(`gh release edit v0.1.1 --notes …`): the tutorial instructs the user in that case to re-download `.env`.

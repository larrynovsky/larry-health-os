<!-- translation-of: docs/how-to/release.md sha256:f9fc778d3d09 -->
**English** · [Русский](release.md)

# How to release a new image version

> **Document type:** How-to (Diátaxis). For the owner. A release happens only on the owner's
> command (decision of 30.09): the new version reaches everyone who updates by the tutorial.

A release is a `vX.Y.Z` tag in the public repository. The tag starts
`.github/workflows/release.yml`: the image is built on GitHub's native runners for amd64 and
arm64, goes to `ghcr.io` under the version number and `latest`, and the GitHub release gets the
three files the tutorial downloads: `compose.yaml` (with this image's name), `health.env` and the
Colima autostart agent.

## 1. Export and wait for the tutorial

```bash
cd ~/health_scripts
python3.11 scripts/public_mirror.py --push
```

The export starts `tutorial.yml` on GitHub: the first-install tutorial runs on a clean machine
with the image from this commit. Wait for a green run:
`gh run list --repo larrynovsky/larry-health-os --workflow tutorial.yml --limit 1`.

## 2. Release

```bash
python3.11 scripts/public_mirror.py --release v0.1.0
```

The command refuses if the mirror clone differs from GitHub, if the tag already exists, or if the
tutorial on this commit is not green. Then it waits for `release.yml` (about half an hour, arm64
takes longest) and checks the release files. A number is never rewritten: if you made a
mistake, release the next one.

## 3. First release: make the package public

While the GHCR package is private, only the owner can pull the image — everyone else's tutorial
fails with `denied`. Only the owner can open the package, once, by hand: GitHub → Your profile →
Packages → `larry-health-os` → Package settings → Change visibility → Public. Check without
logging in to GHCR: `docker logout ghcr.io; docker pull ghcr.io/larrynovsky/larry-health-os:v0.1.0`.

## If the settings changed

On update the tutorial downloads only `compose.yaml`: a person's `.env` holds their time zone.
If the release changed what `.env` contains (a new key), say so in the GitHub release notes
(`gh release edit v0.1.1 --notes …`): the tutorial tells people to download `.env` again then.

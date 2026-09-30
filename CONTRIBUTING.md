# Contributing

Start with an issue for anything bigger than a typo or an obvious bug fix, so we
can agree on the direction before you spend time on it. Onboarding offers Russian or English
for the bot's own interface texts; model-generated prose remains Russian. The README,
installation tutorial and several guides have English versions; other documentation remains
Russian-only. English contributions are welcome.

## How a PR gets in

The public GitHub repository is a one-way mirror of the author's private working
copy. PRs opened here are imported with Copybara and go through the full private
test suite and checks. Accepted changes land in the private copy and come back
out in the next sync. Nobody commits directly to the public repository.

Here, "merged" means accepted into the private copy; the change appears on GitHub
with the next sync. Expect review within about a week. "No" is a normal answer,
and the maintainer will explain why.

## Medical changes and privacy

Changes to a clinical threshold, a doctor or specialist prompt, a nutrition rule,
or anything that changes medical conclusions are a separate class: cite the
source guideline or document **with its version**, and get the maintainer's
**explicit approval** before the change is accepted.

**Never attach real medical data to issues or PRs** — including lab results,
genomes or doctors' letters. Replace names, dates and values with invented ones,
including in error logs, code, tests and fixtures.

## Checks and local tests

After import, PRs are checked by the project's guards: a personal-data census on
every commit, a disposability sidecar contract for new modules
([docs/how-to/disposability_gate.md](docs/how-to/disposability_gate.md)), and contract tests. You don't need to master these
upfront; the maintainer will help you through them.

On macOS with Apple Silicon and Homebrew Python 3.11, run these commands from the
repository root (from [README's Installation section](README.md#installation)):

```bash
/opt/homebrew/bin/python3.11 scripts/clean_clone_probe.py   # fresh install in a temp directory
/opt/homebrew/bin/python3.11 -m pytest -q                   # tests, about five minutes
```

Tests marked `owner_data` are skipped in the public copy because the author's
private data is absent. Tell us what you ran and what passed or failed.

## License

Inbound = outbound: contributions are accepted under [Apache-2.0](LICENSE).
There is no CLA. See [NOTICE.md](NOTICE.md) for third-party terms.

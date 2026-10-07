<!-- translation-of: docs/how-to/publish_mirror.md sha256:5c23235d8341 -->
**English** · [Русский](publish_mirror.md)

# How to update the public repository and accept a change from it

The working repository is closed, together with its history. The public repository
`github.com/larrynovsky/larry-health-os` is a mirror of its public zone: everything not listed in
`publication_zones.yaml`. Each export is one commit, "Export of <sha>".

## Export

```bash
cd ~/health_scripts
python3 scripts/public_mirror.py            # build and commit into the mirror clone
python3 scripts/public_mirror.py --push     # same, then push to GitHub
```

The committed state of `main` is exported: uncommitted and private files do not leave, and files
deleted in the working repository are deleted in the mirror too. The mirror clone lives in
`~/.public_mirror/larry-health-os`.

`--push` refuses until every public file changed after the read mark (`plans/publication_read.txt` in the private part)
has been read: the project's prose easily keeps a "measured on live data" trace. The order:

```bash
python3 scripts/public_mirror.py --unread           # what to read: changed public files
python3 scripts/public_mirror.py --mark-read main   # after reading and fixing; commit the mark
```

Lines that look like a live measurement (an event date or a blood-pressure pair next to a body word) are printed
first by `--unread`. `--mark-read` refuses while they are there: remove the live data or, having reviewed each one,
repeat with `--cleared N`, where N is their count. The highlight catches form, not meaning: it misses a number
without a date, so everything still has to be read.

The mark is judged by the version committed in the exported commit, not by the file on disk: an uncommitted mark does not open the push.

Carry an outside contribution (pull request) into the working repository as your own commit with a
`Co-authored-by: Name <id+login@users.noreply.github.com>` line. An export is a single "Export of"
commit, and it appends the co-authors from history that the mirror does not have yet. Only GitHub
noreply addresses are taken: a private address in a co-author line does not reach GitHub.

Before `--push`, also run a control search for the words of your own medical history: kinds of
treatment, procedures, places. The guard and the `private/pii_terms.yaml` dictionary catch known
words, not structure. On 29.09 a test fixture held personal medical details under
invented names: the census found 0, and twenty-one readers missed it (lesson C-93). Judge each
match by its structure — does it look like your history — not by whether "real" or "measured"
appears next to it.

```bash
git grep -n -i -E "химио|лучев|облуч|операц|рецидив|chemo|radiat|surgery|relapse" main -- tests/
```

## Accept a change from the public repository

An outside change arrives as a pull request to the public repository. It must not be merged
there: the next export would overwrite anything the working repository does not have. There is
one path, through the working repository:

```bash
cd ~/health_scripts
scripts/thread_start.sh pr-<number>
cd ~/.worktrees/health_scripts/pr-<number>
gh pr diff <number> --repo larrynovsky/larry-health-os | git apply --3way
```

From here it is like any change of your own: tests, a commit that credits the author
(`Co-Authored-By: name <email>`), closing the thread. After the export, close the pull request on
GitHub with a link to the "Export of …" commit that carried the change.

## If something goes wrong

| What you see | What to do |
|---|---|
| "the mirror already matches" | Nothing to export: `main` has no new public changes |
| "⛔ … not read after the mark" | Read the files from `--unread`, remove live-data traces, commit, set `--mark-read` |
| `git apply` does not apply the change | The author wrote it against an older export. Ask them to update the branch, or carry it over by hand |
| A file that should not be there ended up in the mirror | Add its path to `publication_zones.yaml` with a reason, commit and export again. It will not disappear from the public repository's history — if it is personal data, delete the repository and export again |

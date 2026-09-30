<!-- translation-of: docs/how-to/two_machine_setup.md sha256:8f9d4eaaedb2 -->
**English** · [Русский](two_machine_setup.md)

# How to add a second machine for development

> **Document type:** How-to (Diátaxis). For you if you have already completed
> the [first installation](../tutorials/first_install.md) on the primary machine and want to write code on
> another machine (a laptop), while the system continues to run on the primary machine.
> Why it works this way: [git_architecture](../explanation/git_architecture.md).

**The rule everything else depends on:** data and the running system live on ONE machine —
the primary machine. The second machine writes code and commits; the commit automatically goes to the primary machine
and is applied there. There is no database on the second machine, and there must not be one.

## 1. Name the primary machine

In `private/infra.yaml` on both machines, set the same `primary_host` — the primary machine's name
(as printed by `hostname`). The code uses it to decide where it may write to the database
(`infra_config.is_primary`), and the hook installer uses it to decide which hooks to install.

## 2. Allow the primary machine to receive pushes into the working tree

On the primary machine, in the repository directory:

```bash
git config receive.denyCurrentBranch updateInstead
```

Without this, git rejects pushes to the currently checked-out branch.

## 3. Clone the repository on the second machine

```bash
git clone <user>@<primary>:health_scripts ~/health_scripts
cd ~/health_scripts
git remote rename origin studio
```

The remote name `studio` is deliberate: the post-commit hook sends code there and gets the primary
machine's address from that remote; there is no second copy of the address.

## 4. Prepare the private dictionary on the second machine

Cloning does not bring over `private/pii_terms.yaml`: it is excluded from the public export
and from git in an installation created by the installer. Before the first commit, add
`/private/pii_terms.yaml` to this clone's local git exclusions, then copy the dictionary from
the primary machine over a private channel (for example, SSH). Keep it out of commits,
issues and chat messages; restrict its file permissions to `600`.

If you have no dictionary to transfer, create an empty one instead. From the repository root:

```bash
printf '%s\n' '/private/pii_terms.yaml' >> "$(git rev-parse --git-path info/exclude)"
mkdir -p private
test -e private/pii_terms.yaml || printf 'literals: {}\npatterns: {}\n' > private/pii_terms.yaml
chmod 600 private/pii_terms.yaml
```

`pii_census.py` refuses a commit if the file is absent. It accepts the empty mappings above,
but then has no personal literals or patterns to detect. Copy the populated dictionary
when available, or maintain your own private entries. A zero-byte file is not a valid empty
dictionary.

## 5. Install hooks on both machines

On each machine, from the main repository copy (not a task worktree):

```bash
scripts/install_hooks.sh
```

The script detects where it is running. On the primary machine, it installs a push-receive hook that
reinstalls hooks from the repository after every push. On the second machine, it installs pre-commit checks and a post-commit
hook: `git push studio main` and a restart of the bot and dashboard on the primary machine.

## 6. Verify

Make a no-op documentation edit on the second machine and commit it. Expected result:

- The commit output includes a line about the push to the primary machine.
- On the primary machine, `git log -1` shows that commit.

If the commit stays only on the second machine, check `git remote -v` (step 3) and
`receive.denyCurrentBranch` (step 2).

## Tests on the second machine

There is no database here, so tests redirect data to a temporary directory themselves (`tests/conftest.py`).
Run the full suite on the primary machine: [run_tests](run_tests.md). For parallel work on several tasks,
use separate worktrees: [thread_worktree](thread_worktree.md).

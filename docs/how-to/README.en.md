<!-- translation-of: docs/how-to/README.md sha256:e785e14d36c1 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](README.md)

# How-to guides: where to start

> **Document type:** How-to (Diátaxis), landing page. There are about fifty guides here, and they
> are written for three different readers. Find yourself below — you can skip the other lists.

If you installed the system by following the [first-install tutorial](../tutorials/first_install.md), you do not have a clone of
this repository — only a `~/health-docker` folder with `compose.yaml`. Most of the guides
below were written earlier, for a clone-based installation with macOS services (`launchctl`).
Until they are rewritten, translate commands using the single rule below.

## Command translation rule for image-based installs

| In the guide | What you have |
|---|---|
| `cd ~/health_scripts` | `cd ~/health-docker` |
| `python3.11 <script>.py …` | `docker compose exec cron python3 <script>.py …` |
| `launchctl kickstart -k …health.bot` (and other services) | `docker compose restart bot` (`dashboard`, `lab-intake`, `cron`) |
| `launchctl list \| grep health`, plists in `~/Library/LaunchAgents` | `docker compose ps` — no plists, the schedule lives inside `cron` |
| service logs `~/health_scripts/logs/…` (`bot_err.log` etc.) | `docker compose exec cron tail -n 50 /app/logs/<file>` |
| task logs `~/health/logs/…` | `docker compose exec cron ls /home/health/health/logs/` |

`docker compose logs` is nearly empty: services write logs to files, not to container stdout.

Code in the container lives at `/app`, and that is where commands are executed: scripts referenced in the guides
exist under the same names there (verified on 01.10 against a clean install from `v0.1.0`). The rule does
not cover Git, tests, or editing code — those require a clone; see the third list.

## 1. You use the system through the bot

No terminal needed.

- [Reply to a system question](answer_a_question.md)
- [Redo onboarding or correct an answer](redo_onboarding.md)
- [Send the bot a large file](send_large_file.md)
- [Connect an Oura ring](connect_oura.md)
- [Connect iPhone Health and Apple Watch](connect_apple_health.md)

## 2. You maintain your own installation

A terminal on the machine where the system is installed is required. Use the rule above for commands.

- Installation and updates: [install the system in Docker from an image](install_docker.md)
- Respond to system alerts: [night cycle alert](night_cycle_respond.md) ·
  [escalation "a person has hit a problem"](service_trouble_alert.md) ·
  [check fired but no message arrived](diagnose_silent_check.md) ·
  [guard blocked a model call](llm_guard_blocked.md)
- Lab results: [recognition pipeline](lab_pipeline.md) · [row review queue](lab_review_queue.md) ·
  [lift quarantine on a pair](adjudicate_quarantine.md) ·
  [defer or close a question about a reference range](record_analyte_norm_verdict.md) ·
  [record a physician's surveillance decision](record_surveillance_decision.md)
- Data and content: [health constitutions](update_constitutions.md) ·
  [long-form analysis](run_analysis.md) · [food rules](reseed_food_rules.md) ·
  [seasonal table](reseed_seasonal_produce.md) · [trail list](refresh_trail_list.md)
- Channels: [email](email_channel.md) · [weekly digest](weekly_digest.md) ·
  [log rotation](rotate_logs.md)
- **Not ported to Docker, the rule will not help:** [add a second person](add_person.md)
  (a second person is not yet described for image-based installs) and
  [migrating from a native install to a container](pilot_switch.md) (only for those who installed
  the system before the image existed).

## 3. You are developing the system

A clone is required: `git clone https://github.com/larrynovsky/larry-health-os.git`. All
remaining guides are here: [Git](git_workflow.md), [tests](run_tests.md),
[failed nightly run](handle_test_failure.md), [second machine](two_machine_setup.md),
[worktree for a thread](thread_worktree.md), [dashboard](extend_dashboard.md),
[domains](add_domain.md), [questionnaires](add_instrument.md), [dependencies](dependency_updates.md),
[documentation](update_docs.md), [image release](release.md),
[public mirror](publish_mirror.md), and others in this folder.

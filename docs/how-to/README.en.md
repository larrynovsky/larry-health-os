<!-- translation-of: docs/how-to/README.md sha256:b821da81d96b -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](README.md)

# How-to guides: where to start

> **Document type:** How-to (Diátaxis), landing page. There are about fifty guides here, and they
> are written for four different readers. Find yourself below — you can skip the other lists.

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
not cover Git, tests, or editing code — those require a clone; see the fourth list.

## 1. You use the system through the bot

No terminal needed.

- [Reply to a system question](answer_a_question.md)
- [Redo onboarding or correct an answer](redo_onboarding.md)
- [Send the bot a large file](send_large_file.md)
- [Add lab results](add_labs.md)
- [Connect an Oura ring](connect_oura.md)
- [Connect iPhone Health and Apple Watch](connect_apple_health.md)
  (the safe path over Tailscale — [separately](connect_apple_health_tailscale.md))

## 2. You maintain your own installation

A terminal on the machine where the system is installed is required. Use the rule above for commands.

- Installation and updates: [install the system in Docker from an image](install_docker.md) ·
  [with an OpenAI, Gemini or DeepSeek key](llm_provider.md)
- [Connect Google Calendar](connect_google_calendar.md)
- [Connect a Withings blood-pressure monitor](connect_withings.md) — section "Docker image install"
- The bot replied “I couldn't answer: something failed inside the system…” with a code: [find the cause by the code](bot_fault.md)

The other maintenance guides are still written for the author's original installation and do not
work in an image-based install — they are in section 3.

## 3. The author's original installation (native, macOS services)

These guides are written for an installation from a repository clone with `launchctl` services on
the author's machine (a second machine, SSH, a Claude Desktop project, a second person on the same
machine). **They do not work in an image-based install, and the command translation rule does not
save them** — a check against the code on 02.10.2026 showed the difference is the installation
model, not command syntax. They are rewritten for Docker one at a time, when people who install
from the image need them.

- System alerts: [night cycle alert](night_cycle_respond.md) ·
  [escalation "a person has hit a problem"](service_trouble_alert.md) ·
  [check fired but no message arrived](diagnose_silent_check.md) ·
  [guard blocked a model call](llm_guard_blocked.md)
- Lab results inside: [recognition pipeline](lab_pipeline.md) · [row review queue](lab_review_queue.md) ·
  [lift quarantine on a pair](adjudicate_quarantine.md) ·
  [defer or close a question about a reference range](record_analyte_norm_verdict.md) ·
  [record a physician's surveillance decision](record_surveillance_decision.md)
- Data and content: [health constitutions](update_constitutions.md) ·
  [long-form analysis](run_analysis.md) · [food rules](reseed_food_rules.md) ·
  [seasonal table](reseed_seasonal_produce.md) · [trail list](refresh_trail_list.md)
- Channels: [email](email_channel.md) · [weekly digest](weekly_digest.md) ·
  [log rotation](rotate_logs.md)
- [Connect a Withings blood-pressure monitor](connect_withings.md) — a second blood-pressure source
- [Add a second person](add_person.md) ·
  [migrating from a native install to a container](pilot_switch.md)

## 4. You are developing the system

A clone is required: `git clone https://github.com/larrynovsky/larry-health-os.git`. All
remaining guides are here: [Git](git_workflow.md), [tests](run_tests.md),
[failed nightly run](handle_test_failure.md), [second machine](two_machine_setup.md),
[worktree for a thread](thread_worktree.md), [dashboard](extend_dashboard.md),
[domains](add_domain.md), [questionnaires](add_instrument.md), [dependencies](dependency_updates.md),
[documentation](update_docs.md), [image release](release.md),
[public mirror](publish_mirror.md).

Extending the system: [a channel in the morning brief](add_brief_channel.md) ·
[a clinical threshold](add_clinical_threshold.md) · [a literature agent topic](add_survivorship_topic.md) ·
[an image-intake domain](add_visual_domain.md) · [a scenario and its test](add_new_uc.md) ·
[a recipient of owner messages](add_bot_consumer.md) ·
[a model call](add_llm_call.md).
Project rules and gates: [find what exists before building](discovery_before_build.md) ·
[the disposability gate](disposability_gate.md) · [the lexicons-in-code sensor](move_lexicon_to_db.md) ·
[a date in a memory channel](date_memory_channel.md) · [a document to history](move_to_archive.md).

A new public guide in this folder must appear in one of the four lists — the test
`tests/unit/test_howto_landing_lists_all.py` checks this.

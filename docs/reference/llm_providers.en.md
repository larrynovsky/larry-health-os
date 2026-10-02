<!-- translation-of: docs/reference/llm_providers.md sha256:618ed23bad14 -->
**English** · [Русский](llm_providers.md)

<!-- generated: python3 llm_admission.py --reference; не править руками / do not edit -->

# What works on which model provider

Anthropic is the tutorial path: every role works. For other providers only a role whose model passed admission on the synthetic corpus (`methodology/llm_corpus`) works; otherwise that role's functions refuse rather than answer with an unchecked model.

A role is the system's internal name for a task (opus, sonnet, haiku, haiku_pinned), not a model name: on OpenAI the opus role is performed by an OpenAI model. Admission runs the model over test lab forms and texts with known answers; a single error means "no". A fallback is the model the system switches to by itself if the provider switches off the default one; "fallback: no" means the model failed and will not be used. Admission is re-run when a provider changes its models; new verdicts arrive with a new version of the system.

## anthropic

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| sonnet | lab photos (pass 2), chat | `claude-sonnet-4-6` | default | — | — |
| sonnet | lab photos (pass 2), chat | `claude-sonnet-5-5` | fallback: yes | 2026-10-02 | — |
| haiku | check-in, short answers | `claude-haiku-4-5` | default | — | — |
| haiku_pinned | treatment and labs from text | `claude-haiku-4-5-20251001` | default | — | — |
| opus | lab photos (pass 1), consilium | `claude-opus-4-7` | default | — | — |
| opus | lab photos (pass 1), consilium | `claude-opus-5` | fallback: yes | 2026-10-02 | — |
| opus | lab photos (pass 1), consilium | `claude-opus-5-5` | fallback: no | 2026-10-02 | — |

## openai

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `gpt-5.6-sol` | yes | 2026-10-02 | — |
| sonnet | lab photos (pass 2), chat | `gpt-5.6-luna` | yes | 2026-10-02 | — |
| haiku | check-in, short answers | `gpt-5.6-luna` | yes | 2026-10-02 | — |
| haiku_pinned | treatment and labs from text | `gpt-5.6-luna` | yes | 2026-10-02 | — |

## gemini

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `gemini-3.1-pro-preview` | yes | 2026-10-02 | — |
| sonnet | lab photos (pass 2), chat | `gemini-3.8-flash` | yes | 2026-10-02 | — |
| haiku | check-in, short answers | `gemini-3.8-flash` | yes | 2026-10-02 | — |
| haiku_pinned | treatment and labs from text | `gemini-3.8-flash` | yes | 2026-10-02 | — |

## deepseek

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `deepseek-v4-pro` | no | 2026-10-02 | lab_clean.jpg: WBC: 5.2 вместо 6.82; lab_clean.jpg: RBC: 4.6 вместо 4.71 |
| sonnet | lab photos (pass 2), chat | `deepseek-flash` | yes | 2026-10-02 | — |
| haiku | check-in, short answers | `deepseek-flash` | yes | 2026-10-02 | — |
| haiku_pinned | treatment and labs from text | `deepseek-flash` | yes | 2026-10-02 | — |

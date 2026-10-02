<!-- translation-of: docs/reference/llm_providers.md sha256:840195d3e47b -->
**English** · [Русский](llm_providers.md)

<!-- generated: python3 llm_admission.py --reference; не править руками / do not edit -->

# What works on which model provider

Anthropic is the tutorial path: every role works. For other providers only a role whose model passed admission on the synthetic corpus (`methodology/llm_corpus`) works; otherwise that role's functions refuse rather than answer with an unchecked model.

## openai

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `gpt-5.6-sol` | not checked | — | — |
| sonnet | lab photos (pass 2), chat | `gpt-5.6-terra` | no | 2026-10-02 | lab_hard.jpg: MCH: 29.3 вместо 29.9 |
| haiku | check-in, short answers | `gpt-5.6-luna` | yes | 2026-10-02 | — |
| haiku_pinned | treatment and labs from text | `gpt-5.6-luna` | yes | 2026-10-02 | — |

## gemini

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `gemini-3.1-pro-preview` | yes | 2026-10-02 | — |
| sonnet | lab photos (pass 2), chat | `gemini-3.8-flash` | not checked | — | — |
| haiku | check-in, short answers | `gemini-3.8-flash` | not checked | — | — |
| haiku_pinned | treatment and labs from text | `gemini-3.8-flash` | not checked | — | — |

## deepseek

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `deepseek-v4-pro` | no | 2026-10-02 | lab_clean.jpg: WBC: 5.2 вместо 6.82; lab_clean.jpg: RBC: 4.6 вместо 4.71 |
| sonnet | lab photos (pass 2), chat | `deepseek-flash` | yes | 2026-10-02 | — |
| haiku | check-in, short answers | `deepseek-flash` | yes | 2026-10-02 | — |
| haiku_pinned | treatment and labs from text | `deepseek-flash` | yes | 2026-10-02 | — |

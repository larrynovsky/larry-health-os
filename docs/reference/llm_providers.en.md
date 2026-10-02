<!-- translation-of: docs/reference/llm_providers.md sha256:a4fc34883381 -->
**English** · [Русский](llm_providers.md)

<!-- generated: python3 llm_admission.py --reference; не править руками / do not edit -->

# What works on which model provider

Anthropic is the tutorial path: every role works. For other providers only a role whose model passed admission on the synthetic corpus (`methodology/llm_corpus`) works; otherwise that role's functions refuse rather than answer with an unchecked model.

## openai

| Role | What it does | Model | Admitted | Date | Why |
|---|---|---|---|---|---|
| opus | lab photos (pass 1), consilium | `gpt-5.6-sol` | no | 2026-10-02 | lab_hard.jpg: WBC: лишняя строка 7.15; lab_hard.jpg: HGB: лишняя строка 13.8 |
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

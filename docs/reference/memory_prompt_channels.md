[English](memory_prompt_channels.en.md) · **Русский**

# Reference: каналы памяти→промпт (staleness)

Инвариант: каждый канал, вносящий воспоминание в промпт модели, делает время наблюдаемым
(штамп `[ГГГГ-ММ-ДД]` на каждом элементе). Иначе модель принимает старое за сегодняшнее
(инцидент: модель выдала давнее воспоминание за сегодняшний факт).

| Канал | Источник | Дата из | Механизм | Тест |
|---|---|---|---|---|
| `hai_core.get_history` | conversation_history (транскрипт) | `created_at` | штамп на сообщении (M2) | test_history_dating |
| `patient_context.pending_chat` | conversation_history (мост read-your-writes) | `created_at` | штамп на строке (M5) | test_context_dating |
| `patient_context.recent_notes` | memory_facts (state) | `valid_from` | штамп + окно 2д (Ф2) | test_memory_temporal |
| `patient_context._chat_context_line` | memory_facts (question, окно 45д) | `valid_from` | штамп на вопросе, убрано «свежие» (M4) | test_context_dating |
| `hai_chat.build_chat_payload` | сборка всего входа | — | + строка-дисциплина времени в системный промпт (M2) | test_chat_payload |

## Enforcement
- **Датчик полноты:** `memory_channel_registry` — AST-скан читателей сырого
  `conversation_history`, census-симметрия → **pre-commit** (блок 5). Ловит новый
  непокрытый канал. How-to: `docs/how-to/date_memory_channel.md`.
- **Гейт поведения:** `scripts/replay_staleness.py` — контролируемый A/B инцидента
  05-07→сегодня (валидировано: контроль 5/5 галлюцинаций, фикс 0/5).
- **Наблюдаемость сборки:** `hai_chat.assembled_context_text()` — точная строка, что
  видит модель (тест бьёт в точку потребления, не в срез).

## Известные границы (честно)
- Датчик — синтаксический прокси на raw-transcript; «датирует» доказывают тесты M2–M5 +
  реплей M3, не он. Новый канал НЕ-транскриптной формы он пропустит.
- Открытые вопросы копятся (окно 45д, «не закрываются») — датированы, но не гасятся (долг).
- Write-side петля (арбитр пишет галлюцинацию фактом) — вне этого фикса, тред A1.

Explanation (почему так): `дизайн_память_в_отчёт_staleness_2026-07-08.md` (iCloud).

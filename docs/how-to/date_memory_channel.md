[English](date_memory_channel.en.md) · **Русский**

# How-to: датировать канал памяти→промпт (тебя флажнул M6)

Датчик `memory_channel_registry` нашёл функцию, читающую сырой `conversation_history` в
промпт модели без даты. Каждый такой канал должен делать время наблюдаемым.
Выдуманный немедицинский пример: в старом сообщении сказано «мастерская закрыта».
Без даты модель может ошибочно представить это как сегодняшнее состояние.

## Почему
Сырой транскрипт без времени не отличает старое сообщение от сегодняшнего.
Определения каналов: `docs/reference/memory_prompt_channels.md`.

## Шаги

1. Добавь `created_at` в SELECT:
   ```python
   "SELECT role, content, created_at FROM conversation_history ..."
   ```
2. Штампуй каждую строку датой:
   ```python
   day = (r["created_at"] or "")[:10]
   content = f"[{day}] {content}" if day else content
   ```
   Эталоны: `hai_core.get_history` (M2), `patient_context.pending_chat` (M5).
3. Зарегистрируй вердикт в `memory_channel_registry.REGISTERED`:
   ```python
   "модуль.функция": {"verdict": "dated", "note": "штамп из created_at"},
   ```
   verdict: `dated` (читает в промпт, датирует) / `writer` (пишет транскрипт) / `infra` (DDL).
4. Если канал строит промпт из `memory_facts` (не транскрипта) — датируй по `valid_from`
   (эталон `patient_context._chat_context_line`, M4), и убери ложные метки «свежие».

## Проверить
```bash
python3.11 memory_channel_registry.py                     # OK или находки
python3.11 -m pytest tests/unit/test_memory_channel_registry.py -q
```

## Гейт поведения (не только датчик)
Датчик — синтаксический прокси. Что модель РЕАЛЬНО перестала переносить старое на сегодня —
держит реплей-гейт:
```bash
PYTHONPATH=~/health_scripts python3.11 scripts/replay_staleness.py
```
Контроль (без дат) должен воспроизводить галлюцинацию, фикс — гасить.

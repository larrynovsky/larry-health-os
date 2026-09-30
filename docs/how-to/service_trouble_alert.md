[English](service_trouble_alert.en.md) · **Русский**

# Что делать, когда пришла эскалация «тенант столкнулся с проблемой»

Сообщение выглядит так:

```
🛠 Похоже, тенант [health_partner] столкнулся с технической проблемой
(уверенность 0.80, гипотеза #14).
Признаки: echoed_system_text
Судья: человек прислал боту текст, сгенерированный самой системой
```

## 1. Посмотри, что человек прислал на самом деле

```
ssh <studio_ssh> 'sqlite3 ~/health_partner/data/health.db \
  "SELECT created_at, role, substr(content,1,200) FROM conversation_history \
   ORDER BY id DESC LIMIT 6;"'
```

Гипотеза хранит только признаки — переписки в ней нет намеренно.

## 2. Проверь, идёт ли шторм прямо сейчас

```
ssh <studio_ssh> 'tail -5 ~/health_scripts/logs/watcher_partner_err.log; \
  launchctl list | grep -E "watcher|bot|dashboard"'
```

## 3. Закрой гипотезу исходом — обязательно

Без этого шага долю ложных тревог посчитать нечем, и порог не откалибруется.

```
ssh <studio_ssh> 'cd ~/health_scripts && PYTHONPATH=. \
  HEALTH_DATA_DIR=~/health_partner \
  HEALTH_SECRETS_DIR=~/.health_secrets_partner \
  /opt/homebrew/bin/python3.11 -c \
  "import service_trouble as st; st.resolve_outcome(14, \"confirmed\")"'
```

`confirmed` — проблема была. `false_alarm` — не было. Третьего значения нет
намеренно: свободный текст сделал бы статистику невычислимой.

## 4. Посмотреть накопленное

```
ssh <studio_ssh> 'sqlite3 ~/health_partner/data/health.db \
  "SELECT outcome, count(*) FROM service_trouble GROUP BY 1;"'
```

Открытые (`outcome IS NULL`) — это те, которые ты не закрыл. Если их много,
статистика врёт в сторону оптимизма.

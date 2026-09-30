[English](dependency_updates.en.md) · **Русский**

# Как обновлять зависимости (SEC-19, 2026-07-06)

Рецепт для системного python3.11 на Studio — канонического окружения health-os.
Канон версий: `requirements.lock` (в git). Аудит уязвимостей: weekly
`com.larry.health.pipaudit` → `logs/pip_audit_latest.json` → nightly
`security_sensors` → triage → Telegram.

## Когда обновлять

- Пришёл варн `security:pip_audit` с advisory-id — обновляй уязвимый пакет.
- Квартальный аудит SECURITY.md — плановая ревизия отставших пакетов.
- В остальное время — не обновляй: свежесть сама по себе не ценность.

## Шаги (по одному пакету за заход)

1. **Cooldown**: целевой версии ≥3 дней от даты релиза на PyPI (supply-chain
   защита от свежескомпрометированных релизов). Исключение — security-фикс,
   закрывающий активный advisory: ставь сразу.
2. На Studio: `ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip install 'pkg==X.Y.Z' --break-system-packages"`.
3. Прогнать регрессию: `bash scripts/test_on_studio.sh` (или дождаться nightly).
4. Обновить lock с MacBook (single-writer: файл в git правится здесь):
   `{ head -3 requirements.lock; ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip freeze"; } > requirements.lock`
   — поправив дату во 2-й строке заголовка.
5. Внепланово перегенерить аудит: `ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 scripts/pip_audit_check.py"` — варн должен погаснуть.
6. Commit lock (+ при advisory — строчка в SECURITY.md, если решение нетривиальное).

## Advisory без фикса

Если у уязвимости нет исправленной версии или она неприменима (функция не
используется, поверхность недостижима) — id в `_PIP_AUDIT_ALLOW`
(`security_sensors.py`) с комментарием-обоснованием. Пустой allowlist —
нормальное состояние; каждый пункт ревизуется квартальным чеклистом.

## Восстановление окружения с нуля

`/opt/homebrew/bin/python3.11 -m pip install -r requirements.lock --break-system-packages`

## Чего НЕ делать

- Не обновлять «всё сразу» (`pip install -U ...` списком) — при регрессии
  бинарный поиск виновника невозможен.
- Не править версии только в lock без установки на Studio — lock описывает
  фактическое окружение, а не желаемое (иначе он врёт, см.
  feedback_stale_intermediate_layer).
- Не запускать pip-audit из nightly integrity — сеть в 07:50 гейтит утренний
  отчёт; сеть только в weekly-джобе.

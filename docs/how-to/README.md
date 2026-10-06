[English](README.en.md) · **Русский**

# Инструкции «как сделать»: с чего начать

> **Тип документа:** How-to (Diátaxis), посадочная страница. Инструкций здесь полсотни, и они
> написаны для четырёх разных читателей. Найдите себя ниже — остальные списки можно не читать.

Поставили систему по [уроку первой установки](../tutorials/first_install.md) — у вас нет копии
этого репозитория, только папка `~/health-docker` с `compose.yaml`. Большая часть инструкций
ниже написана раньше, для установки из клона репозитория со службами macOS (`launchctl`).
Пока они не переписаны, переводите команды по одному правилу.

## Правило перевода команд для установки из образа

| В инструкции | У вас |
|---|---|
| `cd ~/health_scripts` | `cd ~/health-docker` |
| `python3.11 <скрипт>.py …` | `docker compose exec cron python3 <скрипт>.py …` |
| `launchctl kickstart -k …health.bot` (и другие службы) | `docker compose restart bot` (`dashboard`, `lab-intake`, `cron`) |
| `launchctl list \| grep health`, плисты в `~/Library/LaunchAgents` | `docker compose ps` — плистов нет, расписание живёт внутри `cron` |
| журналы служб `~/health_scripts/logs/…` (`bot_err.log` и др.) | `docker compose exec cron tail -n 50 /app/logs/<файл>` |
| журналы задач `~/health/logs/…` | `docker compose exec cron ls /home/health/health/logs/` |

`docker compose logs` почти пуст: службы пишут журналы в файлы, а не в вывод контейнера.

Код в контейнере лежит в `/app`, и именно там исполняется команда: скрипты из инструкций
находятся по тем же именам (проверено 01.10 на чистой установке из `v0.1.0`). Правило не
покрывает Git, тесты и правку кода — для них нужен клон, это четвёртый список.

## 1. Вы пользуетесь системой через бота

Терминал не нужен.

- [Ответить на вопрос системы](answer_a_question.md)
- [Пройти знакомство заново или исправить ответ](redo_onboarding.md)
- [Прислать боту большой файл](send_large_file.md)
- [Добавить анализы](add_labs.md)
- [Подключить кольцо Oura](connect_oura.md)
- [Подключить «Здоровье» iPhone и Apple Watch](connect_apple_health.md)
  (безопасный путь через Tailscale — [отдельно](connect_apple_health_tailscale.md))

## 2. Вы обслуживаете свою установку

Нужен терминал на машине, где стоит система. Команды — по правилу выше.

- Установка и обновление: [поставить систему в Докере, своей сборкой](install_docker.md) ·
  [с ключом OpenAI, Gemini или DeepSeek](llm_provider.md)
- [Подключить Google Calendar](connect_google_calendar.md)
- [Подключить тонометр Withings](connect_withings.md) — раздел «Установка из образа Докера»
- Бот ответил «Не получилось ответить: сбой внутри системы…» с кодом: [найти причину по коду](bot_fault.md)

Остальные инструкции обслуживания пока написаны для исходной установки автора и в установке из
образа не работают — они в разделе 3.

## 3. Исходная установка автора (нативная, службы macOS)

Эти инструкции написаны для установки из клона репозитория со службами `launchctl` на машине
автора (вторая машина, SSH, проект Claude Desktop, второй человек на той же машине). **В установке
из образа они не работают, и правило перевода команд их не спасает** — сверка с кодом 02.10.2026
показала, что дело в другой модели установки, а не в синтаксисе команд. Переписываются под Докер по
одной, когда у тех, кто ставит из образа, появится в них нужда.

- Звонки системы: [ночной звонок](night_cycle_respond.md) ·
  [эскалация «человек столкнулся с проблемой»](service_trouble_alert.md) ·
  [проверка сработала, а сообщение не пришло](diagnose_silent_check.md) ·
  [защита заблокировала вызов модели](llm_guard_blocked.md)
- Анализы изнутри: [конвейер распознавания](lab_pipeline.md) · [очередь ревью строк](lab_review_queue.md) ·
  [снять карантин с пары](adjudicate_quarantine.md) ·
  [отложить или закрыть вопрос о норме](record_analyte_norm_verdict.md) ·
  [записать решение врача по наблюдению](record_surveillance_decision.md)
- Данные и тексты: [конституции здоровья](update_constitutions.md) ·
  [долгий анализ](run_analysis.md) · [пищевые правила](reseed_food_rules.md) ·
  [сезонная таблица](reseed_seasonal_produce.md) · [список троп](refresh_trail_list.md)
- Каналы: [почта](email_channel.md) · [еженедельный дайджест](weekly_digest.md) ·
  [ротация журналов](rotate_logs.md)
- [Подключить тонометр Withings](connect_withings.md) — второй источник давления
- [Добавить второго человека](add_person.md) ·
  [перевод из нативной установки в контейнер](pilot_switch.md)

## 4. Вы разрабатываете систему

Нужен клон: `git clone https://github.com/larrynovsky/larry-health-os.git`. Здесь все
остальные инструкции: [Git](git_workflow.md), [тесты](run_tests.md),
[упавший ночной прогон](handle_test_failure.md), [вторая машина](two_machine_setup.md),
[своё дерево для нити](thread_worktree.md), [дашборд](extend_dashboard.md),
[домены](add_domain.md), [опросники](add_instrument.md), [зависимости](dependency_updates.md),
[документация](update_docs.md), [выпуск образа](release.md),
[открытый репозиторий](publish_mirror.md).

Расширить систему: [канал в утренний бриф](add_brief_channel.md) ·
[клинический порог](add_clinical_threshold.md) · [тема литературного агента](add_survivorship_topic.md) ·
[домен приёма изображений](add_visual_domain.md) · [сценарий и его тест](add_new_uc.md) ·
[получатель сообщений владельца](add_bot_consumer.md) ·
[вызов модели](add_llm_call.md).
Правила и гейты проекта: [найти существующее до постройки](discovery_before_build.md) ·
[гейт одноразовости](disposability_gate.md) · [датчик словарей в коде](move_lexicon_to_db.md) ·
[дата в канале памяти](date_memory_channel.md) · [документ в историю](move_to_archive.md).

Новая открытая инструкция в этой папке обязана попасть в один из четырёх списков — это проверяет тест
`tests/unit/test_howto_landing_lists_all.py`.

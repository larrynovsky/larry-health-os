[English](TESTING_CONTRACTS.en.md) · **Русский**

# TESTING_CONTRACTS.md — Что система обязана делать правильно

⚠️ **Статус (2026-07-02, обзор трёх линз):** этот файл — декларация порогов и
критериев, НЕ единственная точка истины: он отстаёт от кода (не знает про
check_lab_canon_health, check_treatment_history_extracted, model_health_check,
oura_freshness_check и лаб-конвейер 2026-06-30). Актуальные пороги — в
`system_config` (БД) и константах `integrity_tests.py`. План: генерируемые
секции по образцу gen_blueprint (Поток 5 plan_fix_three_lenses_2026-07-02);
до этого при расхождении верить коду/БД, сюда — фикс.

Последнее содержательное обновление: 2026-04-24

---

## 1. ДАННЫЕ — Свежесть и валидность

### Ежедневные источники (conit C1)
| Источник         | Напоминание | Тревога  | Действие при тревоге              |
|------------------|-------------|----------|-----------------------------------|
| oura             | —           | > 26ч    | Алерт в Telegram до отправки отчёта |
| apple_health     | —           | > 26ч    | То же                             |
| withings (BP)    | —           | > 6ч     | Алерт если было измерение         |
| oncology_pdf     | —           | > 12ч    | Алерт + метка в выводах           |

### Периодические источники
| Источник              | Напоминание | Тревога     | Примечание                              |
|-----------------------|-------------|-------------|-----------------------------------------|
| lab_results           | 6 месяцев   | 9 месяцев   | По дате последнего анализа в БД         |
| genome_update_agent   | —           | > 30 дней   | По дате последнего запуска              |
| GP weekly report      | —           | > 8 дней    | По дате последнего agent_report         |
| GP monthly report     | —           | > 35 дней   | По дате последнего monthly отчёта       |

### Клинические периоды (conit C3)
- Источник: **только документ от врача** после консультации
- Валидность: до даты `next_appointment`
- Тревога: если `next_appointment` прошла и `periods` не обновлены
- Поездки (calendar_sync): обновляются hourly, тревога если > 2ч без синка при наличии активной поездки

### Схемы требующие реализации
- `problem_list`: нужно поле `review_date TEXT` — обязательно при создании записи
- `protocols`: нужно поле `review_date TEXT` — обязательно при создании записи
- Тревога: `review_date` прошла, статус не изменён

---

## 2. ВЫВОДЫ — Качество и верификация

### Правило oracle для агентских отчётов
| Тип отчёта                              | PubMed верификация | Порог |
|-----------------------------------------|--------------------|-------|
| `agent_type IN ('specialist', 'gp')`    | Обязательна        | Каждый PMID существует в PubMed |
| `has_findings = 1`                      | Обязательна        | То же |
| Morning report                          | Не требуется       | Исключён |
| Checkin                                 | Не требуется       | Исключён |

**Что значит «верифицирован»:**
- PMID запрашивается через PubMed API (pubmed_client.py)
- Статья существует и возвращает данные
- Статья рецензированная (peer-reviewed)
- Отчёт без единого PMID при `has_findings=1` — сбой

### Физиологические диапазоны (внутренняя консистентность)
| Метрика      | Допустимый диапазон | Действие при выходе |
|--------------|---------------------|---------------------|
| hrv          | 5–120 мс            | Предупреждение       |
| resting_hr   | 30–120 уд/мин       | Предупреждение       |
| bp_systolic  | 60–200 мм рт.ст.    | Предупреждение       |
| bp_diastolic | 40–130 мм рт.ст.    | Предупреждение       |
| sleep_total  | 0–14 ч              | Предупреждение       |
| spo2_avg     | 70–100 %            | Предупреждение       |

---

## 3. ГИПОТЕЗЫ — Генерация и отработка

Гипотеза без движения = мусор в базе.

| Условие                                              | Статус   |
|------------------------------------------------------|----------|
| Гипотеза без связанного эксперимента                 | Тревога  |
| Эксперимент без `experiment_log` за последние 14 дней | Предупреждение |
| Эксперимент `status='active'` старше 60 дней без логов | Тревога  |

---

## 4. КОНТЕКСТ — Полнота входных данных агентов

Агент не может давать правильные выводы если его контекст неполный.

| Агент             | Обязан содержать                                      |
|-------------------|-------------------------------------------------------|
| checkin_agent     | active periods, upcoming periods (14 дней)            |
| GP weekly report  | последние labs, problem_list, active protocols, genome |
| morning_report    | metrics 7д, BP если есть                             |
| lifestyle agents  | domain-specific genome block, percentile metrics      |

Тест: парсим контекстную строку (или mock-вызов) и ищем обязательные секции.

**Консилиум (лаб-секция) — контракт ИСКЛЮЧЕНИЯ, не включения.** `build_specialized_context`
НЕ подаёт под клиническим заголовком строки чужого домена (дом=канон, ждут сведения
имени к канону): иначе биохимия крови едет консилиуму под этикеткой «вне биохимии крови»
(§16 с изнанки, живой дефект 2026-08-01, пойман глазами). Оракул —
`tests/unit/test_build_specialized_context.py::test_full_output_golden_master`
(golden master всего вывода + исполненный негативный контроль: снятие вердикта `cbc`
роняет пин, Ferritin утекает). Граница названа: детектор ДРЕЙФА вида, не клинической
корректности (§20, RST check≠test). Логика живёт в тесте, здесь — только указатель.

---

> **Датчик сработал, а до тебя не дошло** — рецепт разбора: [docs/how-to/diagnose_silent_check.md](docs/how-to/diagnose_silent_check.md).
> Зелёный юнит-тест доставки проверяет, что код ЗОВЁТ отправку, а не что Telegram доставил.

<!-- BEGIN AUTOGEN: integrity-sensors (gen_testing_contracts.py) -->

### Реестр датчиков целостности (186 шт., генерируется)

> Источник: `integrity_tests.py`. Таблица построена из AST — не редактировать руками, менять код. launchd 07:50 → triage 08:00.

| Датчик (label) | Функция | Что проверяет |
|---|---|---|
| последние данные HRV не старше 48ч | `check_data_freshness` | — |
| <dynamic> | `check_data_window` | — |
| шаги из flat daily_metrics видны через get_day() | `check_steps_visible_via_get_day` | Консистентность шагов: если steps есть в flat daily_metrics.steps — |
| ключевые метрики (HRV + sleep) присутствуют | `check_core_metrics` | — |
| недельная статистика не пуста | `check_stats_non_empty` | — |
| GP-отчёт есть и не старше 8 дней | `check_gp_reports` | — |
| содержание GP-отчёта: есть ключевые разделы | `check_report_content` | — |
| <dynamic> | `check_problem_list` | — |
| открытые проблемы описаны простыми словами | `check_problem_plain_summary` | Открытая проблема несёт описание простыми словами (27.09, волна 4). Медицинский текст — |
| протоколы читаются | `check_protocols` | — |
| задачи читаются (open + overdue) | `check_tasks_pipeline` | — |
| активность задач за 14 дней | `check_recent_task_activity` | — |
| канал вопрос→ответ целостен | `check_question_answer_integrity` | Вопрос, закрытый без ответа; недоставленный вопрос; ответ без следа в памяти. |
| очередь вопросов сливается за свой срок | `check_question_queue_drains` | Очередь вопросов обязана СЛИВАТЬСЯ за время, которое сама себе назначает. |
| предложения по списку проблем доставлены | `check_proposals_delivered` | Предложение GP по списку проблем ждёт решения человека, но до него не доехало. |
| кандидаты в вопросы не выбрасываются молча | `check_question_candidates_not_discarded` | Кандидат выпал за окно, НИ РАЗУ не побывав у судьи. |
| ответ модели разбирается | `check_llm_answer_parsed` | Ответ модели, который не удалось разобрать ВООБЩЕ, обязан быть видимым. |
| доставленные тексты судятся против канона | `check_reco_repeats_fresh_lab` | Текст советует сдать анализ, который уже сдан СВЕЖЕЕ окна мониторинга. |
| поверхность отсутствия под сторожем или названа | `check_absence_surface_guarded` | Тракт с ПОВЕРХНОСТЬЮ для утверждения об отсутствии либо под сторожем, либо назван. |
| ответы пациента доезжают до GP-контекста | `check_question_answers_reach_doctor` | Квитанция в точке потребления: свежий ответ ВИДЕН в собранном GP-контексте. |
| проблемы под наблюдением доезжают до недельного разбора | `check_watched_problems_reach_review` | Каждая проблема со статусом наблюдения видна в контексте недельного разбора GP. |
| build_context(yesterday) содержит данные | `check_build_context` | — |
| канон lab_results: единственный писатель (нет biochemical-json) | `check_lab_single_writer` | Инвариант единственного писателя канона (BL-LAB-CANON-1, Primary-Based |
| PGS reference-БД на месте и населена | `check_pgs_reference` | Reference-БД полигенных весов (pgs_catalog+pgs_weights) вынесена из канона |
| свежесть genome_update_agent | `check_genome_update` | genome_update_agent не старше GENOME_UPDATE_ALERT_DAYS. |
| liveness демонов (KeepAlive) | `check_daemons_alive` | KeepAlive-демоны должны иметь живой PID. Закрывает класс «сервис тихо умер» |
| слепота stderr-датчика | `check_stderr_watch_blindness` | Не ослеп ли сам датчик stderr. ОБЯЗАН стоять ДО check_stderr_errors. |
| новые ошибки в stderr джоб | `check_stderr_errors` | Ошибки, ДОПИСАННЫЕ в stderr-логи джоб с прошлой проверки. |
| полнота рестарта после деплоя (§12) | `check_deploy_restart_completeness` | Ратчет полноты рестарта: множество долгоживущих джоб == множество, которое |
| счётчик тенантов под решением о молчании (§18) | `check_tenant_count_vs_silence_decision` | Счётчик под решением «молчание человека признаком не считать» (§18). |
| env-консистентность launchd-плистов (мультитенант) | `check_plist_env_consistency` | WARN: owner-джоба с HEALTH_DATA_DIR в плисте, но БЕЗ него в загруженном |
| launchd: инвентарь джоб против репозитория | `check_launchd_inventory` | WARN: копия плиста в репозитории разошлась с живой, либо джоб без копии стало больше. |
| живость арбитра памяти | `check_arbiter_liveness` | WARN: экстрактор памяти (арбитр) отстал от СВЕЖИХ сообщений. Класс «тихая |
| живость доставки консолидации/disagree | `check_consolidation_delivery_liveness` | WARN: делверинг-джоб disagree/supersede (run_nightly_consolidation, daily) мог тихо |
| живость подъёмника вопросов | `check_promote_questions_liveness` | WARN: подъёмник кандидатов в вопросы мог тихо умереть. |
| живость uncommitted_watchdog (§14) | `check_watchdog_liveness` | WARN: §14 terminus — сам uncommitted_watchdog мог тихо умереть (launchd |
| незакоммиченная работа на MacBook (по снимку) | `check_macbook_uncommitted` | НЕЗАКОММИЧЕННАЯ РАБОТА НА MacBook — судится отсюда, по снимку, а не по опросу ноутбука. |
| код MacBook доехал до Studio (по снимку) | `check_macbook_head_deployed` | КОД С MacBook НЕ ДОЕХАЛ ДО STUDIO — и об этом никто не сказал (27.09, решение владельца). |
| копия работы нитей (не один экземпляр) | `check_thread_work_has_a_second_copy` | РАБОТА НИТИ В ОДНОМ ЭКЗЕМПЛЯРЕ — вопрос ПОСТРАДАВШЕГО, не строителя. |
| нить без строки в реестре (сирота) | `check_threads_have_index_row` | НИТЬ-СИРОТА: ветка жива, а в реестре нитей её нет — её не видит ни одна сессия. |
| захват чужого файла в коммит (по ленте снимков) | `check_captured_files` | ЗАХВАТ ЧУЖОГО ФАЙЛА В КОММИТ — вопрос человеку, не вердикт машины. |
| хуки исполняемы на диске (§14) | `check_git_hooks_executable` | Активный хук без бита исполнения = ВСЕ гейты выключены разом, и молча. |
| живость гейта одноразовости (§14/§15) | `check_dispgate_liveness` | §14 для гейта одноразовости (§15): он мог перестать защищать двумя способами — |
| живость гейта квитанции замысла (§14, intent-receipts) | `check_intentgate_liveness` | §14 для гейта квитанции замысла (нить intent-receipts): два способа перестать защищать — |
| повтор причин «не одноразовый» (§15/§13) | `check_disposability_causes` | §15/§13: одна и та же причина отрицательного вердикта, всплывшая в N РАЗНЫХ календарных |
| рост эпизодической памяти (Ф4.3 tripwire) | `check_memory_facts_growth` | WARN (Ф4.3 tripwire, план «ПЕРЕСБОРКА ФАЗЫ 4»): активные ЭПИЗОДИЧЕСКИЕ |
| покрытие temporal_class (Ф1) | `check_temporal_class_coverage` | WARN: active state с temporal_class IS NULL — путь записи обошёл деривацию |
| frozen-relative → transient (Ф1/Ф2) | `check_frozen_relative_not_current` | WARN: active state с «сегодня/вчера» в ТЕКСТЕ, но НЕ классифицированное transient → |
| регрессия числа строк vs бэкап | `check_db_row_regression` | Таблица упала >50% против пика последних бэкапов → тихое стирание данных. |
| visual-intake сироты (фото/кейс/гипотеза) | `check_visual_orphans` | Целостность visual-intake: фото без кейса / кейс без фото / handed_off без |
| visual-intake: срок жизни разбора работает | `check_stale_visual_cases` | Сторож МЕХАНИЗМА срока жизни разбора (нить symptom-ttl, 03.10), а не человека. |
| symptom_intake verdict-rate (lagging) | `check_visual_verdict_rate` | ЗАПАЗДЫВАЮЩИЙ сигнал качества elicitation: доля symptom_intake-гипотез, |
| symptom-intake дисциплина промпта (поза+§9) | `check_symptom_prompt_discipline` | Guard безопасной позы: промпт elicitation не потерял запрет успокоения; |
| усыхание лаб-истории vs бэкап (FAIL) | `check_lab_history_regression` | FAIL (health-критично): lab_results резко усохла vs последний бэкап — класс |
| CBCR-методология в git (наличие+непустота) | `check_cbcr_methodology_present` | CBCR-методология load-bearing (manifest = system-prompt генерации гипотез, |
| Методология валидационного гейта в git (наличие+непустота) | `check_validation_gate_methodology_present` | Методология валидационного гейта load-bearing (спека = замысел подсистемы, |
| канон: «норма» не противоречит референсу бланка | `check_canon_normal_flag_matches_reference` | «Норма» не противоречит напечатанному референсу. Оба тенанта (волна 4, 27.09). |
| SQLite integrity_check ok per-tenant (нет B-tree corruption) | `check_db_integrity` | PRAGMA integrity_check per-tenant — B-tree corruption и out-of-order rowids. |
| тенант-БД достижимы (анти-усечёнка per-tenant чеков) | `check_tenant_dbs_reachable` | Мета-датчик против ТИХОЙ УСЕЧЁНКИ (RST, partner-integrity 2026-07-17). Ловушка: |
| готовность партнёра к пер-тенант стратификации (Группа 3 триггер) | `check_partner_epochs_ready` | Готовность СИБЛИНГ-тенанта к пер-тенант стратификации A/D. |
| имена семьи валид-гейта разрешаются в живые данные | `check_family_names_resolve` | Каждое объявленное имя семьи валид-гейта должно разрешаться в ЖИВЫЕ данные. |
| лаб-пороги доезжают до данных (канон-имя + строка в lab_results) | `check_threshold_names_reach_data` | Каждый лабораторный порог обязан ВСТРЕЧАТЬ данные: имя = канон lab_canon.normalize, |
| нормы не просрочены (next_review) | `check_norm_review_overdue` | Активная норма с прошедшим next_review — красное у владельца. Это буквально |
| норма-референс согласна с интервалом лаборатории (внешний свидетель) | `check_norm_vs_lab_reference` | Внешний свидетель нормы. Красное — только для norm_kind='reference_interval': |
| покрытие нормой измеряемых аналитов | `check_norm_coverage` | Покрытие нормой: аналит с ≥norm.coverage_min_n измерений без нормы ни в одном доме — |
| вид нормы вынесен (norm_kind ≠ unclassified) | `check_norm_kind_unclassified` | Остаток без вердикта о виде нормы — WARN-счётчик владельцу. Не красное: вердикт |
| документы нормы свежи (sha256 / updated_at по next_check) | `check_norm_documents_fresh` | Норма — реплика внешнего документа; единственная ось согласованности — устаревание, |
| расписание наблюдения внутри окна гайдлайна по эпизоду | `check_schedule_vs_guideline` | Назначение врача (lab_monitoring_schedule, source encounter:*) — первично; гайдлайн по |
| пороги safety_net выведены из документа (не набраны) | `check_threshold_source_is_document` | Инвариант threshold_derived_from_document (реестр norm_from_documents): набранных |
| живость reschedule брифа (местный tz) | `check_reschedule_liveness` | WARN: 12ч-job пересчёта местного пояса брифа (reschedule_local) молчит у тенанта. |
| метрики прибора: у каждой есть хозяин (HAE) | `check_hae_arrivals_have_owner` | WARN: у метрики, которую прибор реально присылает, нет хозяина (решение владельца 26.09). |
| давление: дневное значение — среднее замеров Withings, второго писателя нет | `check_bp_day_is_withings` | WARN: дневное давление разошлось с замерами Withings (нить bp-withings-owner, 06.10). |
| архив сырья HAE сжат (ротатор, старше 15 дней) | `check_hae_raw_archive_compressed` | FAIL: в архиве сырья HAE лежат несжатые выгрузки старше 15 дней (решение владельца 26.09). |
| weekly_digest: доставлен всем тенантам (понедельник) | `check_weekly_digest_delivered` | Нить weekly-digest (2026-09-05). Понедельник: файл дайджеста ПРОШЛОЙ недели есть и |
| clinical_kb населён (пол/рамка еды не вырождены, Ф2) | `check_clinical_kb_populated` | FAIL: clinical_kb ПУСТ → medical_frame вырождается в standard-рамку И assert_floor молчит → |
| покрытие effect_allele (strand) | `check_effect_allele_coverage` | Покрытие strand-резолюции effect_allele (genome strand-fix Ф7). |
| clinical_kb реплика свежа (source==table, Ф2) | `check_clinical_kb_replica_fresh` | FAIL: clinical_kb в БД тенанта РАЗОШЁЛСЯ с git-источником (yaml сменился, init_db не |
| свежесть lab_results | `check_lab_freshness` | Анализы не старше LAB_ALERT_DAYS (9 мес = тревога, 6 мес = предупреждение). |
| канон lab_results: нет невозможных значений и конфликтов | `check_lab_canon_health` | Канон lab_results: нет физиологически невозможных значений. |
| промоут не стоит (очередь staging движется) | `check_promotion_backlog_stale` | Труба распознавания не должна забиваться на ВЫХОДЕ молча. |
| справочник LOINC: один дом, канон его не перехватывает | `check_reference_tables_not_in_canon` | У справочника LOINC ОДИН дом — общий `loinc.db`. Копия в каноне — ловушка. |
| под одним именем не склеены разные аналиты (нормы не расходятся) | `check_lab_names_not_glued` | Разные аналиты под одним именем — тихая порча тренда. |
| канон lab_results: у каждой строки есть результат | `check_canon_rows_have_a_result` | В каноне нет строк без результата — ни числа, ни текста. Оба тенанта. |
| канон lab_results: один аналит в документе — одна дата | `check_canon_one_date_per_analyte_in_doc` | Внутри ОДНОГО документа один аналит в одном материале не может иметь две даты. |
| канон lab_results: у нового значения есть единица | `check_lab_unit_present` | Число без шкалы непригодно для клинического вывода — а канон его принимает. |
| канон lab_results: референс в той же шкале, что значение | `check_lab_ref_scale` | Референс обязан быть в той же шкале, что значение. |
| staging: материал пробы и его ступень не разошлись | `check_staging_specimen_provenance` | Материал БЕЗ своей ступени неотличим от назначенного — а именно эта |
| значение и оператор сравнения не разошлись | `check_censored_values_coherent` | Оператор сравнения и число живут ТОЛЬКО вместе. |
| specialized_lab_results: целостность (panel_type + значение) | `check_specialized_lab_health` | specialized_lab_results (BL-LAB-CANON-2): целостность спец-панелей. Каждая |
| спец-слой пустеет: сводимое имя не застревает мимо канона | `check_specialized_canon_waiting` | Спец-слой — комната ожидания канона: строка с вердиктом дом=канон живёт здесь, |
| назывной долг канона не растёт | `check_canon_naming_debt` | Назывной долг канона не растёт: строка из документа живёт под своим именем. |
| история не заслоняет настоящее | `check_unrepeated_draw_not_swamping` | Исторический блок не перерастает текущую лабораторную картину. |
| партнёрский кран литературы: гаситель | `check_literature_partner_gate` | Отложенное решение владельца возвращается по СОБЫТИЮ, а не по сроку. |
| специальные панели: электрофорез сходится сам с собой | `check_electrophoresis_sums` | Электрофорез сходится сам с собой (см. `electrophoresis_offenders`). |
| промоут: отказ слить два значения виден человеку | `check_promote_conflicts` | Измерения, которые промоут ОТКАЗЫВАЕТСЯ класть в канон, обязаны быть видны. |
| решения LOINC не противоречат отсеву по фактам о человеке | `check_loinc_decisions_possible` | Записанное решение не смеет ссылаться на код, невозможный для этого человека. |
| канон lab_results: нет биллинг-мусора (валюта/процедуры) | `check_lab_no_billing_rows` | Канон lab_results НЕ содержит биллинг-мусор из инвойсов (#67, 2026-07-02). |
| сцепление carrier-статус⟹effect_allele (null≠чисто) | `check_carrier_status_allele_coupling` | Сцепление carrier-статус ⟹ effect_allele NOT NULL (инвариант |
| инварианты memory_facts (E: no-double-active-key + R11) | `check_memory_facts_invariants` | FAIL: инварианты memory_facts (E, долг из аудита 5 июля). |
| конституции не полые (effect_allele заполнен до генерации) | `check_constitutions_not_hollow` | FAIL, если конституции сгенерированы на ПУСТОМ effect_allele (полые). |
| пульс пересборки конституций по новым вводным | `check_constitutions_trigger_alive` | Пульс пересборки конституций по новым вводным (решение владельца 2026-09-25: «дальше |
| предохранителю есть по чему судить | `check_safety_net_can_judge` | «Судить нечем» у предохранителя — долг системы в инженерную очередь. |
| профиль генотипа тенанта в публичной зоне | `check_public_genotype_profile` | Профиль генотипа тенанта в публичной зоне (2026-09-25, нить genotype-scrub). |
| единственный канонический health.db (R1/R2 split-brain) | `check_single_canonical_db` | R1/R2 (split-brain prevention, 2026-06-18): ровно один health.db, не в синк-папке. |
| нет кросс-тенант загрязнения Oura (fail-closed belt-and-suspenders) | `check_cross_tenant_contamination` | Belt-and-suspenders к fail-closed secrets_dir() (2026-07-03). |
| разбор документов: один файл — одно событие | `check_one_document_one_event` | Разбор документов создаёт ровно одно событие на файл (нить intake-tails, 24.09). |
| longitudinal_analysis свежесть | `check_longitudinal_freshness` | longitudinal_analysis запускается по расписанию `com.larry.health.longitudinal`. |
| longitudinal гейт применён | `check_longitudinal_gate_applied` | В свежем longitudinal-саммари статистический гейт обязан быть применён. |
| квитанция прогона гейта | `check_gate_run_receipt` | Отказ гейта — событие с читателем, а не «строки просто нет». |
| вера свежее правок данных | `check_belief_fresh_vs_data` | Вера — это КЭШ, посчитанный из `daily_metrics`. У кэша не было инвалидации. |
| провенанс стадий сна (чинит сам) | `check_sleep_stage_provenance` | Фальшивые стадии сна не должны существовать — а если появились, чинятся САМИ. |
| смешение приборов в вере (ратчет) | `check_sleep_device_mixing` | Сон от одного прибора, ВСР от другого — в одной строке веры. Ратчет, не порог. |
| карантин без вердикта | `check_quarantine_stuck` | Карантин без вердикта дольше QUARANTINE_STUCK_DAYS — затор, а не порядок. |
| mc_gap: открытия на разрешении Монте-Карло (триггер Фаз 1/3-а) | `check_mc_gap` | MC-зазор: любой прошедший BY-вердикт лёг в ±2·MCSE от своей линии отбора → решение об |
| pass-set мерцание (Фаза 4 триггер онлайн-контроллера) | `check_passset_flicker` | Мерцание pass-set: членство прошедших пар изменилось между двумя прогонами → пара впервые |
| глубина истории pass-set (тихая потеря базы сравнения) | `check_passset_history_depth` | История снимков короче календаря → её подчистили или файл пересоздали. |
| артефакты гейта после applied-прогона | `check_gate_artifacts_liveness` | После УСПЕШНОГО прогона гейта обязательные артефакты обязаны существовать и быть не |
| жива ли рельса доставки warn (integrity→triage→Telegram) | `check_triage_delivery_liveness` | ЖИВА ЛИ САМА РЕЛЬСА ДОСТАВКИ warn (integrity → triage → Telegram). |
| утренний разбор человека-тенанта жив | `check_tenant_triage_alive` | Утренний разбор ЧЕЛОВЕКА-ТЕНАНТА жив (28.09, решение владельца «партнёру — свой разбор»). |
| расписание GP monthly | `check_gp_schedule` | GP monthly ≤35д. |
| review_date проблем и протоколов | `check_review_dates` | Проблемы и протоколы с просроченным review_date. |
| актуальность клинических периодов | `check_periods_expiry` | Клинические периоды: фазы watchful_waiting с истёкшей датой без next phase. |
| даты терапии: periods ↔ problem_list, medications, профиль | `check_treatment_dates_agree` | Даты терапий в `periods` (primary) против `problem_list` (копии в полях и заголовках), |
| лечение извлечено из документов | `check_treatment_history_extracted` | Лечение — производное из документов (medications), не ручная строка. |
| нет зашитого диагноза в промпт-билдерах (нить diagnosis-hardcode) | `check_no_hardcoded_diagnosis` | FAIL: снятый онко-литерал вернулся в промпт-билдер (нить diagnosis-hardcode). |
| классификатор документов: per-tenant сид засеян (A3 liveness) | `check_doc_classifier_seeded` | WARN: tenant_doc_patterns.yaml есть, но его паттерны НЕ в doc_patterns — per-tenant |
| канон CPIC засеян и консистентен (A2-full: 3 проекции, один seed_version) | `check_cpic_canon_consistent` | FAIL: канон CPIC (3 проекции) не засеян или рассогласован по seed_version/ссылкам. |
| PubMed egress нейтрален (нить diagnosis-hardcode B: нет сырого диагноза в запросе) | `check_pubmed_egress_neutral` | FAIL: egress-guard пропускает сырую специфику диагноза в PubMed, или онко-паттерн |
| labs freshness per-tenant (нить diagnosis-hardcode B6: онкомаркеры не в общей базе) | `check_labs_freshness_per_tenant` | FAIL: онкомаркеры в НЕЙТРАЛЬНОЙ базе лаб-свежести → навязаны всем тенантам, или |
| Oura-колонки заполняются свежими данными (#169) | `check_oura_column_completeness` | W5K-#169 (2026-05-14): свежесть Oura-колонок в daily_metrics. |
| все doc-файлы на месте, CHANGELOG и SECURITY.md валидны | `check_doc_structure` | Проверяет что ключевые doc-файлы существуют после Diataxis-реструктуризации. |
| Переводы документации не отстают от оригинала | `check_translations_fresh` | Английский перевод отстал от русского оригинала (28.09.2026, решение владельца: |
| survivorship: assessments freshness | `check_assessments_freshness` | Каждый инструмент — последнее заполнение не старше cadence_days + 14. |
| survivorship: PRO-балл в одной размерности | `check_pro_score_unit_single` | PRO-балл под одним именем хранится в ОДНОЙ размерности (§16 клауза идентичности: |
| литература: literature_search freshness | `check_literature_freshness` | Последний literature_search покрывает последний плановый запуск по живому плисту |
| литература: literature_search партнёра | `check_literature_freshness_partner` | Поиск литературы партнёра покрывает свой последний плановый запуск (решение владельца 27.09). |
| survivorship: analyzer freshness | `check_survivorship_agent_freshness` | Последний survivorship_analysis не старше 18 дней. |
| survivorship: pending proposals ageing | `check_pending_proposals_ageing` | problem_list_proposals со status='pending' старше 21 дня. |
| ЭКГ: не-синусовый ритм | `check_ecg_nonsinus` | Записи ЭКГ с тревожным ритмом (AFib / High HR) за последние 48ч → кардио-алерт. |
| survivorship: open hypotheses ageing | `check_open_hypotheses_ageing` | Гипотезы со status='open' старше 60 дней без перехода в testing. |
| hypothesis: оценка консилиумом | `check_unresolved_evaluations` | HV-6: гипотезы в testing, лабные данные есть, outcome отсутствует > 7 дней. |
| recommendation engine здоров | `check_recommendation_engine_health` | evaluate_domain_need не должен молча падать в error-path. |
| survivorship: constitution conflicts unresolved | `check_constitution_conflicts_unresolved` | memory(category='constitution_conflict') со status='open' старше 45 дней. |
| фармакогеномика синхронизирована с геномом (WFR) | `check_pharmaco_genome_sync` | Фармакогеномика синхронизирована с последним импортом генома. |
| черты генома синхронизированы с геномом (WFR, Phase F) | `check_traits_genome_sync` | Детерминированные черты (Phase F) синхронизированы с последним импортом генома. |
| wellness геномика синхронизирована с геномом (WFR, Phase G) | `check_wellness_genome_sync` | Wellness-геномика (Phase G) синхронизирована с последним импортом генома. |
| PRS синхронизированы с геномом (WFR, Phase H) | `check_prs_genome_sync` | PRS (Wave 4, Phase H) синхронизированы с последним импортом генома. |
| Свежесть бэкапа (каждый плановый запуск) | `check_backup_freshness` | Свежайший ежедневный бэкап КАЖДОГО тенанта покрывает последний плановый запуск бэкапа. |
| Размер DB < 500MB | `check_db_size` | — |
| Логи вне конфига ротации | `check_logs_all_listed` | Слепое пятно ротации: лог РАСТЁТ, а в конфиге ротации его нет. |
| Сбои бота и служб за сутки | `check_fault_journal` | Решение владельца 2026-09-28: сбои идут в ночной цикл, не в Telegram. |
| Ротация логов жива и чиста | `check_logrotate_liveness` | Ротатор жив И отработал чисто (§14, находка producer_registry 01.09). |
| Реестр классов секретов против каталогов | `check_secret_scope_matches_reality` | Объявленный класс секрета против того, что реально лежит в каталогах (02.09). |
| LLM-тракты без secret_guard не множатся (§19) | `check_llm_tracts_guarded` | Число LLM-трактов БЕЗ secret_guard не растёт (§19, решение владельца 2026-08-03). |
| Конструкторы Anthropic мимо llm_client не множатся | `check_llm_direct_constructors` | Клиент Anthropic конструируется ТОЛЬКО в llm_client — там гард секретов стоит по конструкции. |
| Блокировки LLM-гарда видны в отчёте | `check_llm_guard_blocks_reported` | Блокировки гарда не молчат: счётчик за сутки виден человеку. |
| Корреляции в отчётах обоснованы (UC-B-09) | `check_correlations_grounded` | UC-B-09: каждое `r=0.X` в свежих отчётах агентов подтверждено ПРИНЯТОЙ верой. |
| UC-B-09: ратчет журнала необоснованных корреляций | `check_ungrounded_corr_ratchet` | ЧИТАТЕЛЬ журнала — читает СОДЕРЖИМОЕ, а не длину. |
| UC-B-09: производители корреляций объявлены | `check_correlation_producers_declared` | WARN: КТО в периметре считает корреляцию — объявлен либо находка. |
| Held-out/лаги: гаситель оживления нити (validation-gate-repair) | `check_heldout_ready` | Гаситель нити validation-gate-repair (2026-08-08): оживить held-out/лаги ДАННЫМИ, не памятью. |
| SEC-датчики отработали (perms/ports/tokens) | `check_security_sensors` | SECURITY.md «Квартальный чеклист» → код (security_sensors.py). |
| журнал изменений: у каждой слитой нити есть строка | `check_changelog_freshness` | BL-DOCAGENT-DEAD-1: у каждой слитой нити с работой кода есть строка журнала. |
| карта проекта пересобрана после закрытия нити | `check_arch_map_fresh` | Карта проекта (ARCH_SNAPSHOT ru/en) пересобрана после последнего закрытия нити. |
| MDT-консилиум свеж (producer) | `check_consilium_freshness` | monthly_consilium пишет agent_report('monthly_consilium'). Молчание = гипотезы |
| календарь свеж (producer) | `check_calendar_freshness` | calendar_cache.json кормит GP-контекст; fetcher hourly. Молчание = устаревший |
| биометрия свежа у каждого тенанта (producer: oura-import.partner) | `check_tenant_biometrics_freshness` | Биометрия свежа У КАЖДОГО тенанта, а не только у владельца. |
| реестр производителей полон (context_gate_orphan) | `check_producer_registry` | context_gate_orphan: каждый scheduled-производитель покрыт датчиком или |
| пульс вотчера входа (§14) | `check_lab_intake_pulse` | §14 для вотчера входа: доказываем, что ЦИКЛ крутится, а не что процесс жив. |
| вход не слеп: новые файлы доезжают до staging (§17) | `check_lab_intake_blind_spot` | Вторая половина предиката (§17): пульс есть, а работы нет. |
| очередь ревью движется (детект-без-доставки) | `check_lab_review_queue_movement` | Очередь человека движется. Непустая очередь без движения = потерянные |
| покрытие статусов staging (ни одна строка не молчит) | `check_staging_status_coverage` | РАТЧЕТ: ни одна строка staging не лежит в статусе, который никто не стережёт. |
| цели глоссария известны канону (F-14) | `check_glossary_targets_known` | Цель подтверждённого алиаса обязана быть ИЗВЕСТНЫМ каноническим именем. |
| покрытие конверсии единиц лабов (несходимость) | `check_unit_conversion_coverage` | WARN burn-in (plan_consilium_under_manifest 2026-07-08 Фаза 1): аналит в |
| конвенция имён мочи (Urine_*) | `check_urine_name_convention` | Конвенция имён мочевого домена — Urine_* (решение владельца 2026-08-31). |
| morning_brief: gate liveness | `check_morning_brief_gate_liveness` | Гейт анти-повтора жив: context_cards пополняется. Если гейт тихо упал в |
| morning_brief: не пересказывает слова владельца | `check_brief_does_not_retell_owner` | WARN: утренний бриф открывается пересказом того, что владелец сказал системе сам. |
| patient_profile: не отстаёт от живых источников | `check_profile_reconciler_fresh` | WARN: patient_profile разошёлся с живым источником — reconciler отстал или мёртв. |
| контракт единого времени (_time_inject) | `check_time_contract` | Контракт единого времени: прод-код читает часы через _time_inject. |
| носитель проб жив и зелен (§14) | `check_probe_liveness` | §14 для НОСИТЕЛЯ ПРОБ: у датчика, на котором стоят обещания реестра, обязан быть пульс. |
| набор тестов гонялся недавно (§14 для носителя доказательств) | `check_suite_freshness` | §14 для САМОГО НАГРУЖЕННОГО датчика проекта — набора тестов. |
| ночной прогон тестов состоялся (§14) | `check_nightly_suite_liveness` | §14 для НОЧНОЙ задачи тестов (`com.larry.health.test-suite`, 00:00). |
| ночной цикл жив (§14) | `check_night_cycle_liveness` | §14 для движка ночного цикла: тихо умерший движок = падения ночью никто не |
| колокол решений жив (§14, поведенческий) | `check_doorbell_liveness` | §14 для КОЛОКОЛА, поведенческий: молчание = launchd owner_nag мёртв (WARN); |
| граница двух домов лабораторных данных цела | `check_domain_boundary` | Граница двух домов лабораторных данных стережётся (работа B, 2026-08-01). |
| у каждого класса лаб-строк есть вердикт о доме | `check_lab_class_verdicts_complete` | Каждый класс, который РЕАЛЬНО есть в данных, имеет вердикт человека. |
| проверки машины Studio дошли (машинный судья) | `check_machine_judge_alive` | Проверки машины Studio дошли до владельца: квитанция машинного прогона свежа, его находки — |

### Пороги-константы (41 шт., генерируется)

| Константа | Значение | Пояснение |
|---|---|---|
| `DATA_FRESHNESS_HOURS` | 26 | oura/apple_health: conit C1 |
| `MIN_DAYS_WITH_DATA` | 5 | из последних 7 дней должно быть данных >= N |
| `GP_WEEKLY_MAX_AGE_DAYS` | 8 | GP weekly report не старше N дней |
| `GP_MONTHLY_MAX_AGE_DAYS` | 35 | GP monthly report не старше N дней |
| `GP_REPORT_MAX_AGE_DAYS` | 8 | обратная совместимость → weekly |
| `MIN_ACTIVE_PROBLEMS` | 1 | минимум активных проблем в problem_list |
| `MIN_OPEN_TASKS` | 0 | 0 = только проверяем что таблица читается |
| `LAB_REMINDER_DAYS` | 180 | 6 мес — напоминание пересдать анализы |
| `LAB_ALERT_DAYS` | 270 | 9 мес — тревога: агент работает по stale labs |
| `PROMOTION_BACKLOG_DAYS` | 7 | staging: строка «к авто-промоуту» ждёт > N дней = затор |
| `GENOME_UPDATE_ALERT_DAYS` | 30 | genome_update_agent не запускался > N дней |
| `HYPOTHESIS_MAX_AGE_DAYS` | 14 | гипотеза без эксперимента/лога > N дней |
| `REPORT_MIN_CHARS` | 500 | минимальная длина GP-отчёта в chars |
| `PASS` | 0 | — |
| `FAIL` | 0 | — |
| `WARN` | 0 | — |
| `_ABSENCE_SURFACE_BASELINE` | 0 | замер 14.09: открытых трактов не осталось |
| `NAMING_DEBT_RATCHET` | 0 | — |
| `_PARTNER_TENANTS_AT_DECISION` | 1 | — |
| `_LAUNCHD_UNCOVERED_BASELINE` | 23 | — |
| `MACBOOK_SNAPSHOT_STALE_D` | 3 | снимок раз в 3ч; трое суток тишины уже не «ноут поспал» |
| `THREAD_COPY_GRACE_H` | 6 | два периода снимка (backup-wip каждые 3ч), решение владельца 16.09 |
| `THREAD_ORPHAN_GRACE_H` | 48 | решение владельца 22.09: свежую нить не шуметь двое суток |
| `MEMORY_EPISODIC_GROWTH_WARN` | 1500 | активных state+question; выше — пора строить Ф4.1 |
| `FDR_QUAL_DRIFT_AMP_WARN` | 2.0 | σ: порог предупреждения; выход за диапазон квалификации требует повторной проверки |
| `RAW_ARCHIVE_STALE_DAYS` | 15 | сжатие — после 14 дней (hae_checker.compress_raw_archive) + сутки запаса |
| `GENOTYPE_PROFILE_SEED` | 3 | seed (§9 п.4): пишется в system_config, читается оттуда |
| `SLEEP_DEVICE_MIXING_KNOWN` | 3 | — |
| `QUARANTINE_STUCK_DAYS` | 21 | — |
| `PASSSET_DEPTH_MIN_RATIO` | 0.6 | доля ожидаемых по календарю снимков, ниже которой история неполна |
| `PASSSET_HISTORY_MAX` | 120 | зеркало longitudinal_analysis.PASSSET_HISTORY_MAX (cap → молчим) |
| `TREATMENT_DATE_TOLERANCE_DAYS` | 45 | — |
| `DB_SIZE_WARN_MB` | 300 | WARN порог |
| `DB_SIZE_FAIL_MB` | 500 | FAIL порог |
| `LOG_UNLISTED_WARN_MB` | 1.0 | замер 01.09: вне конфига 160 файлов, НИ ОДНОГО >1 МБ — |
| `_LLM_TRACTS_UNGUARDED_BASELINE` | 20 | замер 2026-08-03 по AST: 21 тракт, гард у 1 |
| `_DIRECT_CTOR_BASELINE` | 0 | 29.09: миграция дописана (было 27 на 2026-08-03, 23 на 29.09) |
| `_CORR_WINDOW_DAYS` | 30 | — |
| `_RETELL_WINDOW_DAYS` | 3 | окно реплик владельца назад от даты брифа |
| `_RETELL_THETA_SEED` | 0.15 | доля слов первого предложения, пришедших из его реплики |
| `SUITE_STALE_DAYS` | 10 | у носителя проб недельный ритм и порог 10; свой ритм не заводим |

<!-- END AUTOGEN: integrity-sensors -->

---

## 5. ЧТО НЕ ТЕСТИРУЕТСЯ И ПОЧЕМУ

| Компонент              | Статус       | Причина                                  |
|------------------------|--------------|------------------------------------------|
| memory (наблюдения)    | Бэклог       | Механизм протухания не проработан        |
| morning report quality | Не критично  | Нарратив, не медицинские выводы          |
| Telegram bot E2E       | Не реализован | Требует test account; низкий ROI         |
| Genome annotation       | Разовая      | Данные не меняются, только аннотации     |

---

## 6. КОГДА ЗАПУСКАЮТСЯ ПРОВЕРКИ

**Автоматически, до отправки отчётов в Telegram:**
- Свежесть данных (conit) — перед каждым утренним отчётом
- Свежесть labs/genome/GP — при каждом запуске GP-агента
- PubMed верификация — после генерации каждого specialist/GP отчёта

**По расписанию (launchd):**
- integrity_tests.py — ежедневно в 07:50 (до 08:00 отчёта)

**Вручную:**
- smoke_tests.py — до/после каждого изменения кода
- check_contracts.py — при рефакторинге межмодульных сигнатур

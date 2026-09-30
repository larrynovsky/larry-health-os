CREATE TABLE daily_metrics (
            date TEXT PRIMARY KEY,
            sleep_total REAL,
            sleep_deep  REAL,
            sleep_rem   REAL,
            sleep_score INTEGER,
            hrv         REAL,
            resting_hr  REAL,
            readiness   INTEGER,
            steps       INTEGER,
            active_kcal REAL,
            spo2_avg    REAL,
            weight      REAL,
            vo2max      REAL,
            raw         TEXT
        , sleep_inbed REAL, sleep_awake REAL, sleep_core REAL, sleep_start TEXT, sleep_end TEXT, sleep_efficiency INTEGER, readiness_hrv_balance INTEGER, readiness_body_temp INTEGER, readiness_recovery_idx INTEGER, total_kcal REAL, distance_km REAL, activity_score INTEGER, breathing_disturbance REAL, stress_summary TEXT, stress_high_min INTEGER, recovery_high_min INTEGER, resilience_level TEXT, resilience_sleep_pct REAL, resilience_daytime_pct REAL, resilience_stress_pct REAL, bp_systolic REAL, bp_diastolic REAL, exercise_min REAL, cycling_km REAL, walking_speed_avg REAL, walking_step_length_avg REAL, walking_asymmetry_avg REAL, stand_min REAL, met_avg REAL, cardio_recovery_bpm REAL, six_min_walk_m REAL, stair_speed_up_ms REAL, stair_speed_down_ms REAL, bmi REAL, body_fat_pct REAL, skeletal_muscle_pct REAL, muscle_mass_kg REAL, visceral_fat REAL, body_water_pct REAL, bone_mass_kg REAL, protein_pct REAL, bmr_kcal REAL);
CREATE TABLE experiments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            hypothesis   TEXT,
            intervention TEXT,
            start_date   TEXT,
            end_date     TEXT,
            status       TEXT DEFAULT 'active',
            result       TEXT,
            notes        TEXT,
            created_at   TEXT DEFAULT (date('now'))
        , baseline_metrics TEXT, target_metrics TEXT, check_days TEXT, check_results TEXT);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE experiment_log (
            date          TEXT,
            experiment_id INTEGER,
            adhered       INTEGER DEFAULT 1,
            notes         TEXT,
            PRIMARY KEY (date, experiment_id),
            FOREIGN KEY (experiment_id) REFERENCES experiments(id)
        );
CREATE TABLE checkins (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT,
            time_of_day TEXT DEFAULT 'morning',
            question   TEXT,
            answer     TEXT,
            context    TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
CREATE TABLE patterns (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            discovered     TEXT DEFAULT (date('now')),
            category       TEXT,
            description    TEXT,
            evidence       TEXT,
            confidence     REAL DEFAULT 0.5,
            is_active      INTEGER DEFAULT 1
        );
CREATE TABLE recommendations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT,
            text         TEXT,
            category     TEXT,
            followed_up  INTEGER DEFAULT 0,
            outcome      TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_metrics_date ON daily_metrics(date);
CREATE INDEX idx_checkins_date ON checkins(date);
CREATE VIRTUAL TABLE checkins_fts
            USING fts5(question, answer, content='checkins', content_rowid='id')
/* checkins_fts(question,answer) */;
CREATE TABLE IF NOT EXISTS 'checkins_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'checkins_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'checkins_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'checkins_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
CREATE TABLE conversation_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
CREATE TABLE lab_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            source      TEXT,
            test_name   TEXT NOT NULL,
            value       REAL,
            value_text  TEXT,
            unit        TEXT,
            ref_low     REAL,
            ref_high    REAL,
            status      TEXT,
            specimen    TEXT DEFAULT 'blood',
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_lab_date ON lab_results(date);
CREATE INDEX idx_lab_name ON lab_results(test_name);
CREATE INDEX idx_lab_date_name ON lab_results(date, test_name);
CREATE TABLE memory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT DEFAULT (date('now')),
            category   TEXT NOT NULL,
            key        TEXT,
            value      TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            source     TEXT DEFAULT 'conversation',
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_memory_category ON memory(category);
CREATE INDEX idx_memory_date ON memory(date);
CREATE INDEX idx_memory_active ON memory(active);
CREATE TABLE memory_facts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_class     TEXT NOT NULL,
            key           TEXT,
            value         TEXT NOT NULL,
            confidence    REAL DEFAULT 0.8,
            valid_from    TEXT DEFAULT (date('now')),
            valid_to      TEXT,
            superseded_by INTEGER,
            source        TEXT DEFAULT 'conversation',
            subject       TEXT DEFAULT 'self',
            confirmations INTEGER DEFAULT 0,
            critical_flag INTEGER DEFAULT 0,
            temporal_class TEXT,
            active        INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_mf_class ON memory_facts(mem_class);
CREATE INDEX idx_mf_key ON memory_facts(key);
CREATE INDEX idx_mf_active ON memory_facts(active);
CREATE INDEX idx_mf_valid ON memory_facts(valid_to);
CREATE INDEX idx_mf_subject ON memory_facts(subject);
CREATE TABLE memory_consolidation_proposals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id          INTEGER,
            current_key      TEXT,
            current_value    TEXT,
            action           TEXT NOT NULL,
            canonical_key    TEXT,
            proposed_value   TEXT,
            authority_source TEXT,
            medical_flag     INTEGER DEFAULT 0,
            rationale        TEXT,
            status           TEXT DEFAULT 'pending',
            source_keys      TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_mcp_status ON memory_consolidation_proposals(status);
CREATE TABLE trend_thresholds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                metric          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                value           REAL NOT NULL,
                window_days     INTEGER NOT NULL,
                mode            TEXT NOT NULL,
                level           TEXT NOT NULL,
                domain          TEXT DEFAULT '',
                reason_template TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_date     TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                updated_at      TEXT DEFAULT (datetime('now')),
                condition_key   TEXT DEFAULT '',
                UNIQUE(metric, direction, window_days, mode, level)
            );
CREATE INDEX idx_trend_thresh_active ON trend_thresholds(active);
CREATE TABLE periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,       -- travel|treatment|experiment|stress|vacation|illness|other
            start_date  TEXT NOT NULL,       -- YYYY-MM-DD
            end_date    TEXT,                -- NULL = ongoing
            tags        TEXT DEFAULT '[]',   -- JSON array
            source      TEXT DEFAULT 'manual', -- manual|calendar|auto
            notes       TEXT,
            active      INTEGER DEFAULT 1,   -- DEPRECATED (2026-06-19, D++ hybrid), kept for backward-compat
            created_at  TEXT DEFAULT (datetime('now')),
            deleted_at  TEXT                 -- D++ (2026-06-19): NULL = valid, set = soft-deleted
        );
CREATE INDEX idx_periods_dates ON periods(start_date, end_date);
CREATE INDEX idx_periods_type  ON periods(type);
CREATE TABLE context_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,       -- YYYY-MM-DD
            ts          TEXT,                -- точное время если есть
            source      TEXT NOT NULL,       -- checkin|gmail|calendar|weather|conversation|manual
            category    TEXT NOT NULL,       -- mood|energy|stress|symptom|nutrition|social|work|weather|travel|note
            key         TEXT,                -- mood_score|energy_level|stress_level|meeting_count|temp_c|meal_quality
            value_num   REAL,               -- числовое значение
            value_text  TEXT,               -- текстовое значение
            period_id   INTEGER REFERENCES periods(id),
            tags        TEXT DEFAULT '[]',   -- JSON array
            created_at  TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_ctx_date     ON context_events(date);
CREATE INDEX idx_ctx_category ON context_events(category);
CREATE INDEX idx_ctx_key      ON context_events(key);
CREATE INDEX idx_ctx_source   ON context_events(source);
CREATE TABLE agent_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,      -- дата отчёта YYYY-MM-DD
            agent_type      TEXT NOT NULL,      -- lifestyle|specialist|therapist|synthesizer
            agent_name      TEXT NOT NULL,      -- sleep|stress|oncology|cardiology|therapist...
            period_days     INTEGER DEFAULT 7,  -- за какой период
            has_findings    INTEGER DEFAULT 0,  -- 1 = есть изменения, идёт в отчёт
            -- Обязательные поля контракта (proof of work)
            data_queried    TEXT DEFAULT '[]',  -- JSON: список запросов к БД
            pubmed_ids      TEXT DEFAULT '[]',  -- JSON: реальные PMID если были
            peers_reviewed  TEXT DEFAULT '[]',  -- JSON: какие агенты учтены
            changes_summary TEXT,               -- что изменилось vs прошлый отчёт
            -- Полный вывод
            findings        TEXT,               -- нарратив по домену
            recommendations TEXT,               -- рекомендации
            data_requests   TEXT,               -- какие данные нужны (анализы, измерения)
            raw_output      TEXT,               -- полный JSON output агента
            created_at      TEXT DEFAULT (datetime('now'))
        );
CREATE INDEX idx_reports_date  ON agent_reports(date);
CREATE INDEX idx_reports_agent ON agent_reports(agent_name);
CREATE INDEX idx_reports_type  ON agent_reports(agent_type, date);
CREATE TABLE problem_list (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id      TEXT UNIQUE NOT NULL,  -- стабильный ID: 'deep_sleep_decline'
            title           TEXT NOT NULL,
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'active',  -- active|monitoring|resolved
            priority        INTEGER DEFAULT 2,     -- 1=высокий, 2=средний, 3=низкий
            domain          TEXT,                  -- sleep|hrv|oncology|nutrition|stress...
            first_seen      TEXT NOT NULL,         -- когда впервые появилась
            last_updated    TEXT NOT NULL,         -- когда последний раз менялась
            -- Watchful waiting
            watch_trigger   TEXT,   -- условие эскалации: 'если deep < 20м три ночи подряд'
            watch_deadline  TEXT,   -- дата пересмотра
            -- Связи
            supporting_data TEXT DEFAULT '[]',  -- JSON: event IDs, report IDs
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        , review_date TEXT, plain_summary TEXT);
CREATE INDEX idx_problems_status   ON problem_list(status);
CREATE INDEX idx_problems_priority ON problem_list(priority);
CREATE INDEX idx_problems_domain   ON problem_list(domain);
CREATE TABLE tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT DEFAULT (datetime('now')),
                source      TEXT NOT NULL,        -- gp_weekly|gp_monthly|mdt|checkin|manual
                source_date TEXT,                 -- дата отчёта-источника
                type        TEXT NOT NULL,        -- question|lab_test|action|followup
                priority    TEXT DEFAULT 'medium',-- critical|high|medium|low
                content     TEXT NOT NULL,        -- текст задачи
                deadline    TEXT,                 -- дата дедлайна если известна
                status      TEXT DEFAULT 'open',  -- open|answered|completed|dismissed|snoozed
                resolved_at TEXT,
                resolved_text TEXT,               -- ответ пользователя или отметка о выполнении
                sent_at     TEXT,                 -- когда отправлено в Telegram
                followup_sent_at TEXT             -- когда отправлен follow-up
            , reason TEXT, fingerprint TEXT, resolution_type TEXT DEFAULT 'self_managed');
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_source_date ON tasks(source_date);
CREATE TABLE problem_list_proposals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   TEXT DEFAULT (datetime('now')),
                source       TEXT NOT NULL,   -- gp_weekly|gp_monthly
                proposed     TEXT NOT NULL,   -- JSON: список изменений
                status       TEXT DEFAULT 'pending',  -- pending|approved|rejected|superseded|expired
                reviewed_at  TEXT,
                review_note  TEXT,
                dedup_key        TEXT,                 -- 2026-09-01: идентичность правки (см. problems_db)
                repeats          INTEGER DEFAULT 1,    -- сколько раз предлагалось (refresh, не дубль)
                last_proposed_at TEXT,
                delivered_at     TEXT,                 -- 2026-09-23: квитанция доставки человеку
                tg_message_id    INTEGER
            );
CREATE INDEX idx_proposals_status ON problem_list_proposals(status);
CREATE INDEX idx_tasks_fingerprint ON tasks(fingerprint);

CREATE TABLE lab_monitoring_schedule (
    test_name       TEXT PRIMARY KEY,
    interval_days   INTEGER NOT NULL,
    priority        TEXT NOT NULL DEFAULT 'medium',
    source          TEXT NOT NULL DEFAULT 'default',
    effective_from  TEXT,
    note            TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE raw_snps (
            rsid        TEXT PRIMARY KEY,
            chromosome  TEXT,
            position    INTEGER,
            genotype    TEXT
        );
CREATE TABLE genetic_variants (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rsid            TEXT UNIQUE NOT NULL,
            gene            TEXT,
            genotype        TEXT,
            significance    TEXT,
            prev_significance TEXT,
            conditions      TEXT,
            domain_tags     TEXT,
            effect_allele   TEXT,
            clinical_summary TEXT,
            annotation_source TEXT DEFAULT 'myvariant',
            annotated_at    TEXT,
            updated_at      TEXT,
            ref_allele      TEXT,
            assembly        TEXT,
            effect_allele_status TEXT
        );
CREATE TABLE genome_update_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT NOT NULL,
            variants_checked INTEGER DEFAULT 0,
            variants_changed INTEGER DEFAULT 0,
            changes_json    TEXT,
            narrative       TEXT,
            sent_to_user    INTEGER DEFAULT 0
        );
CREATE INDEX idx_raw_snps_rsid ON raw_snps(rsid);
CREATE TABLE protocols (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT NOT NULL,
    behavior             TEXT NOT NULL,
    rationale            TEXT,
    frequency            TEXT,
    linked_hypothesis_id INTEGER,
    linked_experiment_id INTEGER,
    reminder_days        TEXT DEFAULT "[]",
    status               TEXT DEFAULT "active",
    created_at           TEXT,
    retired_at           TEXT,
    notes                TEXT
, review_date TEXT, domain TEXT);
CREATE TABLE consultations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                specialist_type TEXT NOT NULL,
                specialist_name TEXT,
                platform        TEXT,
                key_findings    TEXT,
                source_file     TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );
CREATE INDEX idx_consult_date ON consultations(date);
CREATE TABLE promethease_variants (
            rsnum        TEXT PRIMARY KEY,
            geno         TEXT,
            magnitude    REAL,
            repute       TEXT,
            genes        TEXT,
            genosummary  TEXT,
            topic        TEXT,
            numrefs      INTEGER,
            clinvar_diseases TEXT,
            domain_tags  TEXT,
            imported_at  TEXT
        );
CREATE INDEX idx_prom_magnitude ON promethease_variants(magnitude DESC);
CREATE INDEX idx_prom_repute    ON promethease_variants(repute);
CREATE TABLE workouts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT NOT NULL,
                activity_type TEXT,
                start_time    TEXT,
                end_time      TEXT,
                duration_min  REAL,
                distance_km   REAL,
                calories      REAL,
                avg_hr        REAL,
                max_hr        REAL,
                source        TEXT DEFAULT 'Oura',
                raw           TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );
CREATE INDEX idx_workouts_date ON workouts(date);
CREATE TABLE consult_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT,
                question     TEXT NOT NULL,
                status       TEXT DEFAULT 'pending',
                response     TEXT,
                specialist   TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            );
CREATE INDEX idx_consult_req_status ON consult_requests(status);
CREATE UNIQUE INDEX idx_lab_uniq
            ON lab_results(date, test_name, source, specimen);


-- ── Consolidation layer (Phase 0, 2026-05-10) ──────────────────────────────
CREATE TABLE IF NOT EXISTS patient_profile (
    key        TEXT PRIMARY KEY,
    value_text TEXT,
    value_json TEXT,
    category   TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    updated_by TEXT DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS system_config (
    key        TEXT PRIMARY KEY,
    value_text TEXT,
    value_num  REAL,
    value_json TEXT,
    category   TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    source     TEXT DEFAULT 'manual'
);

-- ── Medical Record (Wave 10, 2026-05-17) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS episodes_of_care (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_problem_id    TEXT,
    title                 TEXT NOT NULL,
    start_date            TEXT NOT NULL,
    end_date              TEXT,
    status                TEXT NOT NULL DEFAULT 'active',
    managing_organization TEXT,
    notes                 TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    effective_date  TEXT NOT NULL,
    effective_time  TEXT,
    recorded_at     TEXT DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'completed',
    performer       TEXT,
    performer_role  TEXT,
    location        TEXT,
    episode_id      INTEGER REFERENCES episodes_of_care(id),
    recorded_by     TEXT NOT NULL DEFAULT 'patient',
    attachments     TEXT NOT NULL DEFAULT '[]',
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS encounters (
    event_id          INTEGER PRIMARY KEY REFERENCES events(id),
    class             TEXT,
    specialty         TEXT,
    reason_text       TEXT,
    chief_complaint   TEXT,
    duration_minutes  INTEGER,
    subjective        TEXT,
    objective         TEXT,
    assessment        TEXT,
    plan              TEXT
);

CREATE TABLE IF NOT EXISTS diagnostic_events (
    event_id              INTEGER PRIMARY KEY REFERENCES events(id),
    type                  TEXT,
    modality              TEXT,
    ordering_event_id     INTEGER REFERENCES events(id),
    raw_values_ref        TEXT DEFAULT '[]',
    interpreted_report    TEXT,
    abnormal_flags        TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS event_problem_links (
    event_id    INTEGER REFERENCES events(id),
    problem_id  TEXT,
    link_type   TEXT NOT NULL DEFAULT 'reason',
    PRIMARY KEY (event_id, problem_id)
);

CREATE TABLE IF NOT EXISTS event_relationships (
    parent_event_id   INTEGER REFERENCES events(id),
    child_event_id    INTEGER REFERENCES events(id),
    relationship_type TEXT NOT NULL,
    PRIMARY KEY (parent_event_id, child_event_id)
);

CREATE TABLE IF NOT EXISTS immunizations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vaccine      TEXT NOT NULL,
    manufacturer TEXT,
    lot_number   TEXT,
    site         TEXT,
    date         TEXT NOT NULL,
    performer    TEXT,
    event_id     INTEGER REFERENCES events(id),
    notes        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

-- ── Survivorship-extension (SX-17 test schema) ─────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    severity TEXT,
    message TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    source TEXT
);

CREATE TABLE IF NOT EXISTS assessment_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id TEXT NOT NULL,
    wording_version_hash TEXT NOT NULL,
    chat_id INTEGER,
    task_id INTEGER,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    answers_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'in_progress'
);
CREATE INDEX IF NOT EXISTS idx_assess_sess_status ON assessment_sessions(status);
CREATE INDEX IF NOT EXISTS idx_assess_sess_chat ON assessment_sessions(chat_id);

-- ── medications (онкорежимы + гейт, 2026-06-18) ──────────────────────────────
CREATE TABLE IF NOT EXISTS medications (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    dosage                TEXT,
    frequency             TEXT,
    route                 TEXT,
    indication_problem_id TEXT,
    prescribing_event_id  INTEGER,
    start_date            TEXT,
    end_date              TEXT,
    status                TEXT NOT NULL DEFAULT 'active',
    notes                 TEXT,
    created_at            TEXT DEFAULT (datetime('now')),
    modality              TEXT,
    intent                TEXT,
    cycles_completed      INTEGER,
    cycles_planned        INTEGER,
    agents                TEXT,
    source                TEXT,
    confirmation          TEXT,
    updated_at            TEXT,
    gate_sent_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_medications_confirmation ON medications(confirmation);


-- ── Поток B рефакторинга (2026-06-27): таблицы/колонки, присутствующие в боевой
-- схеме, но отсутствовавшие в фикстуре. Аддитивно (IF NOT EXISTS / ALTER в конце),
-- существующие определения не затрагиваются. Источник — sqlite_master боевой health.db.

CREATE TABLE IF NOT EXISTS routing_keywords (
    domain   TEXT NOT NULL,
    keyword  TEXT NOT NULL,
    added_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (domain, keyword)
);

CREATE TABLE IF NOT EXISTS doc_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern         TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    match_on        TEXT NOT NULL DEFAULT 'filename',
    match_type      TEXT NOT NULL DEFAULT 'literal',
    specialist_name TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS lab_name_aliases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    format_id      INTEGER NOT NULL,
    raw_name       TEXT NOT NULL,
    canonical      TEXT NOT NULL,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    orphan         INTEGER NOT NULL DEFAULT 0,
    source_example TEXT,
    confirmed_at   TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(format_id, raw_name)
);

CREATE TABLE IF NOT EXISTS imported_docs (
    source_file TEXT PRIMARY KEY,
    imported_at TEXT DEFAULT (datetime('now')),
    doc_type    TEXT
);

ALTER TABLE checkins ADD COLUMN stress_score INTEGER;
ALTER TABLE checkins ADD COLUMN mood_score INTEGER;
ALTER TABLE checkins ADD COLUMN energy_score INTEGER;

CREATE TABLE IF NOT EXISTS hae_metric_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name   TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'new',
    first_seen    TEXT,
    last_seen     TEXT,
    sample_values TEXT DEFAULT '[]',
    unit          TEXT,
    alerted_at    TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS lab_formats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    detector_config TEXT DEFAULT '{}',
    parser_type     TEXT DEFAULT 'fitz',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS constitutions (
    domain         TEXT PRIMARY KEY,
    title          TEXT,
    body_md        TEXT NOT NULL,
    generated_at   TEXT DEFAULT (datetime('now')),
    source_version TEXT
);

CREATE TABLE IF NOT EXISTS consultation_sessions (
    chat_id      INTEGER PRIMARY KEY,
    session_json TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS absolute_thresholds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    value           REAL NOT NULL,
    reason_template TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_date     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT DEFAULT (datetime('now')),
    kind            TEXT NOT NULL DEFAULT 'absolute',
    baseline        TEXT,
    band_label      TEXT DEFAULT '',
    variant         TEXT DEFAULT '',
    -- провенанс нормы (norm-provenance 2026-09-02): зеркало ALTER в health_db._migrate_absolute_thresholds
    norm_kind       TEXT NOT NULL DEFAULT 'unclassified' CHECK (norm_kind IN ('reference_interval','decision_threshold','stratified_target','personal','unclassified')),
    next_review     TEXT,
    stratum         TEXT,
    UNIQUE(metric, direction, band_label, variant)
);

CREATE TABLE IF NOT EXISTS pending_doc_reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file    TEXT NOT NULL,
    proposed_type  TEXT,
    imported_at    TEXT DEFAULT (datetime('now')),
    status         TEXT DEFAULT 'needs_review',
    confirmed_type TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses_cbcr (
    memory_id        INTEGER PRIMARY KEY,
    payload          TEXT NOT NULL,
    generated_at     TEXT DEFAULT (datetime('now')),
    structural_score INTEGER,
    confidence_level TEXT,
    generated_by     TEXT,
    model            TEXT
);

CREATE TABLE IF NOT EXISTS pending_field_reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    format_id     INTEGER,
    raw_name      TEXT NOT NULL,
    value         REAL,
    unit          TEXT,
    source_file   TEXT,
    suggested     TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT DEFAULT (datetime('now')),
    tg_message_id INTEGER
);

-- Карантин мерцающих пар валид-гейта (Ш3 ремонта validation_gate, 2026-07-26):
-- состояние «пара ждёт явного вердикта», снимается только человеком/контроллером.
CREATE TABLE IF NOT EXISTS passset_quarantine (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pair         TEXT NOT NULL CHECK (trim(pair) <> ''),
    family       TEXT NOT NULL CHECK (family IN ('D','A','q_lag')),
    method_epoch TEXT NOT NULL DEFAULT '',
    entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','admitted','rejected')),
    resolved_at  TEXT,
    resolution   TEXT,
    resolved_by  TEXT,
    CHECK (status = 'pending' OR trim(coalesce(resolution,'')) <> ''),
    UNIQUE(pair, method_epoch)
);

-- ── Доведение фикстуры до боевой схемы (2026-06-28): таблицы, присутствующие
-- в боевой ~/health/data/health.db, но отсутствовавшие в фикстуре. Источник —
-- sqlite_master боевой БД. Гард tests/integration/test_fixture_schema_complete.py
-- падает на Studio, если появятся новые недостающие таблицы.

CREATE TABLE IF NOT EXISTS allergies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    substance     TEXT NOT NULL,
    reaction_type TEXT,
    severity      TEXT,
    onset_date    TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    notes         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS care_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  INTEGER,
    goals       TEXT NOT NULL DEFAULT '[]',
    activities  TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS family_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship  TEXT NOT NULL,
    condition     TEXT NOT NULL,
    age_at_onset  INTEGER,
    notes         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS genome_imports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    import_date          TEXT NOT NULL DEFAULT (date('now')),
    vcf_source           TEXT,
    phase_e_pharmaco_at  TEXT,
    phase_f_monogenic_at TEXT,
    phase_g_prs_at       TEXT,
    completed_at         TEXT,
    notes                TEXT,
    phase_h_prs_at       TEXT
);

CREATE TABLE IF NOT EXISTS pharmaco_phenotypes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    gene               TEXT NOT NULL,
    star_allele_1      TEXT,
    star_allele_2      TEXT,
    phenotype          TEXT NOT NULL,
    confidence         TEXT NOT NULL DEFAULT 'indeterminate',
    coverage_snp_count INTEGER DEFAULT 0,
    genome_import_id   INTEGER,
    computed_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS social_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    value       TEXT,
    status      TEXT,
    recorded_at TEXT DEFAULT (datetime('now'))
);

-- Добор остатка до полной боевой схемы (2026-06-28, гард-driven).

CREATE TABLE IF NOT EXISTS dashboard_edits (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT DEFAULT (datetime('now')),
        entity     TEXT NOT NULL,
        entity_id  TEXT NOT NULL,
        action     TEXT NOT NULL,
        field      TEXT,
        old_value  TEXT,
        new_value  TEXT,
        actor      TEXT DEFAULT 'dashboard'
    );

CREATE TABLE IF NOT EXISTS "hypothesis_outcomes" (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id        INTEGER NOT NULL,
            verdict          TEXT NOT NULL,
            confidence       REAL,
            evidence_json    TEXT,
            reasoning        TEXT,
            coordinator_text TEXT,
            evaluated_at     TEXT DEFAULT (date('now')),
            sent_at          TEXT,
            delivering_since TEXT,
            telegram_chat_id INTEGER
        );

CREATE TABLE IF NOT EXISTS pgs_catalog (
    pgs_id         TEXT PRIMARY KEY,
    trait_name     TEXT NOT NULL,
    trait_label    TEXT,
    num_variants   INTEGER,
    ancestry_broad TEXT,
    genome_build   TEXT DEFAULT 'GRCh38',
    downloaded_at  TEXT NOT NULL,
    trait_efo_ids  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS pgs_weights (
    id            INTEGER PRIMARY KEY,
    pgs_id        TEXT NOT NULL REFERENCES pgs_catalog(pgs_id) ON DELETE CASCADE,
    rsid          TEXT,
    chr_name      TEXT,
    chr_position  INTEGER,
    effect_allele TEXT NOT NULL,
    other_allele  TEXT,
    effect_weight REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prs_scores (
    id               INTEGER PRIMARY KEY,
    genome_import_id INTEGER NOT NULL,
    pgs_id           TEXT    NOT NULL,
    trait_label      TEXT,
    raw_score        REAL    NOT NULL,
    snps_matched     INTEGER NOT NULL,
    snps_total       INTEGER NOT NULL,
    computed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(genome_import_id, pgs_id)
);

CREATE TABLE IF NOT EXISTS trait_phenotypes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            trait            TEXT    NOT NULL,
            category         TEXT    NOT NULL DEFAULT '',
            phenotype        TEXT    NOT NULL,
            confidence       TEXT    NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json   TEXT,
            notes            TEXT,
            genome_import_id INTEGER REFERENCES genome_imports(id),
            computed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

CREATE TABLE IF NOT EXISTS vcf_discovery (
            chrom        TEXT    NOT NULL,
            pos          INTEGER NOT NULL,
            ref          TEXT    NOT NULL,
            alt          TEXT    NOT NULL,
            rsid         TEXT,
            gene         TEXT,
            significance TEXT,
            conditions   TEXT,
            genotype     TEXT,
            gq           INTEGER,
            dp           INTEGER,
            annotated_at TEXT,
            PRIMARY KEY (chrom, pos, ref, alt)
        );

CREATE TABLE IF NOT EXISTS vcf_import_log (
            phase   TEXT NOT NULL,
            step    TEXT NOT NULL,
            status  TEXT NOT NULL,
            count   INTEGER,
            message TEXT,
            ts      TEXT DEFAULT (datetime('now'))
        );

CREATE TABLE IF NOT EXISTS vcf_staging (
            chrom    TEXT    NOT NULL,
            pos      INTEGER NOT NULL,
            ref      TEXT    NOT NULL,
            alt      TEXT    NOT NULL,
            gt       TEXT    NOT NULL,
            genotype TEXT,
            gq       INTEGER,
            dp       INTEGER,
            PRIMARY KEY (chrom, pos, ref, alt)
        );

CREATE TABLE IF NOT EXISTS wellness_phenotypes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            gene             TEXT    NOT NULL,
            trait            TEXT    NOT NULL,
            phenotype        TEXT    NOT NULL,
            confidence       TEXT    NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json   TEXT,
            notes            TEXT,
            genome_import_id INTEGER REFERENCES genome_imports(id),
            computed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );


-- 2026-06-28: фикс footgun upsert_workout — UNIQUE(date,start_time), после
-- которого ON CONFLICT DO NOTHING реально дедуплицирует. См. _migrate_workouts_dedup.
CREATE UNIQUE INDEX IF NOT EXISTS idx_workouts_unique ON workouts(date, start_time);


-- 2026-06-30: фикстура отставала — lab_results_staging создаётся лениво
-- (health_db.py CREATE TABLE IF NOT EXISTS) при первом прогоне лаб-экстракции,
-- поэтому всплыла только когда прод реально её создал. См. test_fixture_schema_covers_prod_tables.
CREATE TABLE IF NOT EXISTS lab_results_staging (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    page              INTEGER,
    raw_line          TEXT,
    bbox              TEXT,
    date              TEXT NOT NULL,
    panel             TEXT,
    raw_name          TEXT,
    canonical_name    TEXT,
    value             REAL,
    value_text        TEXT,
    unit              TEXT,
    ref_low           REAL,
    ref_high          REAL,
    doc_flag          TEXT,
    pass1_value       REAL,
    pass2_value       REAL,
    parser_value      REAL,
    value_agreement   TEXT,
    unit_agreement    TEXT,
    ref_agreement     TEXT,
    field_evidence    TEXT,
    oracle_status     TEXT,
    oracle_notes      TEXT,
    confidence        TEXT,
    review_status     TEXT DEFAULT 'pending',
    date_source       TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stg_run ON lab_results_staging(run_id);
CREATE INDEX IF NOT EXISTS idx_stg_src ON lab_results_staging(source_file);
CREATE INDEX IF NOT EXISTS idx_stg_review ON lab_results_staging(review_status);

-- 2026-07-04 BL-LAB-CANON-2: спец-панели (микробиом/аутоантитела/метаболомика/…)
CREATE TABLE IF NOT EXISTS specialized_lab_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    source TEXT,
    panel_type TEXT,
    specimen TEXT,
    analyte_raw TEXT,
    analyte_canonical TEXT,
    value REAL,
    value_text TEXT,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    flag TEXT,
    method TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_speclab_date ON specialized_lab_results(date);
CREATE INDEX IF NOT EXISTS idx_speclab_panel ON specialized_lab_results(panel_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_speclab_uniq ON specialized_lab_results(date, analyte_raw, source, panel_type);

-- 2026-07-01: дедуп распознанных доков по content-hash (lab_backfill._ensure_recognized_docs)
CREATE TABLE IF NOT EXISTS recognized_docs (sha256 TEXT PRIMARY KEY, source_file TEXT, run_id TEXT, recognized_at TEXT);


-- 2026-07-04: genome_source — трекинг источника генома (файл/сигнатура/snp_count),
-- добавлена параллельной сессией (genome_parser). Фикстура отставала. См.
-- test_fixture_schema_covers_prod_tables.
CREATE TABLE IF NOT EXISTS genome_source (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT,
    signature   TEXT,
    filename    TEXT,
    snp_count   INTEGER,
    imported_at TEXT
);


-- 2026-07-04: trend_thresholds — оконные/трендовые пороги алертов (BL-ALERT-TREND-1).
-- См. test_fixture_schema_covers_prod_tables.
CREATE TABLE IF NOT EXISTS trend_thresholds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    value           REAL NOT NULL,
    window_days     INTEGER NOT NULL,
    mode            TEXT NOT NULL,
    level           TEXT NOT NULL,
    domain          TEXT DEFAULT '',
    reason_template TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_date     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(metric, direction, window_days, mode, level)
);

-- safety-net-thresholds (E1 2026-07-18): пороги лаб-трендов (pct за N ИЗМЕРЕНИЙ, не дней).
CREATE TABLE IF NOT EXISTS lab_trend_thresholds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    pct_change      REAL NOT NULL,
    n_readings      INTEGER NOT NULL,
    level           TEXT NOT NULL,
    reason_template TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL,
    source_date     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT DEFAULT (datetime('now')),
    near_boundary_share REAL,
    near_boundary_source TEXT,
    UNIQUE(metric, direction)
);

CREATE TABLE IF NOT EXISTS ecg_readings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time     TEXT NOT NULL,
    end_time       TEXT,
    date           TEXT,
    classification TEXT NOT NULL,
    severity       TEXT,
    avg_hr         REAL,
    n_voltage      INTEGER,
    sampling_hz    INTEGER,
    source         TEXT,
    source_file    TEXT,
    raw_meta       TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(start_time, source)
);


-- ── context_cards (2026-07-13): анти-повтор утреннего брифа ──────────────────
-- Зеркалит _migrate_context_cards() в health_db.py. Сторож синхронности:
-- tests/integration/test_fixture_schema_covers_prod_tables. tenant_id НЕ хранится
-- (БД=тенант по HEALTH_DATA_DIR; хранить = дубль идентичности → split-brain).
CREATE TABLE context_cards (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    date               TEXT NOT NULL,
    provider           TEXT NOT NULL,
    semantic_key       TEXT NOT NULL,
    lane               TEXT NOT NULL CHECK (lane IN ('safety','routine')),
    origin             TEXT NOT NULL DEFAULT 'internal'
                       CHECK (origin IN ('internal','personal_external','official','third_party','curated','computed')),
    delivery           TEXT NOT NULL DEFAULT 'computed'
                       CHECK (delivery IN ('stable_api','cached_freshness','scrape_risk','computed','manual_staleness')),
    relevance          REAL,
    importance         REAL,
    severity           REAL,
    status             TEXT NOT NULL DEFAULT 'candidate'
                       CHECK (status IN ('candidate','shown','suppressed_cooldown','suppressed_gate','degraded_stale')),
    gate_reason        TEXT,
    evidence_summary   TEXT,
    allowed_claims     TEXT,
    forbidden_claims   TEXT,
    recurrence_state   TEXT NOT NULL DEFAULT 'none'
                       CHECK (recurrence_state IN ('none','new_alert','still_active','worsened','resolved')),
    last_value         TEXT,
    last_shown_at      TEXT,
    escalation_level   INTEGER NOT NULL DEFAULT 0,
    cooldown_until     TEXT,
    expires_at         TEXT,
    rendered_text_hash TEXT,
    created_at         TEXT DEFAULT (datetime('now')),
    UNIQUE(semantic_key, date)
);
CREATE INDEX IF NOT EXISTS idx_context_cards_date ON context_cards(date);
CREATE INDEX IF NOT EXISTS idx_context_cards_key ON context_cards(semantic_key, date);
CREATE INDEX IF NOT EXISTS idx_context_cards_status ON context_cards(status, date);

-- ── device_location (2026-07-14): сигнал локации с iOS Shortcut (travel-режим) ──
-- Зеркалит _ensure_device_location_table() в health_db.py.
CREATE TABLE device_location (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject     TEXT DEFAULT 'self',
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    accuracy_m  REAL,
    name        TEXT,
    is_home     INTEGER,
    source      TEXT DEFAULT 'device_gps',
    received_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_device_location_recv ON device_location(subject, received_at);

-- data-in-code-9: пол безопасности питания + склад сгенерированных диет-правил (2026-07-15)
CREATE TABLE IF NOT EXISTS food_floor (
    id           TEXT PRIMARY KEY,
    why          TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    trigger_json TEXT NOT NULL,
    assert_json  TEXT NOT NULL,
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS generated_food_rules (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key           TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'shadow',
    payload_json       TEXT NOT NULL,
    floor_ok           INTEGER NOT NULL DEFAULT 0,
    critique           TEXT NOT NULL DEFAULT '',
    generated_by       TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    model_version_date TEXT NOT NULL DEFAULT '',
    data_sources       TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT DEFAULT (datetime('now')),
    updated_at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_genfood_key ON generated_food_rules(rule_key, status);


-- brief-neutralization Фаза 2: единый clinical_kb (индекс состояний + доменные срезы).
-- Совпадает с clinical_kb.seed_clinical_kb (CREATE IF NOT EXISTS). Держать синхронно.
CREATE TABLE IF NOT EXISTS clinical_kb_conditions (
    id             TEXT PRIMARY KEY,
    temporal_class TEXT NOT NULL DEFAULT 'standing',
    description    TEXT NOT NULL DEFAULT '',
    trigger_json   TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clinical_kb (
    id             TEXT PRIMARY KEY,
    domain         TEXT NOT NULL,
    condition_key  TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT 'frame_rule',
    payload_json   TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    temporal_class TEXT NOT NULL DEFAULT 'standing',
    valid_from     TEXT,
    review_date    TEXT,
    superseded_by  TEXT,
    updated_at     TEXT DEFAULT (datetime('now'))
);

-- ── CPIC reference (нить diagnosis-hardcode A2-full): канон CPIC, 3 проекции ──
CREATE TABLE IF NOT EXISTS cpic_drug_catalog (
    drug_id      TEXT PRIMARY KEY,
    drug_display TEXT NOT NULL,
    drug_search  TEXT NOT NULL,
    gene         TEXT NOT NULL,
    seed_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cpic_drug_risk (
    gene            TEXT NOT NULL,
    phenotype_class TEXT NOT NULL,
    drug_id         TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    recommendation  TEXT NOT NULL,
    seed_version    TEXT NOT NULL,
    PRIMARY KEY (drug_id, phenotype_class)
);
CREATE TABLE IF NOT EXISTS cpic_gene_implication (
    gene             TEXT NOT NULL,
    phenotype_match  TEXT NOT NULL,
    implication_text TEXT NOT NULL,
    seed_version     TEXT NOT NULL,
    PRIMARY KEY (gene, phenotype_match)
);

-- visual/symptom-intake feature (WP2–WP6, этап2) — added 2026-07-23 to close fixture-drift gate
CREATE TABLE IF NOT EXISTS visual_case (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id               INTEGER,
    tenant                TEXT,
    domain                TEXT,
    region                TEXT,
    status                TEXT DEFAULT 'open',
    hypothesis_memory_id  INTEGER,
    session_json          TEXT,
    opened_at             TEXT DEFAULT (datetime('now')),
    updated_at            TEXT DEFAULT (datetime('now')),
    followup_reminded_at  TEXT,
    decision_requested_at TEXT
);
CREATE TABLE IF NOT EXISTS visual_domains (
    domain         TEXT PRIMARY KEY,
    region_options TEXT,
    dialog_params  TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS visual_photo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       INTEGER,
    path          TEXT,
    sha256        TEXT UNIQUE,
    exif_stripped INTEGER DEFAULT 0,
    taken_at      TEXT DEFAULT (datetime('now'))
);

-- 2026-07-29: таблиц справочника LOINC (loinc_terms / loinc_synonyms / loinc_ru)
-- здесь НЕТ намеренно. Справочник вынесен в отдельный общий файл loinc.db, и его
-- присутствие в схеме КАНОНА было бы не удобством, а ловушкой: SQLite ищет
-- неквалифицированное имя сначала в main, поэтому пустая таблица-двойник в каноне
-- перехватила бы чтение у настоящего справочника — зелёный тест на пустом
-- словаре (§12). Датчик check_reference_tables_not_in_canon стережёт это.
CREATE TABLE IF NOT EXISTS lab_name_loinc (
    our_name    TEXT NOT NULL,
    unit        TEXT,
    specimen    TEXT NOT NULL DEFAULT 'blood',
    loinc_num   TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    rule        TEXT,
    decided_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (our_name, unit, specimen)
);

-- 2026-07-31 (нить validation-gate-repair): журнал вмешательств в ЗНАЧЕНИЯ данных.
-- Заводится здесь ЗАРАНЕЕ, до первого init_db на боевой БД: иначе гард дрейфа
-- покраснел бы ночью после деплоя, и красный был бы мой собственный.
-- `home` обязателен — у daily_metrics два дома (таблица и data/daily_metrics/*.json),
-- откат без указания дома неполон. Значения — JSON-скаляры, чтобы «было 0.0»
-- и «поля не было» оставались различимы. Определение зеркалит health_db._ensure_data_repair_log.
CREATE TABLE IF NOT EXISTS data_repair_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL CHECK(run_id <> ''),
    repaired_at TEXT NOT NULL DEFAULT (datetime('now')),
    home        TEXT NOT NULL CHECK(home <> ''),
    entity      TEXT NOT NULL CHECK(entity <> ''),
    field       TEXT NOT NULL CHECK(field <> ''),
    old_value   TEXT NOT NULL,
    new_value   TEXT NOT NULL,
    reason      TEXT NOT NULL CHECK(reason <> ''),
    reverted_at TEXT
);

CREATE TABLE IF NOT EXISTS lab_domain_verdicts (
                panel_type TEXT PRIMARY KEY,
                home       TEXT NOT NULL CHECK (home IN ('canon','specialized')),
                decided_on TEXT NOT NULL,
                oracle     TEXT NOT NULL,
                rationale  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

-- Журнал необоснованных корреляций (UC-B-09, 2026-08-08). Дом схемы — health_db.migrate_v2;
-- здесь копия для фикстуры, её равенство боевой стережёт
-- tests/integration/test_fixture_schema_complete.py.
CREATE TABLE IF NOT EXISTS ungrounded_correlations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT NOT NULL,
    agent_type      TEXT NOT NULL,
    value           REAL NOT NULL,
    belief_n        INTEGER,
    belief_accepted INTEGER,
    is_baseline     INTEGER DEFAULT 0,
    logged_at       TEXT NOT NULL,
    UNIQUE(report_date, agent_type, value)
);

-- Журнал вердиктов «человеку плохо от системы» (service_trouble, 2026-08-02). Дом схемы —
-- service_trouble._DDL; здесь копия для фикстуры (test_fixture_schema_complete.py).
-- Таблица создалась на Studio при первом вердикте, фикстура отставала до 2026-08-30.
CREATE TABLE IF NOT EXISTS service_trouble (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT DEFAULT (datetime('now')),
    tenant      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    action      TEXT NOT NULL,
    why         TEXT,
    signals     TEXT,
    outcome     TEXT,
    outcome_at  TEXT
);

-- Решения врача по наблюдению (нить treatment-facts, 2026-08-30). Дом схемы — health_db.migrate_v2.
CREATE TABLE IF NOT EXISTS surveillance_decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    title        TEXT NOT NULL,
    decision     TEXT NOT NULL CHECK(decision IN ('defer','do','stop')),
    decided_by   TEXT NOT NULL,
    decided_on   TEXT NOT NULL,
    valid_until  TEXT,
    rationale    TEXT,
    source       TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    retired_at   TEXT
);

-- norm-from-documents 2026-09-02: реестр документов нормы (зеркало health_db._migrate_norm_documents)
CREATE TABLE IF NOT EXISTS norm_documents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT,
    issued          TEXT,
    url             TEXT,
    local           TEXT,
    kind            TEXT,
    applies_to      TEXT,
    sha256          TEXT,
    next_check_days INTEGER,
    registered_at   TEXT DEFAULT (datetime('now')),
    last_check      TEXT,
    next_check      TEXT,
    remote_state    TEXT
);

-- 2026-09-03: вердикт о норме аналита «нормы нет / отложено до даты» (зеркало
-- health_db._migrate_analyte_norm_verdicts; равенство стережёт test_fixture_schema_complete)
CREATE TABLE IF NOT EXISTS analyte_norm_verdicts (
    analyte    TEXT PRIMARY KEY,
    verdict    TEXT NOT NULL CHECK (verdict IN ('deferred','no_norm')),
    rationale  TEXT NOT NULL CHECK (rationale <> ''),
    oracle     TEXT NOT NULL CHECK (oracle <> ''),
    decided_on TEXT NOT NULL,
    review_at  TEXT NOT NULL CHECK (review_at > decided_on),
    created_at TEXT DEFAULT (datetime('now'))
);

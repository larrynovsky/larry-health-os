"""
tests/fixtures/db.py — fixture `db` для in-tmp-file SQLite со схемой Health OS.

**Зачем не in-memory:** некоторые функции health_db делают `with get_conn()`
несколько раз подряд. In-memory `:memory:` БД исчезает после закрытия первого
коннекта. Используем tmp-файл — он живёт до конца теста, потом удаляется
автоматически через pytest tmp_path.

**Подмена пути БД:** в health_db.py путь к БД — `DB_PATH` (модуль-константа,
вычисляется из `HEALTH_DATA_DIR` env). Fixture monkeypatches `DB_PATH` на
tmp-путь, и реальные функции health_db работают с тестовой БД.

**Single-primary guard:** в conftest.py выставлено `ALLOW_WRITE_NONPRIMARY=1`,
поэтому write-операции через `health_db.get_conn()` работают и на MacBook.
NB: это снимает только WRITE-guard. Чтобы сьют вообще СОБРАЛСЯ off-Studio, нужен
ещё `HEALTH_DATA_DIR` на локальный (не-iCloud) путь — иначе `_validate_db_path`
роняет импорт health_db на коллекции. См. шапку conftest.py.

Использование:

    def test_get_day_returns_inserted_row(db):
        db.add_daily_metrics("2026-05-08", hrv=25, sleep_total=7.5)
        import health_db
        row = health_db.get_day("2026-05-08")
        assert row["hrv"] == 25

    def test_lab_alerts(db):
        db.add_lab_result("2026-05-01", "WBC", 4.2, ref_low=4.0, ref_high=10.0, flagged=False)
        ...
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


_SCHEMA_PATH = Path(__file__).parent / "health_schema.sql"


class Db:
    """Удобная обёртка над тестовой БД: builders + raw connection."""

    def __init__(self, path: Path):
        self.path = path

    def conn(self) -> sqlite3.Connection:
        """Сырое соединение — для прямых SELECT в тестах."""
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.conn() as c:
            return c.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.conn() as c:
            return c.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(sql, params).fetchall()

    def count(self, table: str, where: str = "1=1", params: tuple = ()) -> int:
        with self.conn() as c:
            r = c.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()
            return int(r["n"])

    # ── Builders ────────────────────────────────────────────────────────────

    def add_daily_metrics(self, date_str: str, **fields: Any) -> None:
        """Вставить или обновить строку в daily_metrics.

        fields: hrv, sleep_total, sleep_deep, sleep_rem, sleep_score, steps,
                readiness, resting_hr, bp_systolic, bp_diastolic, ...
        """
        cols = ["date"] + list(fields.keys())
        vals = [date_str] + list(fields.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO daily_metrics ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )

    def add_lab_result(self, date_str: str, test_name: str, value: float, **extras: Any) -> int:
        """Вставить строку в lab_results. Возвращает rowid.

        Реальная схема: id, date, source, test_name, value, value_text, unit,
                        ref_low, ref_high, status, notes, created_at.
        Признак выхода за норму: status='high'/'low'/'critical'/None (нет флага `flagged`).
        """
        defaults = {"source": "test", "value_text": None, "unit": None,
                    "ref_low": None, "ref_high": None, "status": None, "notes": None}
        defaults.update(extras)
        cols = ["date", "test_name", "value"] + list(defaults.keys())
        vals = [date_str, test_name, value] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO lab_results ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            return int(cur.lastrowid)

    def add_problem(self, problem_id: str, title: str, **extras: Any) -> None:
        """Вставить запись в problem_list.

        Реальная схема: priority — INTEGER (1=high, 2=medium, 3=low),
        first_seen и last_updated NOT NULL (даём today по умолчанию).
        """
        from datetime import date as _date
        today = str(_date.today())
        defaults = {"description": None, "status": "active", "priority": 2,
                    "domain": None, "first_seen": today, "last_updated": today,
                    "watch_trigger": None, "watch_deadline": None,
                    "supporting_data": "[]", "notes": None, "review_date": None}
        defaults.update(extras)
        cols = ["problem_id", "title"] + list(defaults.keys())
        vals = [problem_id, title] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO problem_list ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )

    def add_checkin(self, date_str: str, question: str, answer: str, **extras: Any) -> None:
        defaults = {"context": None, "time_of_day": "evening"}
        defaults.update(extras)
        cols = ["date", "question", "answer"] + list(defaults.keys())
        vals = [date_str, question, answer] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            c.execute(
                f"INSERT INTO checkins ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )

    def add_genetic_variant(self, rsid: str, **fields: Any) -> None:
        defaults = {
            "gene": None, "genotype": None, "significance": None,
            "conditions": None, "clinical_summary": None,
            "annotation_source": "test",
        }
        defaults.update(fields)
        cols = ["rsid"] + list(defaults.keys())
        vals = [rsid] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO genetic_variants ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )



    # ── Dashboard-specific builders (Wave 9) ───────────────────────────────

    def add_profile(self, key: str, value_text: str | None = None,
                    value_json: str | None = None, category: str = "test",
                    updated_by: str = "test") -> None:
        """Вставить запись в patient_profile."""
        with self.conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO patient_profile
                   (key, value_text, value_json, category, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, datetime('now'), ?)""",
                (key, value_text, value_json, category, updated_by),
            )

    def add_hypothesis(self, payload: dict, active: int = 1,
                       key: str = "test_hyp", confidence: float = 0.8) -> int:
        """Вставить hypothesis в memory. Возвращает id.

        payload — dict с CBCR-полями (observation, mechanism, prediction, test).
        Сериализуется в memory.value как JSON.
        """
        import json as _json
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO memory (category, key, value, confidence,
                                       source, active, created_at, updated_at)
                   VALUES ('hypothesis', ?, ?, ?, 'test', ?,
                           datetime('now'), datetime('now'))""",
                (key, _json.dumps(payload, ensure_ascii=False), confidence, active),
            )
            return cur.lastrowid

    def add_task(self, content: str, status: str = "open", source: str = "test",
                 priority: str = "medium", **extras: Any) -> int:
        """Вставить задачу в tasks. Возвращает id."""
        defaults = {"type": "action", "deadline": None,
                    "resolved_at": None, "resolved_text": None}
        defaults.update(extras)
        cols = ["content", "status", "source", "priority"] + list(defaults.keys())
        vals = [content, status, source, priority] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO tasks ({','.join(cols)}, created_at) VALUES ({placeholders}, datetime('now'))",
                vals,
            )
            return cur.lastrowid

    def add_protocol(self, title: str, behavior: str = "test behavior",
                     status: str = "active", **extras: Any) -> int:
        """Вставить protocol. Возвращает id."""
        defaults = {"rationale": None, "frequency": "daily",
                    "linked_hypothesis_id": None, "linked_experiment_id": None,
                    "domain": None, "notes": None}
        defaults.update(extras)
        cols = ["title", "behavior", "status"] + list(defaults.keys())
        vals = [title, behavior, status] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO protocols ({','.join(cols)}, created_at) VALUES ({placeholders}, datetime('now'))",
                vals,
            )
            return cur.lastrowid

    def add_experiment(self, name: str, status: str = "active",
                       start_date: str = "2026-01-01", **extras: Any) -> int:
        """Вставить experiment. Возвращает id."""
        defaults = {"hypothesis": None, "intervention": None, "end_date": None,
                    "result": None, "notes": None}
        defaults.update(extras)
        cols = ["name", "status", "start_date"] + list(defaults.keys())
        vals = [name, status, start_date] + list(defaults.values())
        placeholders = ",".join("?" * len(cols))
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO experiments ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            return cur.lastrowid

    def add_consultation(self, date_str: str, specialist_type: str = "general",
                         key_findings: str = "test findings",
                         specialist_name: str | None = None) -> int:
        """Вставить приём врача в медкарту (events + encounters) тем же писателем, что в бою.
        Возвращает id события. До 27.09 писал в таблицу consultations — второй дом."""
        import consultations_db
        return consultations_db.save_consultation(date_str, specialist_type,
                                                  specialist_name=specialist_name,
                                                  key_findings=key_findings)

    def add_period(self, name: str, type_: str = "experiment",
                   start_date: str = "2026-01-01", end_date: str | None = None,
                   notes: str | None = None, tags: str = "[]",
                   active: int = 1) -> int:
        """Вставить period. Возвращает id. tags — JSON-string."""
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO periods
                   (name, type, start_date, end_date, tags, source, notes, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 'test', ?, ?, datetime('now'))""",
                (name, type_, start_date, end_date, tags, notes, active),
            )
            return cur.lastrowid



    # ── Medical Record builders (Wave 10) ───────────────────────────────────

    def add_episode(self, title: str, primary_problem_id: str | None = None,
                    start_date: str = "2026-01-01", end_date: str | None = None,
                    status: str = "active", **extras: Any) -> int:
        """INSERT в episodes_of_care. Возвращает id."""
        defaults = {"managing_organization": None, "notes": None}
        defaults.update(extras)
        cols = ["title", "primary_problem_id", "start_date", "end_date", "status"] + list(defaults.keys())
        vals = [title, primary_problem_id, start_date, end_date, status] + list(defaults.values())
        ph = ",".join("?" * len(cols))
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO episodes_of_care ({','.join(cols)}, created_at) "
                f"VALUES ({ph}, datetime('now'))",
                vals,
            )
            return cur.lastrowid

    def add_event(self, event_type: str, effective_date: str = "2026-05-01",
                  status: str = "completed", episode_id: int | None = None,
                  performer: str | None = None, performer_role: str | None = None,
                  location: str | None = None, notes: str | None = None,
                  recorded_by: str = "test", attachments: str = "[]") -> int:
        """INSERT в events. Возвращает id."""
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO events
                   (event_type, effective_date, status, episode_id,
                    performer, performer_role, location, notes,
                    recorded_by, attachments)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_type, effective_date, status, episode_id,
                 performer, performer_role, location, notes, recorded_by, attachments),
            )
            return cur.lastrowid

    def add_encounter(self, event_id: int, specialty: str | None = None,
                      class_: str | None = "ambulatory",
                      subjective: str | None = None, objective: str | None = None,
                      assessment: str | None = None, plan: str | None = None,
                      reason_text: str | None = None,
                      chief_complaint: str | None = None,
                      duration_minutes: int | None = None) -> None:
        """INSERT в encounters (FK event_id)."""
        with self.conn() as c:
            c.execute(
                """INSERT INTO encounters
                   (event_id, class, specialty, reason_text, chief_complaint,
                    duration_minutes, subjective, objective, assessment, plan)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, class_, specialty, reason_text, chief_complaint,
                 duration_minutes, subjective, objective, assessment, plan),
            )

    def add_diagnostic_event(self, event_id: int, type_: str = "lab",
                             modality: str | None = None,
                             interpreted_report: str | None = None,
                             abnormal_flags: str = "[]",
                             raw_values_ref: str = "[]",
                             ordering_event_id: int | None = None) -> None:
        """INSERT в diagnostic_events (FK event_id)."""
        with self.conn() as c:
            c.execute(
                """INSERT INTO diagnostic_events
                   (event_id, type, modality, ordering_event_id,
                    raw_values_ref, interpreted_report, abnormal_flags)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_id, type_, modality, ordering_event_id,
                 raw_values_ref, interpreted_report, abnormal_flags),
            )

    def add_event_problem_link(self, event_id: int, problem_id: str,
                                link_type: str = "reason") -> None:
        """INSERT в event_problem_links."""
        with self.conn() as c:
            c.execute(
                """INSERT INTO event_problem_links (event_id, problem_id, link_type)
                   VALUES (?, ?, ?)""",
                (event_id, problem_id, link_type),
            )

    def add_event_relationship(self, parent_event_id: int, child_event_id: int,
                                relationship_type: str = "followup_to") -> None:
        """INSERT в event_relationships."""
        with self.conn() as c:
            c.execute(
                """INSERT INTO event_relationships
                   (parent_event_id, child_event_id, relationship_type)
                   VALUES (?, ?, ?)""",
                (parent_event_id, child_event_id, relationship_type),
            )

    def add_immunization(self, vaccine: str, date_str: str = "2026-01-01",
                         manufacturer: str | None = None,
                         site: str | None = None, performer: str | None = None,
                         event_id: int | None = None) -> int:
        """INSERT в immunizations. Возвращает id."""
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO immunizations
                   (vaccine, manufacturer, site, date, performer, event_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (vaccine, manufacturer, site, date_str, performer, event_id),
            )
            return cur.lastrowid


def _build_schema(db_path: Path) -> None:
    """Применяет CREATE TABLE-скрипты к новой БД.

    Удаляем строки про `sqlite_sequence` — служебную таблицу SQLite,
    которая создаётся автоматически при AUTOINCREMENT. Прямой CREATE TABLE
    по ней даёт OperationalError.
    """
    import re
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    # В нашей schema это однострочный statement: `CREATE TABLE sqlite_sequence(name,seq);`
    cleaned = re.sub(
        r"^\s*CREATE\s+TABLE\s+sqlite_sequence\s*\([^)]*\)\s*;\s*$",
        "",
        schema_sql,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    with sqlite3.connect(db_path) as c:
        c.executescript(cleaned)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Db:
    """
    Создаёт чистую SQLite-БД во временном каталоге, применяет схему,
    подменяет `health_db.DB_PATH` и `HEALTH_DATA_DIR` env, чтобы реальный
    health_db API работал с тестовой БД.

    После теста tmp_path удаляется автоматически через pytest.
    """
    health_dir = tmp_path / "health"
    data_dir = health_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "health.db"

    _build_schema(db_path)

    # Подмена env (для случаев, когда модуль перечитывается)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(health_dir))

    # Подмена константы в уже импортированном health_db
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", db_path)
    monkeypatch.setattr(health_db, "_HEALTH_DIR", health_dir)
    monkeypatch.setattr(health_db, "ICLOUD", health_dir)
    monkeypatch.setattr(health_db, "METRICS_DIR", data_dir / "daily_metrics")

    # §9 поток F: засеять absolute_thresholds как в бою (init_db делает это же),
    # чтобы код, читающий клинические пороги через get_threshold, не падал KeyError
    # в тестах. Идемпотентно (INSERT OR IGNORE). Тест, которому нужно ДРУГОЕ значение
    # порога, должен его UPDATE/REPLACE-нуть (а не INSERT — иначе коллизия по UNIQUE).
    health_db._seed_absolute_thresholds()
    # NB (safety-net-thresholds E1): safety_net-пороги СЮДА не сеются намеренно —
    # тесты db_config_api считают точный brief-контент absolute_thresholds. Тесты, которым
    # нужны safety-пороги (loader'ы БД-пути), сеют их локально (см. sdb-фикстуру в
    # test_safety_net_thresholds.py). Таблица lab_trend_thresholds в схеме есть (пустая ок).

    # brief-neutralization Фаза 2: medical_frame/food_genome читают clinical_kb (условие→рамка,
    # shared-нутрициология). init_db сеет его в бою — фикстура обязана тоже, иначе рамка пуста.
    try:
        import clinical_kb
        clinical_kb.seed_clinical_kb()
    except Exception:  # silent-ok: сид kb не должен ронять фикстуру (тесты без food-пути)
        pass

    return Db(db_path)

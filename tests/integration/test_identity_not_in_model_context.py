"""Имя человека и точная дата рождения не уходят в контекст модели (нить identity-out-of-llm, 04.10.2026).

Модели, которая судит о здоровье, имя не нужно; возраст нужен, точная дата рождения — нет.
До нити имя ехало в модель из семи сборщиков: «Пациент: <имя>», «Сообщение от <имя>»,
«Имя: …» и дата рождения «(р. дд.мм.гггг)» в пакете консилиума. Здесь две проверки:
  (1) поведение — каждый сборщик, получив профиль с вымышленным уникальным именем,
      не выпускает ни имени, ни даты рождения, но возраст оставляет;
  (2) периметр — новый читатель identity.name вне человеческих поверхностей краснеет
      ратчетом (урок C-77: идентификаторы уже уезжали в модель путём, которого не было
      в переписи). Граница: ратчет видит чтение по ключу, а не профиль, переданный в
      промпт целиком; такой путь ловит только (1) — и только у перечисленных сборщиков.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import health_db

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
FAKE_NAME = "Зюзикова Тестомира"
BIRTH = "1961-03-17"
LEAKS = ("Зюзиков", "Тестомир", "1961-03-17", "17.03.1961")


def _seed():
    health_db.upsert_profile("identity.name", value_text=FAKE_NAME, category="identity")
    health_db.upsert_profile("identity.birth_date", value_text=BIRTH, category="identity")
    health_db.upsert_profile("identity.location", value_text="Исландия", category="identity")
    health_db.upsert_profile("medical.diagnosis", value_text="Диагноз X", category="medical")


def _assert_clean(text: str, where: str):
    hits = [text[max(0, text.find(bad) - 80):text.find(bad) + 40] for bad in LEAKS if bad in text]
    assert not hits, f"{where}: в контекст модели уходит имя или дата рождения: {hits}"


def test_patient_brief_has_age_not_name(db):
    import patient_context as pc
    _seed()
    t = pc.build_patient_brief()
    _assert_clean(t, "patient_context.build_patient_brief")
    assert "лет" in t and "Исландия" in t


def test_gp_header_and_problem_list_reviewer(db):
    import gp_agent
    import gp_context
    _seed()
    h = gp_context._patient_header()
    _assert_clean(h, "gp_context._patient_header")
    assert "лет" in h
    _assert_clean(gp_agent._build_problem_list_reviewer_prompt(), "gp_agent._build_problem_list_reviewer_prompt")


def test_chat_payload(db):
    import hai_chat
    _seed()
    _assert_clean(hai_chat.assembled_context_text("как я спал?", include_data=True), "hai_chat")


def test_lifestyle_block(db):
    import _lifestyle_profile as lp
    _seed()
    _assert_clean(lp.build_profile_block(), "_lifestyle_profile.build_profile_block")


def test_consilium_package_has_age_not_birthdate(db):
    import wellally_consult as wc
    _seed()
    t = wc._build_data_package()
    _assert_clean(t, "wellally_consult._build_data_package")
    assert "лет" in t


def test_consult_prep_visit_report(db, monkeypatch, tmp_path):
    import consult_prep
    sent: list[str] = []

    class _Spy:
        class messages:  # noqa: N801 — форма SDK
            @staticmethod
            def create(**kw):
                sent.append(repr(kw.get("system", "")) + repr(kw.get("messages", "")))
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                                       stop_reason="end_turn")

    monkeypatch.setattr(consult_prep, "_get_client", lambda: _Spy)
    monkeypatch.setattr(consult_prep, "REPORTS_DIR", tmp_path)
    _seed()
    consult_prep.prepare_visit_report("2026-12-01", "gp")
    assert sent, "запрос к модели не ушёл — тест ничего не проверил"
    _assert_clean("\n".join(sent), "consult_prep.prepare_visit_report")


# Человеческие поверхности: имя показывается человеку, в модель не идёт.
_HUMAN_SURFACES = {
    "dashboard_views.py",          # подпись поля профиля в дашборде
    "food_quarterly.py",           # документ-профиль питания человеку (рендер кодом)
    "handlers/meta.py",            # первый контакт: «как к вам обращаться»
    "scripts/first_contact_smoke.py",
}
_NAME_KEY = r'(\.get\(\s*["\']name["\']|\[\s*["\']name["\']\s*\])'
# Три формы чтения: рядом со словом identity; через переменную ident*/_cp_ident (hai_core:284
# так и проскочил первую перепись — имя ехало в системный промпт); плоский ключ identity.name.
_READ = re.compile(r'identity.{0,80}?' + _NAME_KEY + r'|(?<![A-Za-z])ident\w*\s*' + _NAME_KEY
                   + r'|identity\.name', re.S)


def test_new_identity_name_reader_is_red():
    # Обход диска, а не git ls-files: прогон на Studio идёт в песочнице без .git.
    skip = ("tests/", "plans/", "migrations/", ".venv/", "venv/", "build/")
    readers = set()
    for path in ROOT.rglob("*.py"):
        f = path.relative_to(ROOT).as_posix()
        if f.startswith(skip) or "/." in f or "__pycache__" in f:
            continue
        if _READ.search(path.read_text(encoding="utf-8", errors="ignore")):
            readers.add(f)
    new = sorted(readers - _HUMAN_SURFACES)
    assert not new, (f"новый читатель identity.name: {new}. Если имя идёт человеку — добавь файл "
                     f"в _HUMAN_SURFACES с причиной; если в модель — убери имя из контекста")

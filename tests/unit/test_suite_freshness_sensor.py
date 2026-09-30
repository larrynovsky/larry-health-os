"""Датчик свежести прогона набора (`integrity_tests.check_suite_freshness`).

§11, догфудинг: правило «у носителя доказательств обязан быть пульс» вводится этим же
диффом — значит и сам новый датчик обязан краснеть при своей поломке, а не быть
украшением. Слепое пятно, которое §11 велит проверять: освободить текущее изменение
(«этот датчик особенный, он же просто WARN»). Не освобождаю.

Четыре состояния, и различие между ними — весь смысл датчика: свежо · протухло ·
отметки нет вовсе · отметка не читается. Последние два стоят отдельно намеренно: «датчик
мёртв» нельзя путать с «нарушений нет».
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def _marker(tmp_path, payload):
    p = tmp_path / "suite_last_run.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                 encoding="utf-8")
    return p


def _sensor(monkeypatch, it, marker_path):
    """Прогнать датчик, вернуть (вердикт, сколько WARN прибавилось).

    ГАСИМ ПРИЗНАК КЛОНА, и это находка, а не удобство теста. Датчик (как и соседний
    `check_probe_liveness`, у которого признак взят) выходит рано, если каталог его файла
    похож на дев-клон. А набор тестов исполняется ровно в таком каталоге —
    `~/health_staging`, единственное место, где прогон вообще возможен (§12, Р-7). То есть
    признак клона делает этот класс датчиков НЕПРОВЕРЯЕМЫМ там, где только и есть проверка:
    без гашения все ассерты ниже проходят по early-return, и тест зеленеет, ничего не
    проверив. Ровно тот ложно-зелёный, против которого написан весь этот диф.

    Сам признак проверяется отдельно — `test_clone_is_skipped_and_says_so`.
    """
    monkeypatch.setattr(it, "_DEV_CLONE_MARKERS", ("__заведомо_не_встречается__",))
    monkeypatch.setattr(it, "SUITE_MARKER_PATH", marker_path)
    before = it.WARN
    res = it.check_suite_freshness()
    return res, it.WARN - before


def test_fresh_run_is_quiet(db, tmp_path, monkeypatch):
    import integrity_tests as it
    ran = (it.get_today()).isoformat()
    res, warns = _sensor(monkeypatch, it, _marker(
        tmp_path, {"ran_at": ran, "commit": "abc1234", "full": True}))
    assert warns == 0, "свежий прогон не должен шуметь"
    assert "abc1234" in res, f"вердикт не называет версию, на которой прогон был: {res!r}"


def test_threshold_is_ten_days(db):
    """Порог — РЕШЕНИЕ (сколько дней владелец согласен не знать), а не деталь реализации.

    Числа ниже — литералы, и это исправление собственной ошибки, найденной догфудингом
    §11 в тот же день. Первая редакция брала дату как `today - (SUITE_STALE_DAYS + 1)`,
    то есть читала ту самую константу, которую проверяла: при любом её значении тест
    оставался зелёным. Мутация «порог = 100000» прошла незамеченной — тест был
    тавтологией. Тот же класс, что «проверяю прокси вместо предмета».

    Теперь сдвинуть порог молча нельзя: смена числа роняет этот тест и требует решения
    человека, а не правки одной строки.
    """
    import integrity_tests as it
    assert it.SUITE_STALE_DAYS == 10, \
        "порог свежести изменён. Это решение владельца (Р-4: 30 дней отвергнуты — " \
        "пропустишь дважды, привыкнешь к жёлтому), а не рефакторинг. Обнови тест осознанно"


def test_eleven_days_warns(db, tmp_path, monkeypatch):
    """11 дней (литерал) обязаны заговорить: молчание не читается как «всё хорошо»."""
    import integrity_tests as it
    import datetime as _dt
    ran = (it.get_today() - _dt.timedelta(days=11)).isoformat()
    _res, warns = _sensor(monkeypatch, it, _marker(
        tmp_path, {"ran_at": ran, "commit": "abc1234", "full": True}))
    assert warns == 1, "прогон 11-дневной давности не дал WARN"


def test_ten_days_is_still_quiet(db, tmp_path, monkeypatch):
    """Ровно на пороге (литерал 10) — молчим. Граница проверяется с двух сторон."""
    import integrity_tests as it
    import datetime as _dt
    ran = (it.get_today() - _dt.timedelta(days=10)).isoformat()
    _res, warns = _sensor(monkeypatch, it, _marker(
        tmp_path, {"ran_at": ran, "commit": "abc1234", "full": True}))
    assert warns == 0, "на самом пороге датчик обязан молчать, иначе порог не тот"


def test_missing_marker_is_not_silence(db, tmp_path, monkeypatch):
    """Отметки нет вовсе → WARN. Пустота не равна благополучию (§14)."""
    import integrity_tests as it
    _res, warns = _sensor(monkeypatch, it, tmp_path / "нет_такого_файла.json")
    assert warns == 1, "отсутствие отметки прошло молча — датчик мёртв и об этом не сказал"


def test_unreadable_marker_is_not_silence(db, tmp_path, monkeypatch):
    """Битая отметка → WARN, а не исключение и не тишина.

    Отдельный случай от предыдущего: сломанный писатель отметки выглядел бы как «файл
    есть, значит прогон был». Дата, не разбираемая как дата, — урок DG-05.
    """
    import integrity_tests as it
    _res, warns = _sensor(monkeypatch, it, _marker(tmp_path, {"ran_at": "позавчера"}))
    assert warns == 1, "нечитаемая дата прошла молча"
    _res2, warns2 = _sensor(monkeypatch, it, _marker(tmp_path, "{это не json"))
    assert warns2 == 1, "битый json прошёл молча"


def test_clone_is_skipped_and_says_so(db, tmp_path, monkeypatch):
    """На дев-клоне датчик молчит ОСОЗНАННО и объясняет причину, а не притворяется зелёным.

    `logs/` в снимок не синхронизируется (rsync его исключает), поэтому отметки там нет по
    построению, и крик был бы ложным. Но выход обязан быть ОТЛИЧИМ от «всё свежо»: иначе
    «пропущено» и «подтверждено» выглядят одинаково — та же подмена, что чинит вся нить.
    """
    import integrity_tests as it
    monkeypatch.setattr(it, "_DEV_CLONE_MARKERS", ("staging",))
    # Каталог — подставной, а не «где сейчас исполняемся»: тест обязан быть зелёным и из
    # ~/health_scripts (scheduled 07:50), и из ~/health_staging (test_on_studio.sh). До
    # 2026-08-30 он молча полагался на второе и краснел в первом ежедневно с 2026-07-30.
    monkeypatch.setattr(it, "SUITE_ROOT_NAME", "health_staging")
    monkeypatch.setattr(it, "SUITE_MARKER_PATH", tmp_path / "нет.json")
    before = it.WARN
    res = it.check_suite_freshness()
    assert it.WARN == before, "на клоне датчик не должен шуметь"
    assert "клон" in res, f"вердикт на клоне не отличим от вердикта о свежести: {res!r}"

"""Сеть РОЖДЕНИЯ производителя корреляций (UC-B-09, 2026-09-08).

Пара к сети на утечку (`check_correlations_grounded`), и различие в ОРАКУЛЕ.
Утечка судится неавторитетно: цитату из литературы от числа, протащенного мимо
гейта, по тексту отчёта не отличить — там WARN и не может быть иначе. Рождение
судится авторитетно: множество модулей, вычисляющих корреляцию, вычислимо, и
«объявлен или нет» — двоичный факт.

Класс, который закрывается, реализовался один раз и найден ПОЗДНО:
`hai_analysis.detect_correlation_drift` печатал r во вход консилиума, и это
обнаружилось лишь когда число доехало до владельца (31.08, вердикт 03.09).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _run(tmp_path, monkeypatch, files: dict, declared: dict):
    import integrity_tests as I
    cap: list[tuple[str, str]] = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    for name, src in files.items():
        (tmp_path / name).write_text(src, encoding="utf-8")
    return I.check_correlation_producers_declared(root=tmp_path, declared=declared), cap


_CALLS = "from scipy import stats\ndef f(a, b):\n    return stats.spearmanr(a, b)\n"


def test_undeclared_producer_is_loud(tmp_path, monkeypatch):
    """⭐ Ровно случай 31.08: модуль считает корреляцию, в объявлении его нет.
    Сеть на рождение обязана назвать его в день появления, а не в день утечки."""
    r, cap = _run(tmp_path, monkeypatch, {"newbie.py": _CALLS}, declared={})
    assert r["undeclared"] == ["newbie"]
    assert any("без объявления" in n for n, _ in cap), cap
    assert "spearmanr" in cap[0][1], "не назван ВЫЗОВ — читателю нечего искать в файле"


def test_declared_producer_is_silent(tmp_path, monkeypatch):
    """Объявленный не звонит: иначе датчик кричал бы про три штатных производителя
    каждую ночь и научил бы себя пролистывать (§13)."""
    r, cap = _run(tmp_path, monkeypatch, {"newbie.py": _CALLS},
                  declared={"newbie": "причина"})
    assert (r["undeclared"], r["stale"], cap) == ([], [], [])


def test_stale_declaration_is_loud(tmp_path, monkeypatch):
    """Симметрия ратчета: объявлен, а считать перестал. Без этой стороны покрытие
    «улучшается» удалением кода, а список расходится с деревом молча — тот же класс,
    что census-асимметрия producer_registry 07.08."""
    r, cap = _run(tmp_path, monkeypatch, {"quiet.py": "x = 1\n"},
                  declared={"ghost": "когда-то считал"})
    assert r["stale"] == ["ghost"]
    assert any("протух" in n for n, _ in cap), cap


def test_prose_is_not_a_producer(tmp_path, monkeypatch):
    """⭐ НЕГАТИВНЫЙ КОНТРОЛЬ на грубый греп. Слово в строке, комментарии и импорте —
    не вычисление. Грубый детектор по подстроке однажды уже объявил трактом девять
    файлов, которые лишь упоминают API (урок LLM-периметра, 2026-08-03)."""
    prose = (
        "# spearmanr здесь только в комментарии\n"
        "DOC = 'мы считаем spearmanr в другом модуле'\n"
        "from scipy.stats import spearmanr  # импорт без вызова\n"
        "def f():\n    return DOC\n"
    )
    r, cap = _run(tmp_path, monkeypatch, {"talker.py": prose}, declared={})
    assert r["found"] == [] and cap == [], f"проза принята за вычисление: {r}, {cap}"


def test_attribute_and_bare_call_both_seen(tmp_path, monkeypatch):
    """`stats.pearsonr(...)` и голый `spearmanr(...)` — один и тот же факт.
    Мутация «смотреть только на Attribute» роняет вторую половину."""
    r, _ = _run(tmp_path, monkeypatch, {
        "a.py": "from scipy import stats\ndef f(x, y): return stats.pearsonr(x, y)\n",
        "b.py": "from scipy.stats import spearmanr\ndef g(x, y): return spearmanr(x, y)\n",
    }, declared={})
    assert r["undeclared"] == ["a", "b"]


def test_unparsable_file_is_loud_not_skipped(tmp_path, monkeypatch):
    """Неразобранный файл выпадает из периметра — то есть производитель внутри
    становится НЕВИДИМЫМ. Тихий `continue` здесь дал бы датчику зелёный ровно тогда,
    когда он ослеп."""
    r, cap = _run(tmp_path, monkeypatch, {"broken.py": "def f(:\n"}, declared={})
    assert any("не разобран" in n for n, _ in cap), cap
    assert r["found"] == []


def test_tests_are_out_of_perimeter(tmp_path, monkeypatch):
    """Тест, считающий корреляцию, производителем не является: он не печатает
    владельцу. Иначе периметр наполнился бы собственной оснасткой."""
    r, cap = _run(tmp_path, monkeypatch, {"test_probe.py": _CALLS}, declared={})
    assert (r["found"], cap) == ([], [])


def test_live_declaration_matches_the_tree():
    """ПОЗИТИВНЫЙ КОНТРОЛЬ на живом дереве: объявление и репозиторий сходятся
    СЕЙЧАС. Замер 2026-09-08 — ровно три производителя: correlation_gate (сам гейт),
    longitudinal_analysis (вера через гейт), hai_analysis (диагностика, вызывающих
    нет с 03.09). Разойдётся — этот тест краснеет раньше ночного датчика."""
    import integrity_tests as I
    r = I.check_correlation_producers_declared()
    assert r["undeclared"] == [] and r["stale"] == [], r
    assert r["found"] == ["correlation_gate", "hai_analysis", "longitudinal_analysis"], r

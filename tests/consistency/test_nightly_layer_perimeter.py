"""Ночной отбор не может выкинуть тест, о котором никто не узнает.

ЧТО УМЕРЛО. До 15.09 ночной прогон звал слой как `pytest tests/<слой>/ -m <слой>`.
Файл без `pytestmark = pytest.mark.<слой>` собирался и тут же отсеивался — молча,
с зелёным итогом. Замер на Studio 14–15.09: unit брал 2363 теста из 4267,
integration 335 из 451, consistency 524 из 559. Отсеянные прогнали отдельно:
1898 + 115 + 37 passed, ноль красных. То есть фильтр выкидывал не больные тесты,
а невидимые, и потому расхождение не могло проявиться ничем.

Нашлось это не датчиком, а вопросом «а где этот тест гоняется по-настоящему» —
на ритуале закрытия нити `night-cycle`, чьи ДВА новых оракула оказались снаружи
ночного прогона. Автор проверял их прямым путём (`pytest tests/unit/test_x.py`),
видел зелёное и записывал «оракул есть». «Тест зелёный» и «тест гоняется» —
разные утверждения, и второе прямым вызовом не доказывается вовсе.

ЧТО СТЕРЕЖЁТ. Ровно одно: у вопроса «какого слоя этот тест» не должно быть двух
домов одновременно. Либо периметр задаёт КАТАЛОГ (тогда ночной отбор не фильтрует
по имени слоя — так сейчас), либо МЕТКА (тогда её обязан нести каждый файл слоя).
Красное = дома разошлись снова: фильтр вернули, а метки есть не у всех.

ЧЕГО НЕ ЛОВИТ, вслух. Другие способы тихо сузить прогон: `norecursedirs`,
`addopts`, `-k`, skip внутри теста, каталог, который перестали звать из скрипта.
Этот файл держит один названный класс, а не «прогон полон» вообще. Квитанционный
датчик (сверка junit последней ночи с числом собранного) был бы шире и живёт
отдельным решением — здесь его нет, и это сказано, а не забыто.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "run_full_test_suite.sh"

# Слой → каталог. Отклонение одно, историческое (см. маппинг в скрипте).
LAYERS = {"unit": "unit", "integration": "integration", "consistency": "consistency",
          "e2e_mock": "e2e_mock", "snapshot": "snapshots"}

# Платный слой включает свои тесты сам и по построению фильтрует по метке —
# у него каталог и метка совпадают по составу, расходиться нечему.
PAID = "llm_judge"


def _marker_filter_in_suite() -> str:
    """Выражение `-m ...`, с которым ночной прогон зовёт НЕплатный слой."""
    text = SUITE.read_text(encoding="utf-8")
    m = re.search(r'^\s*mfilter="(?P<expr>[^"]*)"\s*$', text, re.M | re.X)
    # Ищем ветку «иначе» — она идёт после ветки платного слоя.
    exprs = re.findall(r'^\s*mfilter="([^"]*)"', text, re.M)
    if exprs:
        return exprs[-1]
    # Старая форма: фильтр подставлялся прямо в вызов.
    direct = re.search(r'-m\s+"\$layer"', text)
    return "$layer" if direct else (m.group("expr") if m else "")


def _unmarked(layer: str, dirname: str) -> list[str]:
    """Файлы слоя БЕЗ метки своего слоя."""
    d = ROOT / "tests" / dirname
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("test_*.py")):
        if f"mark.{layer}" not in p.read_text(encoding="utf-8", errors="replace"):
            out.append(p.name)
    return out


def _filters_by_layer_name(expr: str) -> bool:
    """Выражение отбора опирается на имя слоя (значит метка — второй дом)."""
    if "$layer" in expr:
        return True
    return any(re.search(rf"\b{re.escape(name)}\b", expr) for name in LAYERS)


def test_layer_perimeter_has_one_home():
    expr = _marker_filter_in_suite()
    if not _filters_by_layer_name(expr):
        return                      # периметр = каталог, меток можно не иметь
    broken = {layer: _unmarked(layer, d) for layer, d in LAYERS.items()}
    broken = {k: v for k, v in broken.items() if v}
    assert not broken, (
        f"Ночной отбор фильтрует по имени слоя (-m «{expr}»), но метку несут не все "
        "файлы — значит часть тестов не гоняется ночью и молчит об этом:\n  "
        + "\n  ".join(f"{k}: {len(v)} файлов без метки, напр. {v[0]}"
                      for k, v in broken.items())
        + "\nЛибо снять фильтр из run_full_test_suite.sh, либо проставить метки всем."
    )


def test_paid_layer_still_filtered():
    """Негативный контроль: правило не съело исключение.

    Платный слой обязан остаться под меткой — его вызов идёт с очищенными addopts,
    и без метки туда затекли бы чужие тесты, каждый из которых стоит денег."""
    text = SUITE.read_text(encoding="utf-8")
    assert f'mfilter="{PAID}"' in text or f'-m "{PAID}"' in text, (
        "Платный слой перестал отбираться по метке — прогон может уйти в деньги."
    )


def test_sentinel_reads_a_real_script():
    """Позитивный контроль: пустой разбор дал бы вечно-зелёное (§20)."""
    assert SUITE.exists(), "скрипт ночного прогона не найден"
    assert _marker_filter_in_suite(), "выражение отбора не разобрано — сторож слеп"
    assert (ROOT / "tests" / "unit").is_dir()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

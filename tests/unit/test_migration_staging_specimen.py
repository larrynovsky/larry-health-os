"""Расчёт бэкфилла материала (migrations/staging_specimen_20260731).

Проверяется ЧИСТАЯ часть — plan_updates. Транзакционная часть контролем не
покрыта осознанно, и это записано в сайдкаре: её оракул — dry-run перед запуском
и снапшот рядом с базой.
"""
from migrations import staging_specimen_20260731 as mig


ROWS = [
    {"id": 1, "source_file": "invented-materials.pdf", "page": 6},   # своя шапка
    {"id": 2, "source_file": "invented-materials.pdf", "page": 7},   # продолжение той же заявки
    {"id": 3, "source_file": "invented-materials.pdf", "page": 11},   # подвал отчёта
    {"id": 4, "source_file": "invented-materials.pdf", "page": 3},   # чужая вёрстка, материала нет
    {"id": 5, "source_file": "invented-materials.pdf", "page": 12},   # страница-график
    {"id": 6, "source_file": "нет-такого.jpeg", "page": 1},
]
FACTS = {"invented-materials.pdf": {
    6: {"specimen": "Кровь с ЭДТА", "specimen_source": "read_header", "role": "data"},
    7: {"specimen": "Кровь с ЭДТА", "specimen_source": "continuation", "role": "data"},
    11: {"specimen": "мазке из зева", "specimen_source": "read_footer", "role": "data"},
    3: {"specimen": None, "specimen_source": "unknown", "role": "data"},
    12: {"specimen": "мазке из зева", "specimen_source": "read_footer",
         "role": "derived_chart"},
}}


def _by_id(updates):
    return {u[3]: u[:3] for u in updates}


def test_read_steps_carry_material():
    upd, _ = mig.plan_updates(ROWS, FACTS)
    got = _by_id(upd)
    assert got[1] == ("Кровь с ЭДТА", "read_header", "data")
    assert got[2] == ("Кровь с ЭДТА", "continuation", "data")
    assert got[3] == ("мазке из зева", "read_footer", "data")


def test_page_without_material_gets_unknown_not_neighbour():
    """Без собственного материала страница не наследует соседнюю заявку."""
    got = _by_id(mig.plan_updates(ROWS, FACTS)[0])
    assert got[4] == (None, "unknown", "data")


def test_material_is_never_written_without_its_step():
    """Сцепка, которую стережёт check_staging_specimen_provenance. Без этого
    контроля миграция могла бы покрасить собственный датчик."""
    facts = {"invented-materials.pdf": {6: {"specimen": "Кровь с ЭДТА",
                            "specimen_source": "inherited",   # снятая ступень
                            "role": "data"}}}
    upd, _ = mig.plan_updates([ROWS[0]], facts)
    assert upd == [(None, "unknown", "data", 1)]


def test_missing_document_does_not_break_the_plan():
    upd, tally = mig.plan_updates(ROWS, FACTS)
    assert _by_id(upd)[6] == (None, "unknown", "data")
    assert len(upd) == len(ROWS)          # ни одна строка не потеряна
    assert tally["unknown"] == 2          # стр.3 и документ без файла


def test_role_is_carried_independently_of_material():
    """Роль страницы решает, импортировать ли; материал — чей это результат.
    Слить их значило бы потерять одно из двух."""
    got = _by_id(mig.plan_updates(ROWS, FACTS)[0])
    assert got[5] == ("мазке из зева", "read_footer", "derived_chart")


def test_tally_matches_updates():
    """Счёт, по которому миграция проверяет себя внутри транзакции, обязан
    сходиться с самими правками — иначе проверка ничего не значит."""
    upd, tally = mig.plan_updates(ROWS, FACTS)
    steps = {k: v for k, v in tally.items() if not k.startswith("роль:")}
    assert sum(steps.values()) == len(upd)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("расчёт бэкфилла: все контроли зелёные")

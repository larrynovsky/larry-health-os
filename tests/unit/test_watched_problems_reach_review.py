"""Датчик: проблемы со статусом наблюдения доезжают до недельного разбора (29.09).

Подпись статуса обещает человеку «учитываю в каждом недельном разборе». Датчик судит собранный
контекст GP — то, что реально уйдёт модели, — и ищет проблему именно в секции списка проблем."""
import pytest

pytestmark = pytest.mark.unit

ROWS = [{"problem_id": "P7", "status": "watchful_waiting"},
        {"problem_id": "P9", "status": "resolved"}]
SECTION = "АКТИВНЫЙ СПИСОК ПРОБЛЕМ (из базы данных):\nP7 [priority 2] Вымышленная — watchful_waiting\n"


def test_green_when_the_problem_is_in_its_section():
    import integrity_tests as I
    ctx = "LOCATION\n\n" + SECTION + "\nLIFESTYLE:\n..."
    assert I.check_watched_problems_reach_review(rows=ROWS, ctx=ctx)["missing"] == 0


def test_red_when_the_section_drops_it():
    import integrity_tests as I
    ctx = "LOCATION\n\nАКТИВНЫЙ СПИСОК ПРОБЛЕМ (из базы данных):\n\nLIFESTYLE:\nP7 [priority 2] elsewhere"
    with pytest.raises(AssertionError, match="не держится"):
        I.check_watched_problems_reach_review(rows=ROWS, ctx=ctx)


def test_resolved_problems_are_not_promised_anything():
    import integrity_tests as I
    assert I.watched_missing_from_review([{"problem_id": "P9", "status": "resolved"}], "") == []


def test_quiet_without_watched_problems():
    import integrity_tests as I
    assert I.check_watched_problems_reach_review(rows=[], ctx="")["checked"] == 0

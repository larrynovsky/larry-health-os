"""Оракул датчиков тишины входа (§14 пульс + §17 вторая половина).

Датчики Studio-only и в тест-среде не исполняются целиком, поэтому судится
ЧИСТОЕ ЯДРО `integrity_tests.blind_spot_ages` — то место, где живут все ветвления.
Каждый тест назван так, чтобы падение читалось как утверждение о системе.

Проверка на прокси (обязательна, урок 31.07): каждый позитивный случай ниже
покраснеет, если из ядра убрать соответствующее условие. Проверено мутациями,
см. блок __main__.
"""
HOUR = 3600
NOW = 1_800_000_000.0
WM = NOW - 10 * 24 * HOUR      # водяной знак: 10 дней назад
GRACE = 1 * HOUR


def _it():
    """Импорт внутри функции — конвенция проекта: `integrity_tests` на импорте
    прогоняет весь монитор, и на уровне модуля это случилось бы при СБОРЕ тестов."""
    import integrity_tests
    return integrity_tests


def _ages(cand, seen=(), decided=()):
    return _it().blind_spot_ages(cand, set(seen), set(decided), WM, GRACE, NOW)


def test_новый_файл_не_доехавший_до_staging_это_находка():
    assert _ages([("панель.pdf", NOW - 5 * HOUR)]) == [5 * HOUR]


def test_файл_попавший_в_staging_находкой_не_является():
    assert _ages([("панель.pdf", NOW - 5 * HOUR)], seen=["панель.pdf"]) == []


def test_отклонённый_вотчером_файл_находкой_не_является():
    # Решение «не лаборатория» принято и записано — это не слепота, а вердикт.
    assert _ages([("выписка.pdf", NOW - 5 * HOUR)], decided=["выписка.pdf"]) == []


def test_файл_старше_водяного_знака_не_забота_вотчера():
    # Архив CR/ лежит годами; вотчер намеренно не едет по нему.
    assert _ages([("архив_2019.pdf", WM - HOUR)]) == []


def test_файл_внутри_отсрочки_ещё_не_находка():
    # Распознавание одного бланка идёт минуты — тревога здесь была бы ложной.
    assert _ages([("свежий.pdf", NOW - 60)]) == []


def test_находки_считаются_все_а_не_первая():
    got = _ages([("a.pdf", NOW - 3 * HOUR), ("b.pdf", NOW - 9 * HOUR)])
    assert sorted(got) == [3 * HOUR, 9 * HOUR]


def test_пороги_датчиков_зеркалят_сид_бд():
    """Резерв в коде — зеркало `system_config`, а не альтернативная норма.

    Расхождение зеркала с сидом означает, что датчик в офлайне судит по одному
    порогу, а в проде по другому, и никто этого не увидит ([[duplicate_value_splitbrain]]).
    """
    import re
    from pathlib import Path
    it = _it()
    src = (Path(it.__file__).parent / "health_db.py").read_text()
    for key, mirror in it._INTAKE_FALLBACK.items():
        m = re.search(rf"VALUES \('{re.escape(key)}', ([\d.]+),", src)
        assert m, f"сид {key} исчез из health_db.init_db — зеркало осталось без оригинала"
        assert float(m.group(1)) == mirror, (
            f"{key}: сид {m.group(1)} != зеркало {mirror}")


if __name__ == "__main__":
    # Негативный контроль: мутация ядра обязана покраснеть. Без него «тест есть»
    # не значит «тест ловит» — ровно тот прокси, на котором я уже попался 31.07.
    it = _it()
    orig = it.blind_spot_ages

    def _no_watermark(cand, seen, decided, watermark, grace_s, now):
        return orig(cand, seen, decided, 0, grace_s, now)

    it.blind_spot_ages = _no_watermark
    try:
        test_файл_старше_водяного_знака_не_забота_вотчера()
        raise SystemExit("МУТАЦИЯ НЕ ПОКРАСНЕЛА: условие водяного знака не стережётся")
    except AssertionError:
        print("✅ мутация «водяной знак снят» краснеет")
    finally:
        it.blind_spot_ages = orig

    def _no_grace(cand, seen, decided, watermark, grace_s, now):
        return orig(cand, seen, decided, watermark, 0, now)

    it.blind_spot_ages = _no_grace
    try:
        test_файл_внутри_отсрочки_ещё_не_находка()
        raise SystemExit("МУТАЦИЯ НЕ ПОКРАСНЕЛА: отсрочка не стережётся")
    except AssertionError:
        print("✅ мутация «отсрочка снята» краснеет")
    finally:
        it.blind_spot_ages = orig

    for fn in (test_новый_файл_не_доехавший_до_staging_это_находка,
               test_файл_попавший_в_staging_находкой_не_является,
               test_отклонённый_вотчером_файл_находкой_не_является,
               test_находки_считаются_все_а_не_первая,
               test_пороги_датчиков_зеркалят_сид_бд):
        fn()
    print("✅ все утверждения зелёные, обе мутации краснеют")

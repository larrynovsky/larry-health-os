"""
tests/unit/test_import_all_exit_status.py — характеризация F-03.

Нить: ремонт `data_ingestion`, аудит `data_ingestion@2026-07-27-b2306c4`, Фаза A (A6).
Красный на baseline `e4617ff` — ожидаемо. Зелёный только после Фазы C.

ЧТО ОХРАНЯЕТ:
  A6.1  `main()` при полном успехе возвращает КОД 0, а не None.
  A6.2  `main()` при per-file ошибке возвращает non-zero.
  A6.3  Процесс отдаёт этот код shell'у (`raise SystemExit(main())`).

ПОЧЕМУ ЭТО НЕ ПРИДИРКА К СТИЛЮ. `watch_and_import.sh:63-77` собирает `$?` двух
импортёров и пишет в лог ✅ или ❌ по их сумме. Агрегация там **корректна** — проверено
чтением, и A7 это фиксирует отдельным зелёным тестом. Дефект целиком в Python:
`main()` не имеет ни одного `return`, а `__main__` зовёт её без `sys.exit`, поэтому
процесс всегда завершается нулём. Лог может содержать «✗ Ошибки: 3» — и рядом «✅ Импорт
завершён». Наблюдаемая часть системы противоречит машиночитаемой.

ГРАНИЦА (чтобы зелёный не переоценили):
  - что watcher действительно ЗАПИСАЛ failure — это A7, здесь shell не участвует;
  - что документ реально распознан — `extract_text` замокан;
  - A6.3 запускает настоящий subprocess, поэтому он единственный доказывает
    цепочку «код → процесс → shell»; A6.1/A6.2 доказывают только контракт функции.

ОРАКУЛЫ объявлены до тела: A6.1 — `rc == 0`; A6.2 — `rc not in (0, None)`;
A6.3 — `returncode != 0` у subprocess.

Изоляция: `tmp_path`, mocks, `--dry-run` не нужен (запись замокана через `db`).
Ни сети, ни production DB, ни уведомлений.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

import import_all as mod


@pytest.fixture
def health_root(tmp_path, monkeypatch):
    """Подменённый корень HEALTH с одним медицинским PDF — И изолированная БД.

    Файл пустой: путь до `extract_text` он проходит по имени и расширению, а сам
    `extract_text` в тестах замокан — либо на успех, либо на отказ.

    ИЗОЛЯЦИЯ БД — не украшение, а ремонт дефекта, найденного 29.07.2026 по жалобе
    владельца: бот спамил карточками «Новый документ probe.pdf». Механизм: на пути
    успеха `import_all.main()` зовёт `db.save_pending_doc_review()` (import_all.py:681),
    а `tests/conftest.py:49` СОЗНАТЕЛЬНО не уводит `HEALTH_DATA_DIR` в tmp на Studio —
    там integrity/smoke обязаны читать настоящий канон. Мок подменял четыре точки, но
    не БД, и каждый полный прогон дописывал строку в БОЕВУЮ `pending_doc_reviews`,
    откуда её забирал бот. Десять строк за двое суток, 28.07 19:26 → 29.07 07:44 UTC.

    Урок общий, а не про эту функцию: перечислять моки писателей нельзя — список
    устареет на первом же новом вызове. Поэтому здесь закрыт ПУТЬ, а не вызовы:
    `DB_PATH` уведён в tmp, и любой писатель — сегодняшний или завтрашний — попадёт
    в песочницу. Сторож — A6.4 ниже.
    """
    (tmp_path / "probe.pdf").touch()
    import health_db
    (tmp_path / "db").mkdir()
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "db" / "health.db")
    # Второй путь записи — ФАЙЛОВЫЙ: import_all кладёт JSON документа в DATA_DIR/documents,
    # а DATA_DIR — константа модуля, вычисленная при импорте. Без этой строки ночной прогон
    # на Studio каждую ночь клал 2026-01-01_probe.json в НАСТОЯЩИЕ документы владельца
    # (найдено 24.09 приёмкой урока установки: у постороннего тот же файл лёг в его ~/health).
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path / "db" / "data")
    health_db.init_db()
    return tmp_path


def _run_main(health_root, *, extract_side_effect=None, extract_return="текст"):
    """Один прогон `import_all.main()` в изоляции. Возвращает то, что вернула main().

    Замокано ровно четыре точки — корень файлов, классификатор финансовых документов,
    набор уже импортированных и извлечение текста. Всё остальное — настоящий код.

    Герметичность держат ДВА разных механизма, и путать их нельзя: моки убирают
    внешние зависимости (диск, эвристики), а изоляция `DB_PATH` в фикстуре
    `health_root` не даёт настоящим писателям дотянуться до канона. Список моков
    неполон по замыслу — именно поэтому нужен второй слой.
    """
    ex = {"side_effect": extract_side_effect} if extract_side_effect else {"return_value": extract_return}
    with mock.patch.object(mod, "HEALTH", health_root), \
         mock.patch.object(mod, "is_financial", return_value=False), \
         mock.patch.object(mod.db, "get_imported_sources", return_value=set()), \
         mock.patch.object(mod, "extract_text", **ex), \
         mock.patch.object(mod, "parse_date", return_value="2026-01-01"), \
         mock.patch.object(mod, "classify", return_value="general_medical"), \
         mock.patch.object(mod, "save_clinical_to_db"), \
         mock.patch.object(mod.db, "mark_imported"):
        return mod.main()


@pytest.mark.xfail(strict=True, reason=(
    "F-03: `main()` не имеет ни одного return — успех неотличим от любого исхода. "
    "Зелёным станет в Фазе C — тогда XPASS(strict) заставит снять пометку."))
def test_a6_1_success_returns_code_zero(health_root):
    """A6.1 — полный успех отдаёт код 0, а не None.

    `None` тут не «почти ноль»: `__main__` не оборачивает результат в `sys.exit`,
    поэтому отсутствие возврата делает исход неотличимым от успеха ЛЮБОЙ ценой.
    """
    rc = _run_main(health_root)
    assert rc == 0, f"main() вернула {rc!r} вместо кода 0"


@pytest.mark.xfail(strict=True, reason=(
    "F-03: per-file ошибка посчитана и напечатана, но не доехала до кода возврата. "
    "Зелёным станет в Фазе C."))
def test_a6_2_per_file_error_returns_nonzero(health_root):
    """A6.2 — упавший файл делает job неуспешным.

    Отказ смоделирован исключением в `extract_text`: `import_all` ловит его в
    per-file `except` (строки 694-696), печатает «✗ ОШИБКА» и добавляет в `errors`.
    То есть ошибка СОСЧИТАНА — и не доехала до кода возврата. Это и есть F-03.
    """
    rc = _run_main(health_root, extract_side_effect=RuntimeError("planted: extract failed"))
    assert rc not in (0, None), (
        f"job с ошибкой вернул {rc!r} — неотличимо от полного успеха, "
        "хотя ошибка была посчитана и напечатана"
    )


def test_a6_4_harness_writes_land_in_sandbox_not_in_canon(health_root):
    """A6.4 — запись, которую делает прогон, попадает в ПЕСОЧНИЦУ.

    Сторож над дефектом 29.07.2026 (бот спамил владельцу «Новый документ probe.pdf»).
    Оракул устроен так, чтобы краснеть на ПРИЧИНЕ, а не на симптоме: он требует, чтобы
    строка `pending_doc_reviews` появилась в tmp-БД. Если изоляцию `DB_PATH` уберут,
    строка уйдёт мимо песочницы — сюда, в канон или в iCloud-форк, — и тест упадёт.

    Проверять «в каноне НЕ появилось» было бы хуже вдвойне: пришлось бы открывать
    боевую БД из юнит-теста (то самое, что мы запрещаем) и получать зелёный на
    не-Studio просто потому, что канона там нет — §12, зелёный не на той версии.

    ЧЕГО НЕ ДОКАЗЫВАЕТ: что все писатели `import_all` изолированы поимённо. Он
    доказывает, что закрыт ПУТЬ — и этого достаточно ровно потому, что поимённый
    список устарел бы на первом же новом вызове.
    """
    import health_db

    assert str(health_db.DB_PATH).startswith(str(health_root)), (
        f"изоляция БД не действует: DB_PATH={health_db.DB_PATH} вне песочницы "
        f"{health_root} — прогон пишет туда же, куда смотрит бот"
    )
    _run_main(health_root)   # путь успеха: import_all.py:681 зовёт save_pending_doc_review

    with health_db.get_conn() as c:
        rows = [r[0] for r in c.execute(
            "SELECT source_file FROM pending_doc_reviews WHERE source_file='probe.pdf'")]
    assert rows, (
        "путь успеха не дописал строку в tmp-БД. Либо изоляция увела запись мимо "
        "песочницы (тогда она ушла в канон и снова придёт владельцу в бот), либо "
        "`import_all` больше не ставит документ в очередь подтверждения — "
        "и то и другое требует человека, а не правки этого теста"
    )
    # Файловый путь — тот же вопрос: JSON документа обязан лечь в песочницу, а не в
    # каталог данных машины (24.09: у владельца он ложился в боевой ~/health/data/documents).
    docs = list((health_root / "db" / "data" / "documents").glob("*probe*.json"))
    assert docs, (
        "JSON документа не лёг в песочницу — значит, ушёл в каталог данных машины "
        f"(import_all.DATA_DIR={mod.DATA_DIR})"
    )


@pytest.mark.xfail(strict=True, reason=(
    "F-03: `__main__` зовёт `main()` без SystemExit — shell всегда видит 0. "
    "Зелёным станет в Фазе C."))
def test_a6_3_exit_code_reaches_shell(tmp_path):
    """A6.3 — код возврата доходит до shell. Единственный тест здесь с настоящим процессом.

    Почему отдельно от A6.2: контракт функции и контракт процесса — разные вещи.
    `main()` может вернуть 1, а `__main__` — проигнорировать её результат, и watcher
    снова увидит ноль. Это ровно то, что происходит сейчас (`import_all.py:711-712`).
    Проверять надо то, что читает потребитель: `$?`.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(mod.__file__).parent)!r})
        from unittest import mock
        from pathlib import Path
        import import_all as m
        root = Path({str(tmp_path)!r}); (root / "probe.pdf").touch()
        with mock.patch.object(m, "HEALTH", root), \\
             mock.patch.object(m, "is_financial", return_value=False), \\
             mock.patch.object(m.db, "get_imported_sources", return_value=set()), \\
             mock.patch.object(m, "extract_text", side_effect=RuntimeError("planted")):
            raise SystemExit(m.main())
    """)
    # `setdefault` здесь был ДЕФЕКТОМ, а не осторожностью: на Studio в окружении стоит
    # HEALTH_DATA_DIR=~/health, и «поставить по умолчанию» означало «оставить
    # боевой канон». Подпроцессу изоляция задаётся ЖЁСТКО — унаследованное значение тут
    # не бывает правильным ответом.
    env = {**os.environ, "ALLOW_WRITE_NONPRIMARY": "1",
           "HEALTH_DATA_DIR": str(tmp_path / "data")}
    (tmp_path / "data" / "data").mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode != 0, (
        f"процесс завершился кодом {r.returncode} при упавшем файле — "
        f"shell не отличит этот запуск от успешного.\nstdout:\n{r.stdout[-1500:]}"
    )

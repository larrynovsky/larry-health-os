"""tests/consistency/test_data_ingestion_expected_red.py — ратчет над ожидаемо-красными чеками Фазы A.

ЗАЧЕМ. Нить `data-ingestion` пишет характеризационные чеки: они краснеют НАМЕРЕННО и станут
зелёными только после Фазы C. Оставить их просто красными в общем suite нельзя — замерено на
`run_checks.sh:104-150`, красный `pytest tests/` делает три вещи сразу:
  · Telegram называет упавшие node-id через `head -8` — восемь слотов, ровно столько же наших
    чеков; девятый упавший датчик ЧУЖОЙ нити в сообщение уже не попадает;
  · `PYTEST_OK=false` гасит пинг healthchecks.io, и dead-man's switch (единственный канал,
    независимый от Telegram) начинает слать email ежедневно по причине, которую мы создали сами;
  · recovery-пинг «снова зелёный» замолкает до конца Фазы C, то есть на недели.
Красный по замыслу не отличим от красного по поломке, и цену платят все остальные нити. Поэтому
чеки помечены `xfail(strict=True)`: ночь зелёная, а в момент, когда Фаза C чинит дефект, чек
становится XPASS и ПАДАЕТ — маркер нельзя забыть снять.

ЧТО ЭТО ДОКАЗЫВАЕТ. Три вещи, каждая отдельным тестом:
  1. множество помеченных чеков РАВНО объявленному здесь — в обе стороны;
  2. у каждой пометки есть непустая причина;
  3. `xfail(strict=True)` в ЭТОМ окружении действительно роняет проходящий тест.
Третье — §14: пометка принуждает ровно до тех пор, пока среда честит `strict`. Один вектор
проверен и оказался закрытым самой пометкой: `xfail_strict = false` в ini НЕ отменяет явный
`strict=True` (замерено 2026-07-28). Поэтому третий тест стережёт то, что осталось: смену версии
pytest и плагин, переопределяющий маркер. Первые два теста этого не заметят по конструкции —
они судят ИСХОДНИК, а не поведение прогона.

ЕДИНИЦА УЧЁТА — `файл::функция` из AST, а НЕ node-id прогона. Параметризованная функция даёт
ОДИН элемент множества и N падений в прогоне; путать эти два числа не надо (A11: один элемент,
пять падений). Так сделано намеренно: пометка живёт на функции, значит и ратчет судит функции.

ЧЕГО НЕ ДОКАЗЫВАЕТ. Что пометка ОСМЫСЛЕННА. Причина `reason="потом"` пройдёт — ровно та же
граница, что у `legacy` в §15, и по той же причине: осмысленность машинно неотличима. И что
список полон: чек, который никто не написал, здесь не появится.

КРАСНЫЙ ЗДЕСЬ = решение человека, а не правка этого файла:
  · чек больше не помечен → его починили (вычеркни из EXPECTED и из плана нити) либо потеряли;
  · помечен, но не объявлен → кто-то заглушил падающий тест, минуя нить.

ЦЕНА. ~2 с (один вложенный прогон pytest в третьем тесте). Маркер `slow` НЕ ставится сознательно:
`pytest.ini` обещает, что slow «идут в nightly», а ночной `run_checks.sh` гоняет `pytest tests/`
с дефолтными addopts, то есть БЕЗ slow — помеченное туда не доезжает ни к кому.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]

# Файлы характеризации нити `data-ingestion`. Расширяется вместе с Фазой A.
THREAD_FILES = (
    # Область 3 — «отказ не становится статусом» (F-02/F-03/F-19)
    "tests/unit/test_import_medical_events_cli.py",
    "tests/unit/test_import_all_exit_status.py",
    "tests/unit/test_save_clinical_idempotence.py",
    # Область 2 — «идентичность аналита неустойчива» (F-09/F-13/F-14)
    "tests/unit/test_lab_name_homes.py",
    "tests/unit/test_recognizer_reconcile_scope.py",
    "tests/unit/test_lab_promote_glossary.py",
    # F-20 — «ниже предела обнаружения» теряет оператор (найдено 2026-07-29 на источнике)
    "tests/unit/test_censored_values.py",
)

# Чеки, красные ОСОЗНАННО до Фазы C. Менять только вместе с планом нити и handoff.
# Замерено на Studio @8213726: без пометок ровно 8 failed, 1 passed (A9.1 зелёный — он
# доказывает, что идемпотентность ЕСТЬ, и потому в этот список не входит).
EXPECTED = {
    # A5.1 ВЫЧЕРКНУТ 2026-07-29 — починен по-настоящему, не переоракулен: расширения
    # вынесены в константу DOC_EXTS, выражения `tuple | set` больше нет, и шаг доходит
    # до обработчика. Пометка xfail(strict) на самом тесте снята тем же коммитом —
    # XPASS(strict) сработал ровно так, как обещала записка в её reason.
    "tests/unit/test_import_medical_events_cli.py::test_a5_2_main_returns_zero_code_on_success",
    "tests/unit/test_import_medical_events_cli.py::test_a5_3_handler_failure_maps_to_nonzero",
    "tests/unit/test_import_all_exit_status.py::test_a6_1_success_returns_code_zero",
    "tests/unit/test_import_all_exit_status.py::test_a6_2_per_file_error_returns_nonzero",
    "tests/unit/test_import_all_exit_status.py::test_a6_3_exit_code_reaches_shell",
    "tests/unit/test_save_clinical_idempotence.py::test_a9_2_different_documents_same_day_both_saved",
    "tests/unit/test_save_clinical_idempotence.py::test_a9_3_save_failure_is_visible_to_caller",
    # Область 2, добавлено 2026-07-28. Замер: 11 failed до пометок (A11 параметризован
    # пятью живыми парами глоссария, поэтому node-id функций семь, а падений одиннадцать).
    "tests/unit/test_lab_name_homes.py::test_a10_recognizer_vocab_includes_human_confirmed_names",
    # A17.1/A17.2 вычеркнуты 29.07.2026. НЕ «починены»: владелец решил «сначала мерить,
    # маршрут не трогать», и оракул сменился с «расхождение ломает value_agreement» на
    # «расхождение попадает в данные» (unit_agreement/ref_agreement/field_evidence).
    # Вторую половину решения — что маршрут остался прежним — держит зелёный замок
    # test_a17_4_field_disagreement_does_not_change_route.
    "tests/unit/test_lab_promote_glossary.py::test_a18_1_canon_follows_the_confirmed_glossary",
    "tests/unit/test_lab_promote_glossary.py::test_a18_2_glossary_is_requested_for_an_existing_format",
    # F-20: оператор сравнения хранится отдельно в `value_op` в staging и
    # каноне; промоут обязан переносить его вместе со значением.
    # Сквозной контроль: test_censored_values.py::test_a19_1_censored_value_keeps_its_operator.
    # Регрессионное требование: извлечение и промоут не превращают неравенство в равенство.
}


def _is_xfail_call(node: ast.expr) -> ast.Call | None:
    """Декоратор `@pytest.mark.xfail(...)` → его Call-узел. Иначе None.

    Судится ФОРМА обращения (`....mark.xfail(`), а не имя импорта: `from pytest import mark`
    тоже даёт `mark.xfail`. Голый `@pytest.mark.xfail` без скобок сюда не попадает намеренно —
    он не может нести ни `strict`, ни `reason`, а значит не является пометкой в нашем смысле.
    """
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    if not (isinstance(fn, ast.Attribute) and fn.attr == "xfail"):
        return None
    if not (isinstance(fn.value, ast.Attribute) and fn.value.attr == "mark"):
        return None
    return node


def _collect_marks(rel: str) -> dict[str, str | None]:
    """node-id → reason для функций, помеченных `xfail(strict=True)` в одном файле.

    Читается ИСХОДНИК, а не собранные объекты: пометка обязана быть видима глазами в диффе,
    а не появляться из фикстуры или хука в рантайме.
    """
    path = ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, str | None] = {}
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("test"):
            continue
        for deco in fn.decorator_list:
            call = _is_xfail_call(deco)
            if call is None:
                continue
            kw = {k.arg: k.value for k in call.keywords}
            strict = kw.get("strict")
            if not (isinstance(strict, ast.Constant) and strict.value is True):
                continue  # нестрогий xfail — это не ратчет, а глушилка; в множество не берём
            reason = kw.get("reason")
            out[f"{rel}::{fn.name}"] = (
                reason.value if isinstance(reason, ast.Constant) else None
            )
    return out


def _actual() -> dict[str, str | None]:
    got: dict[str, str | None] = {}
    for rel in THREAD_FILES:
        got.update(_collect_marks(rel))
    return got


def test_expected_red_set_matches_marks():
    """Множество помеченных чеков равно объявленному — в обе стороны."""
    got = set(_actual())

    unmarked = sorted(EXPECTED - got)     # объявлен красным, но пометки нет
    unexpected = sorted(got - EXPECTED)   # помечен, но не объявлен

    assert not unmarked and not unexpected, (
        "список ожидаемо-красных чеков разошёлся с пометками в коде.\n"
        + (f"  ОБЪЯВЛЕН, НО НЕ ПОМЕЧЕН (починили → вычеркни из EXPECTED и из плана нити; "
           f"либо пометку потеряли при правке): {unmarked}\n" if unmarked else "")
        + (f"  ПОМЕЧЕН, НО НЕ ОБЪЯВЛЕН (падающий тест заглушили мимо нити — назови вслух "
           f"или сними пометку): {unexpected}\n" if unexpected else "")
        + "  Оба случая требуют решения человека, а не правки только этого теста."
    )


def test_every_mark_carries_a_reason():
    """У каждой пометки есть непустая причина.

    Осмысленность причины НЕ проверяется и проверена быть не может — та же граница, что у
    `legacy` в §15. Проверяется только наличие носителя.
    """
    empty = sorted(nid for nid, reason in _actual().items() if not (reason or "").strip())
    assert not empty, f"пометка без причины — читателю нечего прочитать: {empty}"


def test_strict_xfail_actually_fails_on_pass(tmp_path):
    """§14: сама схема жива — `xfail(strict=True)` роняет ПРОХОДЯЩИЙ тест в этом репозитории.

    Позитивный контроль механизма, на котором держатся два теста выше. Если маркер будет
    переопределён плагином либо смена версии pytest изменит трактовку `strict`, все пометки
    нити молча станут безусловным «пропустить», а первые два теста останутся зелёными:
    они судят ИСХОДНИК, а не поведение прогона.

    ЧЕГО ЭТОТ ТЕСТ НЕ СТЕРЕЖЁТ (замерено, не предположено): `xfail_strict = false` в ini
    явный `strict=True` не отменяет — этот вектор закрыт самой пометкой, а не пробой.

    Проба герметична: файл лежит вне дерева репозитория, поэтому `tests/conftest.py` к нему
    не подключается и окружение проекта не требуется. Конфиг берётся явно (`-c`), иначе
    проверялся бы дефолт pytest, а не наш.
    """
    probe = tmp_path / "test_strict_probe.py"
    probe.write_text(textwrap.dedent('''
        import pytest

        @pytest.mark.xfail(strict=True, reason="проба механизма: тест проходит, значит XPASS")
        def test_this_one_passes():
            assert True
    '''), encoding="utf-8")

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(ROOT / "pytest.ini"),
         str(probe), "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    out = r.stdout + r.stderr

    assert r.returncode != 0, (
        "xfail(strict=True) НЕ уронил проходящий тест — значит пометки нити ничего не "
        f"принуждают, и Фаза C сможет починить дефект, не заметив этого.\n{out[-2000:]}"
    )
    assert "XPASS" in out.upper(), (
        f"прогон упал, но не по XPASS — проба меряет не то, что заявлено:\n{out[-2000:]}"
    )

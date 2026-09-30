"""tests/consistency/test_dispgate_known_bypasses.py — ратчет над списком известных обходов §15.

ЗАЧЕМ. §15 и how-to называют дыры гейта одноразовости, которые оставлены незакрытыми ОСОЗНАННО.
Пока этот список — только текст, он протухает в обе стороны и молча: закрытая дыра остаётся
перечисленной (норма пугает несуществующим), вернувшаяся не перечислена (норма обещает защиту,
которой нет). За одну нить проза про этот гейт трижды обещала больше, чем делает код, — класс
доказан, поэтому список переведён в исполняемое утверждение.

ЧТО ЭТО ДОКАЗЫВАЕТ. Что множество воспроизводимых обходов РАВНО объявленному — в обе стороны.
Красный тест значит одно из двух, и оба требуют решения человека:
  · дыра закрылась → вычеркни её из §15, how-to и из ожидаемого множества здесь;
  · дыра вернулась либо появилась новая → это регрессия, чини или назови её вслух.

ЧЕГО НЕ ДОКАЗЫВАЕТ. Что список полон: обход, который никто не открыл, не появится ни в оснастке,
ни здесь. Ратчет стережёт согласованность заявленного с исполняемым, а не отсутствие неизвестного.

ЦЕНА. ~75 с (каждая проба поднимает одноразовый клон). Маркер `slow` НЕ ставится сознательно:
`pytest.ini` обещает, что slow «идут в nightly», а ночной `run_checks.sh` гоняет `pytest tests/`
с дефолтными addopts, то есть БЕЗ slow — помеченное туда не доезжает ни к кому.

ПОЧЕМУ ОСНАСТКИ ГОНЯЮТСЯ ОДНОВРЕМЕННО, а не по очереди (2026-09-14). До этого дня каждая писала
в свой ЖЁСТКИЙ путь в /tmp, и два одновременных прогона ратчета — а с переездом работы в деревья
нитей (§21) это будни — роняли друг друга: `git clone` в занятый каталог даёт exit 128, оснастка
не печатает ни одной пробы, ратчет краснеет причиной, неотличимой от вернувшейся дыры. Замер
13.09 объяснил это МЕСТОМ прогона (`BL-DISPGATE-RATCHET-ENV-1`); замер 14.09 объяснение
опроверг — тот же код зелёный и в клоне main, и в дереве нити, а красное воспроизводится ровно
двумя параллельными прогонами. Поэтому одновременность здесь не оптимизация времени, а ОРАКУЛ:
вернётся общий путь — этот тест покраснет сам, без второго прогона рядом.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]

# Обходы, оставленные незакрытыми ОСОЗНАННО. Менять только вместе с CLAUDE.md §15 и how-to.
EXPECTED = {
    "plans/verify_dispgate_findings_2026-07-26.py": {
        "DG-05",   # битая дата в вердикте проходит K1 и обнуляет счётчик недель
        "DG-06",   # getattr(mod, "_private") обходит K3
        "DG-12",   # переименование не считается новым файлом
        "DG-13",   # отказ git → fail-open, политика семьи трёх гейтов
    },
    "plans/verify_dispgate_r2_findings_2026-07-27.py": {
        "F-01",    # застейдженный symlink уводит байты за границу индекса
        "F-03a",   # K2 засчитывает вызов в мёртвой ветке
        "F-03b",   # K2 засчитывает переназначенный алиас
        "F-03c",   # K2 засчитывает файл, который pytest не собирает
    },
    # Второй режим строгости (kind=probe, решение владельца Р-6 2026-07-29): у него своя цена,
    # и она входит в тот же пакт. Иначе новый режим приносит новый обход, невидимый ратчету.
    "plans/verify_probe_mode_bypass_2026-07-29.py": {
        "P-01",    # kind=probe заявляется автором, квитанцию можно написать руками
    },
}

_LINE = re.compile(r"^\[([A-Za-z0-9-]+)\]\s+(ВОСПРОИЗВЕЛОСЬ|НЕ воспроизвелось)", re.M)


def _run(rel: str) -> dict[str, bool]:
    """Прогон оснастки против ПРОВЕРЯЕМОГО дерева. Возврат: тег → воспроизвёлся ли обход."""
    # Оснастка клонирует SRC, поэтому SRC обязан быть репозиторием, и клон берёт HEAD.
    # Два следствия, оба чинятся одним снимком рабочего дерева:
    #   1) в staging (`scripts/test_on_studio.sh`) `.git` отсутствует намеренно → `git clone`
    #      даёт exit 128, и ратчет краснеет от СРЕДЫ, а не от расхождения списка (VG-R5-10);
    #   2) на машине с историей клон судил бы ПОСЛЕДНИЙ КОММИТ, то есть pre-commit контроль
    #      выносил бы вердикт о коде, которого у тебя уже нет.
    from tests.conftest import git_bearing_src
    env = {**os.environ, "DISPGATE_VERIFY_SRC": git_bearing_src(ROOT),
           "ALLOW_WRITE_NONPRIMARY": "1"}
    r = subprocess.run([sys.executable, str(ROOT / rel)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    found = {tag: (verdict == "ВОСПРОИЗВЕЛОСЬ") for tag, verdict in _LINE.findall(out)}
    assert found, f"оснастка {rel} не напечатала ни одной пробы — сломалась сама:\n{out[-2000:]}"
    return found


@pytest.fixture(scope="module")
def _all_probe_runs() -> dict[str, dict[str, bool]]:
    """Все оснастки ОДНОВРЕМЕННО — см. шапку модуля: это оракул на общий путь, а не ускорение."""
    from concurrent.futures import ThreadPoolExecutor
    rels = sorted(EXPECTED)
    from tests.conftest import git_bearing_src
    git_bearing_src(ROOT)                       # снимок строим ДО развилки: один на всех
    with ThreadPoolExecutor(max_workers=len(rels)) as pool:
        return dict(zip(rels, pool.map(_run, rels)))


@pytest.mark.owner_data
@pytest.mark.parametrize("rel", sorted(EXPECTED))
@pytest.mark.host_only
def test_reproducible_bypasses_match_declared_list(rel, _all_probe_runs):
    got = _all_probe_runs[rel]
    red = {tag for tag, repro in got.items() if repro}
    expected = EXPECTED[rel]

    closed = sorted(expected - red)      # объявлено дырой, но больше не воспроизводится
    returned = sorted(red - expected)    # воспроизводится, но не объявлено

    assert not closed and not returned, (
        f"{rel}: список известных обходов разошёлся с исполнением.\n"
        + (f"  ЗАКРЫЛОСЬ (вычеркни из CLAUDE.md §15, docs/how-to/disposability_gate.md и из "
           f"EXPECTED здесь): {closed}\n" if closed else "")
        + (f"  ВОСПРОИЗВОДИТСЯ, НО НЕ ОБЪЯВЛЕНО (регрессия либо новый обход — почини или назови "
           f"вслух): {returned}\n" if returned else "")
        + "  Оба случая требуют решения человека, а не правки только этого теста."
    )


def test_expected_set_is_named_in_the_norm():
    """Вторая половина связки: обход, объявленный здесь, обязан быть назван и в норме.
    Иначе ратчет охраняет список, которого читатель нормы не видит."""
    norm = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    howto = (ROOT / "docs/how-to/disposability_gate.md").read_text(encoding="utf-8")
    for rel in EXPECTED:
        assert rel in norm or rel in howto, (
            f"{rel}: оснастка не упомянута ни в §15, ни в how-to — читатель нормы не узнает, "
            f"чем проверяются названные там дыры."
        )
    for tag in EXPECTED["plans/verify_dispgate_findings_2026-07-26.py"]:
        assert tag in norm, f"{tag} объявлен обходом здесь, но не назван в CLAUDE.md §15"

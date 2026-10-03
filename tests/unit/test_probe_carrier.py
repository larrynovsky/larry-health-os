"""НОСИТЕЛЬ ПРОБ: пульс и авто-понижение статуса (Ш1, решение владельца 2026-07-29).

ЗАЧЕМ. До 29.07 три `holds` реестра опирались на пробы, которые не запускал никто.
Обещание выглядело подтверждённым, а подтверждающий механизм молчал бы вечно — §14 в
чистом виде: тихо умерший guard = ложная безопасность.

РЕШЕНИЕ ВЛАДЕЛЬЦА: красная проба роняет статус САМА + строка в триаж. Здесь проверяется
и то, и другое, и — отдельно — что exit=2 («сломана сама проба») статус НЕ роняет:
без этого различия инфраструктурный сбой понижал бы обещание зря, и после второго раза
понижения перестали бы читать.

ОСОЗНАННОЕ ОТСТУПЛЕНИЕ ОТ БУКВЫ РЕШЕНИЯ, проверяемое здесь же: статус падает
ВЫЧИСЛЕНИЕМ (`effective_status`), а не правкой yaml. Авто-правка на Studio создала бы
незакоммиченное на канонической машине (§1) и грязное дерево под деплоем (§12).
Смысл сохранён: реестр перестаёт врать без человека; человек нужен, чтобы вернуть
`holds`, и эта асимметрия намеренная.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import intent_registry as ir  # noqa: E402

PROBE = "plans/probe_labs_scope_enforced_2026-07-28.py"


def _carrier_lists():
    """Что носитель ЗАПУСКАЕТ и что осознанно ПРОПУСКАЕТ — из его исходника.

    Один дом разбора на все проверки охвата: три теста, читающие один и тот же файл
    тремя регулярками, разъехались бы между собой — и это была бы ровно та болезнь,
    против которой они написаны.
    """
    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")

    def _array(name):
        m = re.search(rf"^{name}=\(\n(.*?)^\)", src, re.S | re.M)
        return m.group(1) if m else ""

    run = set(re.findall(r"plans/probe_[\w\-.]+\.py",
                         _array("PROBES") + _array("LIVE_PROBES")))
    skipped = {}
    for line in _array("SKIPPED_PROBES").splitlines():
        if "|" not in line:
            continue
        path, _, cause = line.strip().strip('"').partition("|")
        skipped[path.strip()] = cause
    return run, skipped


def _inv(status="holds", probe=PROBE):
    d = {"id": "zz", "status": status, "claim": "нечто"}
    if probe:
        d["probe"] = probe
    return d


def _verdicts(exit_code=0, probe=PROBE, ran_at=None):
    return {"ran_at": ran_at or dt.date.today().isoformat(), "where": "staging",
            "probes": {probe: {"exit": exit_code, "verdict": "x"}}}


# ── Авто-понижение ────────────────────────────────────────────────────────────────────

def test_green_probe_keeps_holds():
    """Позитивный контроль: без него «всё падает всегда» выглядело бы как работающий
    механизм."""
    assert ir.effective_status(_inv(), _verdicts(0)) == "holds"


def test_red_invariant_drops_the_promise():
    """⭐ Решение владельца: exit=1 роняет статус САМ, без человека."""
    assert ir.effective_status(_inv(), _verdicts(1)) == "open"


def test_no_material_does_not_drop_anything():
    """exit=3 «СУДИТЬ НЕ НА ЧЕМ» (решение владельца 2026-08-12, вариант A): состояние
    ДАННЫХ — инвариант не ломался, ронять статус нельзя. Неотличимость этого состояния
    от 1 («сломан инвариант») или 2 («чини оснастку») тренировала бы игнорировать
    вердикт; возраст датированного подтверждения предъявляет check_probe_liveness."""
    assert ir.effective_status(_inv(), _verdicts(exit_code=3)) == "holds"


def test_broken_harness_does_not_drop_anything():
    """⭐ exit=2 — сломана ПРОБА, не инвариант. Понижать нечего: об обещании это не
    говорит ничего. Инфраструктурный сбой, роняющий обещания, обесценивает понижения."""
    assert ir.effective_status(_inv(), _verdicts(2)) == "holds"


def test_no_verdicts_at_all_does_not_silently_drop():
    """Отсутствие прогона НЕ роняет статус: это другой сигнал, и у него свой датчик
    (`check_probe_liveness`). Иначе первый же запуск на чистой машине понизил бы всё."""
    assert ir.effective_status(_inv(), {}) == "holds"


def test_invariant_without_probe_is_untouched():
    """80 из 95 обещаний стоят не на пробах. Механизм обязан их не трогать."""
    assert ir.effective_status(_inv(probe=None), _verdicts(1)) == "holds"


def test_non_holds_is_returned_as_is():
    assert ir.effective_status(_inv(status="open"), _verdicts(0)) == "open"


def test_verdict_for_another_probe_is_not_borrowed():
    """Красная СОСЕДНЯЯ проба не роняет чужое обещание — иначе одна поломка гасила бы
    весь реестр, и понижение перестало бы что-либо значить."""
    other = _verdicts(1, probe="plans/probe_quarantine_exit_2026-07-29.py")
    assert ir.effective_status(_inv(), other) == "holds"


# ── Чтение артефакта ──────────────────────────────────────────────────────────────────

def test_broken_artifact_is_not_read_as_empty(tmp_path):
    """⭐ «Датчик мёртв» нельзя путать с «нарушений нет». Битый json обязан отличаться
    от отсутствующего — иначе порча файла читается как тишина."""
    p = tmp_path / "probe_verdicts.json"
    p.write_text("{это не json", encoding="utf-8")
    assert "broken" in ir.probe_verdicts(p)


def test_missing_artifact_is_empty_not_broken(tmp_path):
    assert ir.probe_verdicts(tmp_path / "нет.json") == {}


def test_artifact_roundtrip(tmp_path):
    """Форма, которую пишет носитель, обязана читаться читателем. Разъедутся — статус
    перестанет падать, и никто не заметит."""
    p = tmp_path / "probe_verdicts.json"
    p.write_text(json.dumps(_verdicts(1), ensure_ascii=False), encoding="utf-8")
    data = ir.probe_verdicts(p)
    assert data["probes"][PROBE]["exit"] == 1
    assert data["where"] == "staging"


# ── Носитель как файл ─────────────────────────────────────────────────────────────────

def test_carrier_script_exists_and_names_its_probes():
    """Носитель, не называющий, что он запускает, — обещание без содержания."""
    sh = ROOT / "scripts" / "run_probes.sh"
    assert sh.exists(), "носителя нет — обещания снова без пульса"
    src = sh.read_text(encoding="utf-8")
    for probe in ("probe_failure_never_publishes", "probe_labs_scope_enforced",
                  "probe_quarantine_exit"):
        assert probe in src, f"носитель не запускает {probe}"
    assert "health_staging" in src, "носитель обязан исполняться на снимке (Р-7)"
    assert "Studio" in src, "носитель обязан отказываться работать не на Studio"


def test_every_probe_backed_promise_is_covered_by_the_carrier():
    """⭐ ОХВАТ НОСИТЕЛЯ. Обещание, стоящее на пробе, которую носитель не запускает, —
    это ровно исходная дыра, вернувшаяся под другим именем."""
    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    uncovered = []
    for entry in ir.load_registry():
        for inv in entry.get("invariants") or []:
            if inv.get("status") != "holds":
                continue
            for probe in ir.probes_of(inv):
                if probe not in src:
                    uncovered.append((inv["id"], probe))
    assert not uncovered, (
        f"обещания стоят на пробах, которых нет в носителе: {uncovered}. "
        f"Либо добавить в scripts/run_probes.sh, либо понизить статус")


def test_declared_probe_runs_even_for_an_open_invariant():
    """⭐ ОБЪЯВЛЕННАЯ проба обязана исполняться независимо от статуса обещания.

    До 13.09 охват считался только по `holds`, и это создавало асимметрию: механизм
    стерёг подтверждённые обещания и молчал о неподтверждённых. Следствие поймано на
    живом примере — `question_typing_unverified_open` объявил пробу
    `probe_question_addressing_2026-09-12.py`, сторож охвата законно её не увидел,
    носитель не запускал, и единственный механизм, способный закрыть этот `open`,
    не работал НИ РАЗУ. Открытое обещание так может остаться открытым навсегда, и
    никто не заметит: отсутствие красного здесь неотличимо от отсутствия проверки.

    Граница: тест требует ИСПОЛНЕНИЯ объявленного, а не наличия объявления. Инвариант
    без поля `probe` он не трогает — «пробы нет» законное состояние, «проба есть и не
    запускается» нет."""
    run, skipped = _carrier_lists()
    uncovered = []
    for entry in ir.load_registry():
        for inv in entry.get("invariants") or []:
            if inv.get("status") == "holds":
                continue                      # покрыто предыдущим тестом
            for probe in ir.probes_of(inv):
                if probe not in run and probe not in skipped:
                    uncovered.append((inv["id"], probe))
    assert not uncovered, (
        f"пробы объявлены, но носитель их не запускает и не объявляет пропуск: "
        f"{uncovered}. Либо в PROBES/LIVE_PROBES, либо в SKIPPED_PROBES с причиной, "
        f"либо убрать поле probe — объявление без исполнения выглядит как проверка "
        f"и ею не является")


def test_every_skip_carries_a_cause():
    """Пропуск без причины = тот же молчаливый пропуск, только записанный.

    Отрицательный вердикт легален, отсутствие вердикта — нет: та же норма, что у
    §15 (`not_disposable` обязан нести cause). «Причина не установлена» — законная
    причина: она честно говорит, что это долг, а не решение, и её видно."""
    _, skipped = _carrier_lists()
    assert skipped, "список пропусков пуст — тест судит пустое множество"
    empty = [p for p, cause in skipped.items() if len(cause.strip()) < 20]
    assert not empty, f"пропуски без внятной причины: {empty}"


def test_the_skip_list_does_not_grow_quietly():
    """Ратчет на пропуски. Без него объявленную пробу можно было бы «покрыть»,
    дописав строчку — то есть механизм, введённый против молчания, стал бы его
    удобным оформлением. База 13.09 — три, из них одна с неустановленной причиной.

    БАЗА ПОДНЯТА ДО 4 ОСОЗНАННО (2026-09-15, нить status-home-and-registry), и это
    ровно тот случай, ради которого ратчет допускает подъём строкой, а не молчанием.
    Четвёртая — `plans/probe_question_corpus_2026-09-15.py`. Её корпус не набор, а
    ПОЛНАЯ популяция формулировок за скользящее окно; в ежедневной полосе «покраснело»
    было бы неотличимо от «сменился корпус», то есть датчик с самого начала был бы
    датчиком не того. Альтернатива (ежедневный прогон) отвергнута с ценой названной:
    ⌈N/порция⌉ вызовов модели в сутки за число, смысл которого меняется вместе с
    входом. Перевод в живую полосу — решение владельца.

    БАЗА ПОДНЯТА ДО 5 ОСОЗНАННО (2026-09-16, нить agent-coordination), и пятая
    отличается от четвёртой причиной, а не усталостью: у
    `plans/probe_thread_merge_conflicts_2026-09-16.py` предмет — ИСТОРИЯ git, а
    носитель гоняет пробы в `~/health_staging`, куда дерево приезжает rsync'ом с
    `--exclude '.git/'`. То есть в ежедневной полосе она вернула бы 2 («сломана
    оснастка») каждую ночь — не потому, что сломана, а потому, что её предмета там
    физически нет. Это ограничение НОСИТЕЛЯ, а не пробы, и оно проверяемо одной
    строкой в `run_probes.sh`, а не суждением.

    БАЗА ОПУЩЕНА ДО 4 (2026-09-16, нить `probe-dop3-carrier`): пропуск
    `probe_dop3_numbers_2026-08-01.py` снят — проба не была сломана, её просто не внесли
    в носитель 01.08. Прогнана вручную на снимке канона: обычный режим rc=0, self-test
    rc=0, негативный контроль исполнен. Ратчет опускается ВМЕСТЕ с устранением причины,
    иначе освободившееся место молча примет следующий пропуск.

    ГРАНИЦА ПОДЪЁМА: это НЕ индульгенция следующему пропуску. Пятый обязан краснеть
    здесь так же, как краснел пятый до снятия этого."""
    _, skipped = _carrier_lists()
    # База 4 → 5 (02.10, нить task-dedup): probe_task_dedup — разовый замер на личной истории
    # задач вне репозитория (§23), ежедневного предмета у него нет; причина — в SKIPPED_PROBES.
    assert len(skipped) <= 5, (
        f"пропущенных проб {len(skipped)} > базы 5: список растёт. Либо запустить "
        f"пробу, либо осознанно поднять базу этой строкой — но не молча")


def test_live_lane_gets_the_model_key_and_nothing_else():
    """⭐ НЕГАТИВНЫЙ КОНТРОЛЬ НА ГРАНИЦУ СЕКРЕТА (R2, 13.09).

    Полосе живых проб нужен настоящий ключ модели. Свойство, ради которого пробы
    вообще пускают на снимок канона, — «проба физически не может послать настоящее
    сообщение», и оно держится на том, что telegram-секреты фиктивны. Прецедент цены:
    01.08, 19 278 сообщений партнёру от процесса, который «не должен был» их слать.

    Тест судит ИСХОДНИК носителя, и это его честная граница: он ловит правку, которая
    подкладывает в живую полосу второй настоящий секрет, но не докажет, что в момент
    прогона каталог именно таков."""
    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    assert "LIVE_SEC" in src, "живой полосы нет — тест судит пустое множество"
    live = src.split("LIVE_SEC=")[1]
    # Каталог настоящих секретов — $REAL_SECRETS (30.09: в контейнере он не ~/.health_secrets).
    assert 'REAL_SECRETS="${HEALTH_SECRETS_DIR:-$HOME/.health_secrets}"' in src
    copied = re.findall(r'cp "\$REAL_SECRETS/(\w+)"', live)
    assert copied == ["anthropic_key"], (
        f"в живую полосу копируются настоящие секреты помимо ключа модели: {copied}")
    for must_be_fake in ("telegram_token", "telegram_chat_id"):
        assert must_be_fake in live, (
            f"{must_be_fake} не подменён фиктивным в живой полосе — проба сможет "
            f"дотянуться до настоящего канала")


def test_the_population_is_not_empty():
    """Позитивный контроль на охват предыдущего теста: он про расхождение множеств и на
    пустом зелен тривиально. На 2026-07-29 обещаний на пробах — три."""
    backed = [inv for e in ir.load_registry() for inv in (e.get("invariants") or [])
              if inv.get("status") == "holds" and ir.probes_of(inv)]
    assert len(backed) >= 3, (
        f"обещаний на пробах: {len(backed)} — ожидалось не меньше трёх. Меньше значит, "
        f"что проверка охвата носителя судит почти пустое множество")


def test_the_open_population_is_not_empty():
    """Позитивный контроль к тесту про `open`: на 13.09 такой инвариант ровно один
    (`question_typing_unverified_open`). Станет ноль — тест выше зазеленеет на пустом
    множестве и перестанет что-либо значить, и узнать об этом надо здесь."""
    declared = [inv for e in ir.load_registry() for inv in (e.get("invariants") or [])
                if inv.get("status") != "holds" and ir.probes_of(inv)]
    assert declared, ("не-holds инвариантов с объявленной пробой ноль — проверка "
                      "исполнения объявленного судит пустое множество")


def test_no_material_label_depends_on_evidence_age(monkeypatch):
    """Тело check_probe_liveness, ветка exit=3 (2026-08-31): доказательство моложе
    PROBE_STALE_EVIDENCE_DAYS → метка «судить не на чем» (standing, дайджест);
    старше → «просроченном доказательстве» (decide, ежедневно). Негатив — та же
    оснастка с другой датой подтверждения."""
    import datetime as dt
    import integrity_tests as I
    probe = "plans/probe_quarantine_exit_2026-07-29.py"
    reg = [{"id": "vg", "invariants": [
        {"id": "quarantine_persistent", "status": "holds", "verified_at": "2026-07-29",
         "probes": [probe]}]}]
    verdicts = {"ran_at": "2026-08-31T03:20:00Z", "probes": {probe: {"exit": 3}}}
    monkeypatch.setattr(ir, "probe_verdicts", lambda path=None: dict(verdicts))
    monkeypatch.setattr(I, "_DEV_CLONE_MARKERS", ())   # на staging датчик молчит как «клон» — здесь судим ветку
    monkeypatch.setattr(ir, "load_registry", lambda *a, **k: reg)
    monkeypatch.setattr(ir, "probes_of", lambda inv: inv.get("probes") or [])
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))

    monkeypatch.setattr(I, "get_today", lambda: dt.date(2026, 8, 31))   # 33 дня
    I.check_probe_liveness()
    assert any(n.startswith("Пробам судить не на чем") for n in cap), cap
    assert not any("просроченном" in n for n in cap), cap

    cap.clear()
    verdicts["ran_at"] = "2026-11-30T03:20:00Z"                          # носитель свеж, материала нет
    monkeypatch.setattr(I, "get_today", lambda: dt.date(2026, 11, 30))  # 124 дня
    I.check_probe_liveness()
    assert any("просроченном доказательстве" in n for n in cap), cap
    assert not any(n.startswith("Пробам судить не на чем") for n in cap), cap


def test_crash_is_broken_harness_not_invariant(tmp_path):
    """Упавшая проба = 2 («сломана проба»), а не 1 («сломан инвариант»).

    Замер 2026-09-21: проба Дополнения 3 упала на импорте, Python вышел с 1, и носитель
    записал «СЛОМАН ИНВАРИАНТ» про обещание, которого никто не проверял. Здесь гоняется
    САМА функция `run_probe` из носителя (вырезается из скрипта, копии нет).

    Негативный контроль внутри: та же падающая проба голым интерпретатором даёт 1 —
    то есть без `run_probe` коллизия кодов на месте, и тест различает эти два мира.
    """
    import subprocess

    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    m = re.search(r"^run_probe\(\) \{\n.*?^\}\n", src, re.S | re.M)
    assert m, "в носителе нет функции run_probe — пробы снова запускаются голым python"
    assert src.count('run_probe "$probe"') == 2, "обе полосы обязаны идти через run_probe"

    (tmp_path / "plans").mkdir()
    (tmp_path / "sibling_mod.py").write_text("X = 1\n", encoding="utf-8")
    probes = {
        "crash": ("import no_such_module_xyz\n", 2),
        "red": ("import sys\nsys.exit(1)\n", 1),
        "nomat": ("import sys\nsys.exit(3)\n", 3),
        # корень снимка в sys.path даёт носитель, а не дисциплина автора пробы
        "green": ("import sys\nimport sibling_mod\nsys.exit(0 if sys.argv[1:] == ['--a'] else 1)\n", 0),
    }
    for name, (body, want) in probes.items():
        p = tmp_path / "plans" / f"{name}.py"
        p.write_text(body, encoding="utf-8")
        rc = subprocess.run(
            ["bash", "-c", m.group(0) + f'run_probe "plans/{name}.py" --a'],
            cwd=tmp_path, env={"PY": sys.executable, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True).returncode
        assert rc == want, f"{name}: ждали {want}, получили {rc}"

    bare = subprocess.run([sys.executable, "plans/crash.py"], cwd=tmp_path,
                          capture_output=True).returncode
    assert bare == 1, "контроль потерял смысл: голый python больше не даёт 1 на падении"


@pytest.mark.owner_data
@pytest.mark.parametrize("xpc, want", [
    ("<plist>", "<plist>"),   # штатный прогон: launchd кладёт метку своего плиста
    (None, "manual"),         # ssh / cron: переменной нет
    ("0", "manual"),          # Terminal: «0» — не метка задачи
])
def test_verdicts_name_who_ran_the_carrier(tmp_path, xpc, want):
    """Артефакт называет, КТО запустил носитель (23.09, решение владельца «ок»).

    Критерий подъёма обещания — «первый зелёный прогон НОСИТЕЛЯ». 21.09 ручной прогон
    соседа записал вердикт за 29 минут до коммита, который должен был судиться, и отличить
    его от штатного можно было только по истории. Гоняется САМ писатель артефакта из
    носителя (вырезается из скрипта, копии нет); метка — из плиста носителя в репо."""
    import plistlib
    import subprocess

    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", src, re.S)
    writer = [b for b in blocks if "probe_verdicts.json" in b]
    assert len(writer) == 1, "писатель артефакта в носителе не найден или раздвоился"
    label = plistlib.loads((ROOT / "launchd" / "com.larry.health.probes.plist")
                           .read_bytes())["Label"]
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin",
           "RESULTS": "plans/p.py=0\n", "STARTED": "2026-09-28T03:20:00Z"}
    if xpc is not None:
        env["XPC_SERVICE_NAME"] = label if xpc == "<plist>" else xpc
    subprocess.run([sys.executable, "-c", writer[0]], env=env, check=True,
                   capture_output=True, text=True)
    art = json.loads((tmp_path / "health_scripts" / "logs" / "probe_verdicts.json")
                     .read_text(encoding="utf-8"))
    assert art["runner"] == (label if want == "<plist>" else want), art
    assert art["probes"]["plans/p.py"]["exit"] == 0


# ── журнал вердиктов (28.09, решение владельца, BL-VERDICT-RUNNER-1) ────────────────
# Предмет: доказательство закрытия обещания жило в ПЕРЕМЕННОЙ — сводке на один прогон.
# 21.09 и 28.09 ручной прогон затирал вердикт носителя, и по `ran_at` + `exit 0` подъём
# статуса выглядел законным. Теперь источник — журнал, сводка производная. Проверки ниже
# гоняют САМ писатель, вырезанный из носителя: зелёный причинён тестом, а не файлом,
# случайно лежащим на диске (§20).

def _run_writer(home, results="plans/p.py=0\n", started="2026-09-28T03:20:00Z", xpc=None):
    """Исполнить писатель артефакта из носителя в песочнице. Возвращает CompletedProcess."""
    import subprocess

    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", src, re.S)
    writer = [b for b in blocks if "probe_verdicts.json" in b]
    assert len(writer) == 1, "писатель артефакта в носителе не найден или раздвоился"
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin",
           "RESULTS": results, "STARTED": started}
    if xpc is not None:
        env["XPC_SERVICE_NAME"] = xpc
    return subprocess.run([sys.executable, "-c", writer[0]], env=env,
                          capture_output=True, text=True)


def _paths(home):
    logs = home / "health_scripts" / "logs"
    return logs / "probe_verdicts.jsonl", logs / "probe_verdicts.json"


@pytest.mark.owner_data
def test_carrier_verdict_survives_a_later_manual_run(tmp_path):
    """ВОСПРОИЗВЕДЕНИЕ 28.09: ручной прогон больше не стирает вердикт носителя.

    Носитель в 03:20Z дал по пробе exit 1; ручной прогон в 03:48Z — exit 0 и затёр сводку.
    Признак приёмки этой работы: сессия, у которой нет контекста того дня, находит красный
    вердикт НОСИТЕЛЯ в журнале фильтром по `runner`, не читая текстовый лог глазами."""
    jrn, summary = _paths(tmp_path)
    label = "com.larry.health.probes"

    assert _run_writer(tmp_path, "plans/b.py=1\n", "2026-09-28T03:20:05Z",
                       xpc=label).returncode == 0
    assert _run_writer(tmp_path, "plans/b.py=0\n", "2026-09-28T03:48:47Z").returncode == 0

    rows = [json.loads(l) for l in jrn.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2, "журнал потерял прогон"

    # Сводка — про последний прогон, и он ручной и зелёный. Это НЕ дефект: её читатели
    # (effective_status, check_probe_liveness) спрашивают именно про последний прогон.
    assert json.loads(summary.read_text(encoding="utf-8"))["runner"] == "manual"

    # А вопрос критерия закрытия — про носитель, и теперь на него есть ответ в данных.
    carrier = [r for r in rows if r["runner"] == label]
    assert len(carrier) == 1 and carrier[0]["probes"]["plans/b.py"]["exit"] == 1, carrier


@pytest.mark.owner_data
def test_summary_never_moves_without_the_journal(tmp_path):
    """Сводка не сдвигается, если строка не легла в журнал.

    ЧЕСТНАЯ ГРАНИЦА этой проверки: «сводка собирается ИЗ журнала» — свойство текста кода,
    а не наблюдаемое. При исправной работе последняя строка журнала и своя запись совпадают
    всегда, поэтому `json.dumps(last)` и `json.dumps(record)` неразличимы никаким тестом.
    Наблюдаемо и стережётся здесь другое, и его достаточно: журнал — ворота, и сводка не
    двигается, пока они не пройдены. Мутация «запись журнала обёрнута в try/except, чтобы
    не ломать» — а это первое, что захочется сделать при отказе, — краснеет здесь."""
    jrn, summary = _paths(tmp_path)
    assert _run_writer(tmp_path, "plans/p.py=0\n", "2026-09-28T03:20:00Z").returncode == 0
    before = summary.read_text(encoding="utf-8")

    jrn.chmod(0o444)
    try:
        res = _run_writer(tmp_path, "plans/p.py=1\n", "2026-09-28T09:00:00Z")
    finally:
        jrn.chmod(0o644)
    assert res.returncode != 0, "журнал недоступен, а писатель отчитался успехом"
    assert summary.read_text(encoding="utf-8") == before, \
        "сводка обновилась без записи в журнал — значит она пишется рядом, а не собирается"


@pytest.mark.owner_data
def test_mangled_own_line_is_named_and_leaves_the_summary_alone(tmp_path):
    """Склеенная своя строка (гонка двух прогонов) не даёт выдуманной сводки.

    Прежняя сводка честнее: её возраст ежедневно предъявляет check_probe_liveness и
    краснеет по свежести. Тихо взять предпоследнюю запись было бы хуже отсутствия."""
    jrn, summary = _paths(tmp_path)
    assert _run_writer(tmp_path, "plans/p.py=0\n", "2026-09-28T03:20:00Z").returncode == 0
    before = summary.read_text(encoding="utf-8")

    # Хвост без перевода строки — ровно то, что оставляет прерванная запись соседа.
    with jrn.open("a", encoding="utf-8") as fh:
        fh.write('{"ran_at": "2026-09-28T03:4')

    res = _run_writer(tmp_path, "plans/p.py=1\n", "2026-09-28T09:00:00Z")
    assert res.returncode != 0, "своя запись склеена, а писатель отчитался успехом"
    assert "битая строка" in res.stdout, res.stdout
    assert summary.read_text(encoding="utf-8") == before, "сводка ушла на чужую запись"


@pytest.mark.parametrize("writer_ok", [True, False])
def test_успешная_запись_вердиктов_не_объявляется_провалом(tmp_path, writer_ok):
    """Замер 30.09, первый прогон носителя в контейнере (bash 5): писатель отработал, журнал и сводка
    легли, а носитель напечатал «⛔ запись вердиктов упала» и вышел 2 — условие было перевёрнуто
    (`if ! писатель; then :; else ⛔`). Гоняется САМ блок из носителя настоящим bash; писатель
    подменён true/false — предмет здесь ветвление, а не содержимое записи."""
    import shutil
    import subprocess

    src = (ROOT / "scripts" / "run_probes.sh").read_text(encoding="utf-8")
    m = re.search(r"^if RESULTS=.*?^fi\n", src, re.S | re.M)
    assert m, "блок записи вердиктов не найден в носителе"
    fake = shutil.which("true" if writer_ok else "false")
    r = subprocess.run(["bash", "-c", "RESULTS=; STARTED=; " + m.group(0) + "echo ДОШЛИ"],
                       env={"PY": fake, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    if writer_ok:
        assert "упала" not in r.stdout and "ДОШЛИ" in r.stdout, r.stdout
    else:
        assert "упала" in r.stdout and r.returncode == 2, (r.returncode, r.stdout)

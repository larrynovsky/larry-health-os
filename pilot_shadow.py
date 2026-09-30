#!/usr/bin/env python3.11
"""pilot_shadow.py — теневой сторож срочных тревог на время пилота в контейнере (docker-install, этап 10).

Зачем. Репетиция 29.09 показала: контейнер без приватного слоя владельца молча пересевает справочники
шаблоном постороннего (3 правила лекарственного риска потеряны, источник порогов CTCAE исчез), и ни одна
ошибка об этом не говорит. Пропущенная срочная тревога дороже пропущенного брифа.

Как. Раз в час ОДИН снимок базы из тома (backup API) судится ДВАЖДЫ одной и той же проверкой
(safety_net.run_safety_net): кодом и методологией контейнера и нативным кодом хоста с приватным слоем.
Расхождение наборов тревог — сообщение владельцу сразу (notify_operator), повтор того же расхождения —
не чаще раза в сутки (антиспам: класс «19278 сообщений», 01.08). Сбой любого шага — тоже сообщение:
сторож, который не смог сравнить, не имеет права молчать.

Граница честно: ловит «контейнер судит иначе» (пороги, справочники, код), НЕ ловит «контейнер не доставил» —
это датчики доставки. Снимок хоста — отдельный файл, не рабочая база: второго пишущего нет.
Одноразовый: снимается вместе с пилотом (волна В), вердикт — contracts/pilot_shadow.json.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Имя каталога данных — личность данных (secrets_paths.is_owner_data: «health» → владелец).
SHADOW = Path.home() / "health_shadow" / "health"
IN_BOX = "/home/health/shadow/health"          # тот же снимок внутри тома контейнера
REPEAT_S = 24 * 3600
# Вердикт = тревоги на сегодняшних данных + ДЕЙСТВУЮЩИЕ правила тревог. Только тревог мало (замер 29.09):
# на данных дня, не попавших в спорную полосу, разные правила дают одинаковые тревоги, и сторож молчит.
# Правила берутся из таблиц, которые пересевает старт контейнера из своих файлов (время/id — не правило).
VERDICT = (
    "import json, safety_net as sn, health_db as db; r = sn.run_safety_net(); c = db.get_conn(read_only=True); "
    "rules = [['abs'] + list(x) for x in c.execute(\"SELECT metric, direction, value, kind, baseline, band_label, "
    "variant FROM absolute_thresholds WHERE active = 1 AND variant LIKE 'safety_net%'\")]; "
    "rules += [['trend'] + list(x) for x in c.execute('SELECT metric, direction, pct_change, n_readings, level, "
    "near_boundary_share FROM lab_trend_thresholds WHERE active = 1')]; "
    "print(json.dumps({'alerts': sorted([a['metric'], a['level'], a.get('kind')] for a in r['alerts']), "
    "'rules': sorted(rules, key=repr)}, ensure_ascii=False))")
# Натив сначала пересевает копию СВОИМ кодом и файлами (как было бы на Studio): иначе обе стороны
# читали бы одни и те же таблицы, засеянные контейнером, и сравнение ничего бы не доказывало.
NATIVE = "import health_db; health_db.init_db(); " + VERDICT
SNAPSHOT = ("import sqlite3, pathlib; pathlib.Path('{d}/data').mkdir(parents=True, exist_ok=True); "
            "s = sqlite3.connect('file:/home/health/health/data/health.db?mode=ro', uri=True); "
            "t = sqlite3.connect('{d}/data/health.db'); s.backup(t); t.close()").format(d=IN_BOX)


def diff(native: dict, container: dict) -> list[str]:
    """Пустой список — вердикты совпали. Тревоги сравниваются все (и warn), правила — построчно."""
    out = []
    for key, what in (("alerts", "тревога"), ("rules", "правило")):
        n = {json.dumps(x, ensure_ascii=False) for x in native.get(key, [])}
        c = {json.dumps(x, ensure_ascii=False) for x in container.get(key, [])}
        out += [f"{what} только на хосте: {x}" for x in sorted(n - c)]
        out += [f"{what} только в контейнере: {x}" for x in sorted(c - n)]
    return out


def _run(cmd: list, env: dict | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=ROOT,
                       env={**os.environ, **(env or {})})
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: {p.stderr.strip()[-300:]}")
    lines = p.stdout.strip().splitlines()      # снимок и docker cp молчат — это не ошибка
    return lines[-1] if lines else ""


def compare(container: str, docker: list) -> list[str]:
    """Снимок → вердикт контейнера → копия на хост → нативный вердикт → разница."""
    _run(docker + ["exec", container, "python3", "-c", SNAPSHOT])
    env = {"HEALTH_DATA_DIR": str(SHADOW)}
    # Где лежит база каталога данных — знает только health_db (единственный дом раскладки, R1/R2).
    host_db = Path(_run([sys.executable, "-c", "import health_db; print(health_db.DB_PATH)"], env))
    host_db.parent.mkdir(parents=True, exist_ok=True)
    _run(docker + ["cp", f"{container}:{IN_BOX}/data/health.db", str(host_db)])
    boxed = json.loads(_run(docker + ["exec", "-e", f"HEALTH_DATA_DIR={IN_BOX}", container,
                                      "python3", "-c", VERDICT]))
    native = json.loads(_run([sys.executable, "-c", NATIVE], env))
    return diff(native, boxed)


FROZEN = Path.home() / "health" / "data"


def _launchd_labels() -> set[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=30).stdout
    return {ln.split()[-1] for ln in out.splitlines()[1:] if ln.split()}


def _bootout_labels() -> set[str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import install
    return {label for label, v in install.pilot_split().items() if v == "bootout"}


def frozen_findings(frozen: Path = FROZEN, stamp: Path | None = None,
                    loaded: set[str] | None = None, bootout: set[str] | None = None) -> list[str]:
    """Второй писатель замороженной копии владельца (замер 30.09 12:52: после перезагрузки code-watcher
    записал в неё сиды, потому что ремонт прав монитора партнёра вернул файлу 600). Три признака:
    права базы не 400; файл базы или журнала изменился после первого замера; загружена служба
    владельца из списка выгрузки. Первый прогон записывает замер — дальше любое изменение находка."""
    stamp = stamp or SHADOW.parent / "frozen_copy.json"
    db = frozen / "health.db"
    out = []
    mode = db.stat().st_mode & 0o777
    if mode != 0o400:
        out.append(f"права замороженной базы {oct(mode)[2:]}, ждём 400 — запись снова возможна")
    files = {p.name: [p.stat().st_mtime, p.stat().st_size]
             for p in (db, frozen / "health.db-wal") if p.exists()}
    try:
        base = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps(files), encoding="utf-8")
        base = files
    for name in sorted(set(files) | set(base)):
        if files.get(name) != base.get(name):
            out.append(f"{name} изменился после замера: было {base.get(name)}, стало {files.get(name)}")
    on = sorted((_launchd_labels() if loaded is None else loaded)
                & (_bootout_labels() if bootout is None else bootout))
    if on:
        out.append(f"загружены нативные службы владельца: {', '.join(on)}")
    return out


def _should_send(signature: str, state: Path, now: float) -> bool:
    """Новое расхождение — сразу; то же самое — не чаще раза в REPEAT_S."""
    try:
        last = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if last.get("sig") == signature and now - last.get("at", 0) < REPEAT_S:
        return False
    state.write_text(json.dumps({"sig": signature, "at": now}), encoding="utf-8")
    return True


def main() -> int:
    container = os.environ.get("HEALTH_SHADOW_CONTAINER")
    docker = ["docker", "--context", os.environ.get("HEALTH_SHADOW_CONTEXT", "colima-health")]
    try:
        if not container:
            raise RuntimeError("не задан HEALTH_SHADOW_CONTAINER")
        found = compare(container, docker)
        print("\n".join(found) or "совпало")            # подробности — в журнал агента, не человеку
        text = (f"Пилот: контейнер судит о срочных тревогах иначе, чем судила бы Studio на тех же данных "
                f"(расхождений: {len(found)}). Это сбой пилота, а не тревога о здоровье: сверь журнал "
                f"сторожа и при повторе — откат по отрепетированной процедуре.") if found else ""
    except Exception as e:  # noqa: BLE001 — сторож, не сумевший сравнить, обязан сказать это вслух
        text = (f"Пилот: теневой сторож не смог сравнить проверку тревог в контейнере со Studio "
                f"({type(e).__name__}: {str(e)[:200]}). Пока не починено, срочные тревоги пилота не "
                f"подстрахованы.")
        print(text)
    try:
        frozen = frozen_findings()
    except Exception as e:  # noqa: BLE001 — сторож копии, не сумевший посмотреть, говорит вслух
        frozen = [f"сторож не смог осмотреть копию ({type(e).__name__}: {str(e)[:150]})"]
    if frozen:
        print("\n".join(frozen))
        text = (text + "\n\n" if text else "") + (
            "Пилот: у замороженной копии владельца на Studio появился второй писатель или она больше "
            "не защищена от записи (" + "; ".join(frozen)[:400] + "). Это копия для отката, живые данные "
            "в контейнере не затронуты.")
    SHADOW.parent.mkdir(parents=True, exist_ok=True)
    state = SHADOW.parent / "last_report.json"
    if not text:
        state.unlink(missing_ok=True)   # совпало — вернувшееся расхождение снова придёт сразу
    elif _should_send(text, state, time.time()):
        import notify
        notify.notify_operator(text)
    return 1 if text else 0


if __name__ == "__main__":
    sys.exit(main())

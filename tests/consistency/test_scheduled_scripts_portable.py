"""Скрипт по расписанию обязан запускаться и в контейнере, и на Mac (нить host-container-split, 03.10).

Замер 03.10: в контейнере владельца с переезда 30.09 не запускались три задачи из шести, что
расписание зовёт через bash: ночной набор тестов («pytest not installed» — скрипт искал Python по
пути Homebrew), чтение литературы и survivorship (тот же путь и `cd $HOME/health_scripts`,
которого в контейнере нет). Шов уже был — HEALTH_PY в run_checks.sh, run_probes.sh и
backup_studio.sh; три скрипта его не брали. Датчики видели последствия («analyzer не запускался
ни разу», «ночной прогон не состоялся»), но не причину.

Судим исходник: каждый .sh, который называет шаблон плиста службы, едущей в контейнер, не держит голого пути к
интерпретатору Homebrew и не уходит в каталог от $HOME.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_BARE_PY = re.compile(r"/opt/homebrew/bin/python3[.\d]*")
_SEAM = re.compile(r"\$\{(HEALTH_PY|PYTHON):-/opt/homebrew/bin/python3[.\d]*\}")
_HOME_CD = re.compile(r"cd\s+[\"']?\$HOME/health_scripts")


def _scheduled_scripts() -> list[Path]:
    """Скрипты служб, которые placement.yaml отправляет в контейнер (хостовые — не наш предмет)."""
    import yaml
    placement = yaml.safe_load((ROOT / "templates" / "launchd" / "placement.yaml").read_text(
        encoding="utf-8"))["services"]
    names = set()
    for tpl in (ROOT / "templates" / "launchd").glob("*.plist.tmpl"):
        if placement.get(tpl.name.removesuffix(".plist.tmpl")) != "container":
            continue
        names |= set(re.findall(r"([\w/]+\.sh)</string>", tpl.read_text(encoding="utf-8")))
    out = []
    for n in sorted(names):
        rel = n.split("health_scripts/", 1)[-1].lstrip("/")
        for cand in (ROOT / rel, ROOT / Path(rel).name, ROOT / "scripts" / Path(rel).name):
            if cand.is_file():
                out.append(cand)
                break
    return sorted(set(out))


def test_scheduled_scripts_are_found():
    found = {p.name for p in _scheduled_scripts()}
    # Опора: без неё пустой обход зеленел бы на пустом месте.
    assert {"run_full_test_suite.sh", "run_checks.sh", "run_literature_pipeline.sh"} <= found, found


def test_no_bare_homebrew_interpreter():
    bad = []
    for p in _scheduled_scripts():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _BARE_PY.search(_SEAM.sub("", line)):
                bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not bad, "интерпретатор — через ${HEALTH_PY:-…}, как в run_checks.sh:\n" + "\n".join(bad)


def test_no_cd_into_home_tree():
    bad = [f"{p.relative_to(ROOT)}" for p in _scheduled_scripts()
           if _HOME_CD.search(p.read_text(encoding="utf-8"))]
    assert not bad, f"каталог — от самого скрипта, не от $HOME (в контейнере дерево в /app): {bad}"

"""Родитель пишущего тома внутри /app обязан быть в образе и принадлежать health.

Каталога нет в образе (git archive не несёт неотслеживаемое outputs/) — Docker создаёт его сам при
монтировании вложенного тома, и создаёт от root. С 05.10 в override владельца едет том
outputs/weekly_digest → /app/outputs/weekly_digest; /app/outputs стал root:root, и продольный анализ
(пользователь health) падал на wb.save(/app/outputs/longitudinal_analysis.xlsx) до записи в
agent_reports — последний отчёт 04.10, плановый запуск 11.10 не отметился (карточка night-cycle 11.10).
"""
import re
import sys
from pathlib import Path, PurePosixPath

import pytest
import yaml

from scripts import install

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _dockerfile_owned_dirs() -> set[str]:
    """Каталоги, которыми в ИТОГЕ владеет health: создан `mkdir -p` и ПОСЛЕДНИЙ `chown` строки RUN,
    где он назван, отдаёт его health. Порядок важен (ревью починки 14.10): более поздний
    `chown root:root /app/outputs` вернул бы дефект, а проверка «есть в одной строке» оставалась бы
    зелёной. Это по-прежнему разбор текста, не запуск образа; поведенческий сторож — ночной
    датчик свежести продольного анализа, который и поднял карточку."""
    made, owner = set(), {}
    for line in (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("RUN "):
            continue
        for cmd in re.split(r"&&|;", line[4:]):
            w = cmd.split()
            if w[:2] == ["mkdir", "-p"]:
                made |= set(w[2:])
            elif w[:1] == ["chown"] and len(w) >= 3:
                args = [a for a in w[1:] if not a.startswith("-")]
                for d in args[1:]:
                    owner[d] = args[0]
    return {d for d in made if owner.get(d) == "health:health"}


def _rw_targets_under_app(tmp_path) -> set[str]:
    repo = tmp_path / "repo"
    (repo / "private").mkdir(parents=True)
    out = install.render_owner_override("/Users/o", repo, "h", "Europe/Berlin")
    svc = yaml.safe_load(out["compose.override.yaml"])["services"]
    targets = set()
    for s in svc.values():
        for v in s.get("volumes", []):
            parts = v.split(":")
            if len(parts) >= 2 and parts[1].startswith("/app/") and "ro" not in parts[2:]:
                targets.add(parts[1])
    return targets


def test_родитель_пишущего_тома_создан_в_образе_и_принадлежит_health(tmp_path):
    targets = _rw_targets_under_app(tmp_path)
    assert install.DIGEST_DIR in targets, "том дайджеста пропал — тест судит пустоту"
    owned = _dockerfile_owned_dirs()
    missing = set()
    for t in targets:
        for parent in PurePosixPath(t).parents:
            if str(parent) in ("/", "/app"):
                break
            if str(parent) not in owned:   # есть ли он в git archive — не судим: mkdir+chown обязателен
                missing.add(str(parent))
    assert not missing, (f"Docker создаст {sorted(missing)} от root при монтировании тома — "
                         f"пользователь health не сможет писать рядом с томом")


def test_продольный_анализ_пишет_отчёт_в_каталог_которым_владеет_health():
    """Настоящий путь писателя (ревью 14.10: прежний тест подставлял строку сам и не краснел
    от смены DEFAULT_OUT). Каталог отчёта в образе — /app/<путь от корня репо>; он обязан быть
    создан и отдан health в Dockerfile, иначе wb.save падает PermissionError ДО записи веры —
    замер 15.10: лог прогона 11.10 в health-cron-1, /app/outputs был root:root 755."""
    import belief_contract
    import longitudinal_analysis
    # Корень артефактов — тот же дом, что у писателя (artifact_path): у владельца это корень
    # репозитория, то есть /app в образе; в тестовом окружении — каталог данных тенанта.
    base = Path(belief_contract.artifact_path(".")).resolve()
    rel = Path(longitudinal_analysis.DEFAULT_OUT).resolve().relative_to(base)
    parent = str(PurePosixPath("/app") / PurePosixPath(*rel.parts).parent)
    assert parent in _dockerfile_owned_dirs(), (
        f"отчёт продольного анализа пишется в {parent}, а Dockerfile не отдаёт его health")


def test_поздний_chown_root_возвращает_дефект(monkeypatch, tmp_path):
    """Отрицательный контроль разбора порядка: та же строка mkdir+chown health, а следом
    chown root:root — каталог обязан выпасть из владения health."""
    df = tmp_path / "docker" / "Dockerfile"
    df.parent.mkdir()
    df.write_text("RUN mkdir -p /app/outputs && chown health:health /app/outputs\n"
                  "RUN chown root:root /app/outputs && chmod 755 /app/outputs\n")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert "/app/outputs" not in _dockerfile_owned_dirs()

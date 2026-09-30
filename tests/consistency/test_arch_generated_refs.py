"""Собранные блоки ARCH_SNAPSHOT не называют несуществующих файлов (29.09).

Чтение публичной зоны посторонним (Codex X8, plans/STRANGER_READ_2026-09-29.md) нашло в
GEN-блоках удалённые vps_sync.py, scripts/sync_from_studio.sh, static/index.html: генераторы
(gen_blueprint, gen_key_paths, gen_arch_blocks) не запускаются по расписанию, и блок застывает.
Сравнивать блок с генератором целиком — шумно (покрытие тестами меняется с каждым тестом,
сигналы доменов — с пересчётом в БД). Стережётся ровно вредный класс: файл репозитория,
названный в блоке, должен существовать. Красный → пересобрать блоки генераторами на основной
машине (docs/how-to/update_docs.md) и закоммитить."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_BLOCK = re.compile(r"<!-- GEN:([A-Z_]+):START -->(.*?)<!-- GEN:\1:END -->", re.S)
# имя файла репозитория: путь от корня (или ~/health_scripts/…) с расширением кода/документа.
# Перед именем не должно стоять «.», «/», «~»: ~/.infrastructure.md и ~/x.py — файлы дома, не репо.
_REF = re.compile(r"(?<![\w./~-])(?:~/health_scripts/)?"
                  r"((?:[a-z_][\w-]*/)*[a-z_][\w-]*\.(?:py|sh|md|html|yaml))\b")


def _exists(ref: str, root: Path) -> bool:
    if "/" in ref:
        return (root / ref).exists()
    # голое имя: модуль может жить в подкаталоге
    return (root / ref).exists() or any(root.rglob(ref))


# Описание после « — », « -- » или «  # » — проза из докстрингов и комментариев: там законно
# упомянуты файлы вне репозитория (данные тенанта, дом). Судится только то, что блок НАЗЫВАЕТ.
_PROSE = re.compile(r" — | -- |  # ")


def _named_part(line: str) -> str:
    return _PROSE.split(line, maxsplit=1)[0]


def missing_refs(text: str, root: Path) -> list[str]:
    return sorted({f"{name}: {m.group(1)}"
                   for name, body in _BLOCK.findall(text)
                   for line in body.splitlines()
                   for m in _REF.finditer(_named_part(line)) if not _exists(m.group(1), root)})


def test_generated_blocks_name_only_existing_files():
    bad = missing_refs((ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8"), ROOT)
    assert not bad, f"GEN-блоки называют несуществующие файлы (пересобери генераторами): {bad}"


def test_detector_catches_a_deleted_module(tmp_path):
    (tmp_path / "alive.py").write_text("")
    text = ("<!-- GEN:X:START -->\nalive.py  # ok\ngone.py  # удалён\n"
            "~/health_scripts/scripts/gone.sh -- x\n~/.home_file.md ~/home.py\n"
            "  check() — WARN: tenant_data.yaml есть, но не прочитан\n"
            "alive.py  # переехал из gone2.py\n"
            "<!-- GEN:X:END -->\nvne_bloka.py")
    assert missing_refs(text, tmp_path) == ["X: gone.py", "X: scripts/gone.sh"]

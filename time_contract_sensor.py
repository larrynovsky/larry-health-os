"""
time_contract_sensor.py — датчик контракта единого времени (_time_inject).

Ловит прод-код, читающий стенные часы напрямую (datetime.now / date.today /
datetime.utcnow / .utcnow) в обход get_now()/get_today() из _time_inject.

Почему: расщеплённые часы делают recency-логику незамораживаемой в тестах →
фикстуры/датчики гниют по календарю (инцидент 2026-07-21: calendar_client end<now,
GP-гейт week_ago). Контракт _time_inject предписывает читать время через seam.

Гибрид-ратчет (решение 2026-07-21):
  - сайт ЕСТЬ в BASELINE (унаследованное) → WARN — разгребаем без спешки;
  - сайта НЕТ в baseline (новый / в чистом файле) → FAIL — течь не пускаем.

Исключения: строка с маркером `# time-inject: ok`; блок `if __name__ == "__main__":`
до конца файла; сам _time_inject.py и этот файл; каталоги tests/docs/venv/…

Ключ сайта устойчив к сдвигу строк: (relpath, sha1(нормализованный код)), НЕ номер
строки — иначе правка выше сдвигает все ключи и рушит baseline.

Standalone:  python3 time_contract_sensor.py            → WARN/FAIL, exit=1 при FAIL
             python3 time_contract_sensor.py --snapshot → записать baseline
Программно:  check() → (fails, warns); assert_clock_live() — прод-liveness.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASELINE_PATH = ROOT / "time_contract_baseline.txt"
MARKER = "# time-inject: ok"

_CALL_RE = re.compile(
    r"datetime\.now\(|date\.today\(|datetime\.utcnow\(|(?<![\w.])utcnow\("
)
_SKIP_DIRS = {"tests", "docs", "node_modules", ".git", "__pycache__",
              ".venv", "venv", ".mypy_cache", "backups"}
_SELF = {"_time_inject.py", "time_contract_sensor.py"}


def _norm(line: str) -> str:
    return " ".join(line.split("#", 1)[0].split())


def _key(relpath: str, line: str) -> str:
    h = hashlib.sha1(_norm(line).encode("utf-8")).hexdigest()[:12]
    return f"{relpath}\t{h}"


def scan(root: Path = ROOT) -> list[dict]:
    """Нарушения контракта: прямой вызов часов в прод-логике вне seam."""
    out = []
    for dp, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if d not in _SKIP_DIRS]
        for f in fs:
            if not f.endswith(".py") or f in _SELF:
                continue
            p = Path(dp) / f
            rel = str(p.relative_to(root))
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            in_main = False
            for ln, line in enumerate(lines, 1):
                if re.match(r"\s*if __name__\s*==\s*['\"]__main__", line):
                    in_main = True
                if in_main or MARKER in line:
                    continue
                if _CALL_RE.search(line.split("#", 1)[0]):
                    out.append({"key": _key(rel, line), "file": rel,
                                "line": ln, "text": line.strip()[:100]})
    return out


def load_baseline(path: Path = BASELINE_PATH) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")}


def write_baseline(findings: list[dict], path: Path = BASELINE_PATH) -> None:
    keys = sorted({x["key"] for x in findings})
    head = ["# time_contract baseline — унаследованные прямые вызовы часов.",
            "# Формат: <relpath>\\t<sha1_12>. Есть тут → WARN; нет → FAIL (новое).",
            "# Дочистил сайт (перевёл на get_now/get_today) → удали его строку."]
    path.write_text("\n".join(head + keys) + "\n", encoding="utf-8")


def check(root: Path = ROOT) -> tuple[list[dict], list[dict]]:
    """(fails, warns): fails = не в baseline (новое); warns = унаследованное."""
    base = load_baseline()
    fails, warns = [], []
    for f in scan(root):
        (warns if f["key"] in base else fails).append(f)
    return fails, warns


def assert_clock_live() -> None:
    """Прод-инвариант: тестовый клок не должен течь в живой прогон."""
    import _time_inject
    if _time_inject.is_frozen():
        raise AssertionError(
            "time-inject: _TEST_CLOCK не снят — прод читает замороженное время, "
            "freshness-датчики ослепнут. Ищи незакрытый set_test_clock.")


def main() -> int:
    fails, warns = check()
    print(f"[time-contract] WARN(унаследовано)={len(warns)}  FAIL(новое)={len(fails)}")
    for f in fails:
        print(f"  FAIL {f['file']}:{f['line']}  {f['text']}")
    if not BASELINE_PATH.exists():
        print("  (baseline отсутствует — запусти: python3 time_contract_sensor.py --snapshot)")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--snapshot" in sys.argv:
        found = scan()
        write_baseline(found)
        print(f"baseline записан: {len(load_baseline())} ключей из {len(found)} вызовов")
        sys.exit(0)
    sys.exit(main())

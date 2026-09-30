"""log_rotate.py — ротация логов Health OS БЕЗ root, в пространстве пользователя.

Почему свой код, а не newsyslog (лестница, ступень 3 → 6): штатный ротатор macOS требует
записи в /etc/newsyslog.d/ и запускается от root. Агенту sudo закрыт (blockedCommands моста),
а требовать шаг владельца ради ротации — решение владельца 2026-09-01: «поставь без меня».
Конфиг newsyslog остаётся ЕДИНСТВЕННЫМ домом списка логов (launchd/health-logs.newsyslog.conf) —
этот модуль его читает; два исполнителя одного списка одновременно НЕ включать —
установка, взаимное исключение с newsyslog и оракулы: docs/how-to/rotate_logs.md.

Ключевая механика — COPYTRUNCATE, а не rename. Файлы пишут два разных писателя: Python
через logging.FileHandler и launchd через StandardOutPath/StandardErrorPath. Оба держат
ОТКРЫТЫЙ дескриптор: переименуй файл — писатель продолжит писать в переименованный inode,
а живой лог замолчит навсегда (тихая поломка, ровно тот класс, который эта сессия и чинила).
Поэтому: содержимое копируется в архив, затем файл обрезается НА МЕСТЕ (inode сохраняется).

Публичный контракт: rotate_all(conf_path=None, dry_run=False) -> list[dict]
и unlisted_logs(conf_path=None, min_mb=1.0) -> list[dict] — датчик слепого пятна
(лог растёт, а в конфиге его нет) и receipt_status(path=None) -> dict — квитанция
последнего прогона (жив ли агент и прошла ли самопроверка). Потребитель обеих —
integrity_tests; пороги держит он, здесь только факты.
"""
# INTENT: log_rotation — ротация логов: файл подрезается, а писатель не осиротеет.
#          Замысел и инварианты — subsystem_intent.yaml, раздел log_rotation.
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import time
import re
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_CONF = Path(__file__).resolve().parent / "launchd" / "health-logs.newsyslog.conf"


# Путь лога, объявленный плистом (редирект launchd). Один дом правила: его читают и
# установщик (cover), и сторож периметра tests/unit/test_log_rotate.py.
DECLARED = re.compile(r'<key>Standard(?:Out|Error)Path</key>\s*<string>([^<]+)</string>')


def cover(plist_texts, conf_path: Path, user: str) -> list[str]:
    """Дописать в конфиг ротации логи, которые объявляют эти плисты и которых в нём нет.

    28.09.2026: установщик рендерил плисты второго человека (…_partner.log), а конфиг
    ротации — рукописный список — о них не знал: 27 логов росли без предела, красный тест
    заметил это только при закрытии нити. Периметр берётся там, где лог объявлен.
    Возвращает дописанные пути."""
    listed = {r["path"] for r in _parse_conf(conf_path)} if conf_path.exists() else set()
    new: list[str] = []
    for text in plist_texts:
        for p in DECLARED.findall(text):
            if Path(p) not in listed and p not in new:
                new.append(p)
    if new:
        with conf_path.open("a", encoding="utf-8") as f:
            f.write("\n# дописано установщиком (log_rotate.cover): логи из собранных плистов\n")
            for p in new:
                f.write(f"{p:<55} {user}:staff 644 2 5000 * J\n")
    return new


def _parse_conf(conf_path: Path) -> list[dict]:
    """Строки конфига newsyslog → [{path, keep, size_kb}]. Дом списка — конфиг, не этот файл."""
    out: list[dict] = []
    for raw in conf_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if len(f) != 7 or not f[0].startswith("/"):
            log.warning("строка конфига не разобрана, пропущена: %s", line[:60])
            continue
        try:
            out.append({"path": Path(f[0]), "keep": int(f[3]), "size_kb": int(f[4])})
        except ValueError:
            log.warning("нечисловые keep/size, строка пропущена: %s", line[:60])
    if not out:
        # §7/§14: мёртвый конфиг обязан кричать, а не возвращать пустоту. Пустой список
        # неотличим от «всё чисто»: ротация промолчала бы, а датчик слепого пятна дал бы
        # фальшивый зелёный — ровно тот класс, от которого он и заведён.
        raise ValueError(f"конфиг ротации пуст или не разобран ни одной строкой: {conf_path}")
    return out


def unlisted_logs(conf_path: Path | None = None, min_mb: float = 1.0) -> list[dict]:
    """Логи, которые РАСТУТ в известных домах, но в конфиге ротации не названы.

    Датчик слепого пятна: ротация подрезает только то, что перечислено, и молчит про
    остальное — ровно так 470 МБ копились с мая, а `consilium_eval.log`, ради которого
    ротация и заводилась, в списке даже не значился. Без этого датчика новый крупный лог
    обнаруживается глазами в `ls -laS`, то есть случайно.

    `over_threshold` — файл уже перерос САМЫЙ МЯГКИЙ порог из конфига, то есть был бы
    ротирован, будь он в списке. Число живёт в конфиге и только там; здесь его нет.

    ГРАНИЦА (осознанная): дома логов вычисляются из самого конфига — берутся каталоги
    перечисленных путей. Лог в НОВОМ каталоге остаётся невидимым. Второй список каталогов
    был бы вторым домом и разошёлся бы с первым; цена названа в docs/how-to/rotate_logs.md.
    """
    rows = _parse_conf(conf_path or _DEFAULT_CONF)   # мёртвый конфиг здесь кричит, не молчит
    listed = {r["path"].resolve() for r in rows}
    threshold_mb = min(r["size_kb"] for r in rows) / 1024
    out: list[dict] = []
    for d in {r["path"].parent for r in rows}:
        for f in sorted(d.glob("*.log")):
            try:
                if f.resolve() in listed:
                    continue
                mb = f.stat().st_size / 1048576
            except OSError:
                continue                 # исчез между glob и stat — не наше дело
            if mb >= min_mb:
                out.append({"path": str(f), "mb": round(mb, 1),
                            "over_threshold": mb >= threshold_mb})
    return sorted(out, key=lambda r: -r["mb"])


def default_receipt(start: Path) -> Path:
    """Где агент пишет квитанцию: `logs/` ГЛАВНОЙ копии, а не дерева, из которого спросили.

    Плист агента жёстко пишет stdout в главную копию. Путь от `__file__` в дереве нити
    вёл в `logs/` этого дерева (там квитанции нет никогда), и pre-commit каждой нити
    печатал «агент не запускался» при 159 живых прогонах (замер 21.09). Главная копия
    берётся у git (`--git-common-dir`), как в install_hooks.sh; не git — прежний путь.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).parent / "logs" / "logrotate.log"
    except (OSError, subprocess.SubprocessError):
        pass                             # нет git — не главная копия, путь от кода
    return start / "logs" / "logrotate.log"


def receipt_status(path: Path | None = None) -> dict:
    """Квитанция последнего прогона агента ротации: жив ли он и чисто ли отработал.

    Агент печатает JSON результата и строку `selftest ok` в свой stdout-лог. Значит у
    механизма ЕСТЬ датируемый артефакт — mtime файла даёт liveness, а хвост даёт то, чего
    heartbeat не даёт (§14): прогон не просто СЛУЧИЛСЯ, а прошёл самопроверку copytruncate.
    Умерший ротатор иначе молчит идеально: логи растут, но каждый из них ЕСТЬ в конфиге,
    поэтому датчик слепого пятна на них не сработает — эту дыру закрывает именно квитанция.

    → {"exists": bool, "age_h": float|None, "selftest_ok": bool}. Пороги — не здесь:
    их держит потребитель (integrity_tests), как и у check_db_size.
    """
    path = path or default_receipt(Path(__file__).resolve().parent)
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "age_h": None, "selftest_ok": False}
    age_h = max(0.0, (time.time() - st.st_mtime) / 3600)
    try:
        tail = path.read_text(encoding="utf-8", errors="ignore")[-4000:]
    except OSError:
        tail = ""
    return {"exists": True, "age_h": round(age_h, 2),
            "selftest_ok": tail.rstrip().endswith("selftest ok")}


def _shift_archives(path: Path, keep: int) -> None:
    """log.2.gz → log.3.gz, …, log.0.gz → log.1.gz; самый старый удаляется."""
    oldest = path.with_suffix(path.suffix + f".{keep - 1}.gz")
    if oldest.exists():
        oldest.unlink()
    for i in range(keep - 2, -1, -1):
        src = path.with_suffix(path.suffix + f".{i}.gz")
        if src.exists():
            src.rename(path.with_suffix(path.suffix + f".{i + 1}.gz"))


def _rotate_one(entry: dict, dry_run: bool) -> dict | None:
    """Один файл: архив + обрезка НА МЕСТЕ. None — ротация не нужна."""
    path, keep, size_kb = entry["path"], entry["keep"], entry["size_kb"]
    try:
        size = path.stat().st_size
    except OSError:
        return None                      # файла нет на этой машине — конфиг общий для двух
    if size < size_kb * 1024:
        return None
    res = {"path": str(path), "was_mb": round(size / 1048576, 1), "dry_run": dry_run}
    if dry_run:
        return res
    inode_before = path.stat().st_ino
    _shift_archives(path, keep)
    arch = path.with_suffix(path.suffix + ".0.gz")
    with open(path, "rb") as src, gzip.open(arch, "wb") as dst:
        shutil.copyfileobj(src, dst)
    # ОБРЕЗКА НА МЕСТЕ: дескриптор писателя остаётся валидным, inode тот же.
    with open(path, "r+b") as fh:
        fh.truncate(0)
    res["archive"] = str(arch)
    res["inode_kept"] = path.stat().st_ino == inode_before
    if not res["inode_kept"]:            # не должно случиться; молчать нельзя
        log.error("inode файла %s изменился — писатель мог осиротеть", path)
    return res


def rotate_all(conf_path: Path | None = None, dry_run: bool = False) -> list[dict]:
    """Ротировать всё, что переросло порог. Возвращает список ротированных (пустой — нечего)."""
    conf = Path(conf_path) if conf_path else _DEFAULT_CONF
    done = []
    for entry in _parse_conf(conf):
        try:
            r = _rotate_one(entry, dry_run)
        except Exception as e:  # noqa: BLE001 — один битый файл не отменяет остальные
            log.error("ротация %s не удалась: %s", entry["path"], e)
            continue
        if r:
            done.append(r)
            log.info("ротирован %s (%s МБ) → %s", r["path"], r["was_mb"], r.get("archive", "dry-run"))
    return done


if __name__ == "__main__":
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="Ротация логов Health OS (copytruncate, без root)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что было бы сделано")
    ap.add_argument("--conf", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(_json.dumps(rotate_all(a.conf, a.dry_run), ensure_ascii=False))

    # Сырой архив Apple Health (26.09): тот же агент гигиены диска, отдельный шаг. Сжатие, а не
    # удаление — от глубины архива зависит правило «прибора нет» (см. hae_checker.compress_raw_archive).
    if not a.dry_run:
        import hae_checker
        import secrets_paths
        for _db in secrets_paths.tenant_db_paths():
            _rest = Path(_db).parent / "hae_rest"
            if _rest.is_dir():
                try:
                    print("hae_rest", _rest.parent.parent.name, hae_checker.compress_raw_archive(_rest))
                except Exception as e:  # noqa: BLE001 — архив не отменяет ротацию логов и самопроверку
                    log.error("сжатие архива %s не удалось: %s", _rest, e)

    # Самопроверка контракта (§ponytail: нетривиальная логика оставляет запускаемую проверку).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.log"
        p.write_bytes(b"x" * 3000)
        conf = Path(td) / "c.conf"
        conf.write_text(f"{p} user:staff 644 2 1 * J\n", encoding="utf-8")
        ino = p.stat().st_ino
        assert rotate_all(conf), "порог 1 КБ при 3 КБ — ротация обязана произойти"
        assert p.stat().st_size == 0 and p.stat().st_ino == ino, "copytruncate нарушен"
        assert (Path(td) / "t.log.0.gz").exists(), "архив не создан"
        assert not rotate_all(conf), "пустой файл ротировать нечего"
    print("selftest ok")

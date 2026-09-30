#!/usr/bin/env python3.11
"""genome_intake.py — сырой геном, присланный боту, → геном-конвейер тенанта.

Один домен: «этот файл во входящих — сырые данные генома? если да, безопасно достать,
привести к одной форме и отдать genome_pipeline». Геном не разбирает и в БД не пишет —
этим владеет genome_pipeline (порядок стадий S1…S7, C-24).

Почему по СОДЕРЖИМОМУ, а не по расширению: одинаково выглядят 23andMe (.txt, TSV),
AncestryDNA (.txt, две колонки аллелей, хромосомы 23–26), MyHeritage/FTDNA (.csv в кавычках),
tellmeGen (.zip с CSV rsid,chromosome,position,genotype, пропуск «–»), LivingDNA. Их общий
признак — строки «rsID · хромосома · позиция · генотип», его и ищем (замер форматов 24.09:
beholdgenealogy.com/?p=2700, help.tellmegen.com «Raw Data technical characteristics»).
VCF полного генома (tellmeGen Ultra, Атлас; .vcf или .vcf.gz) с 28.09 принимается: его ведёт
свой конвейер vcf_import_pipeline (позиции, фаза R панели, ClinVar через myvariant.info),
отдельным процессом — разбор идёт часами. FASTQ (сырые прочтения) — честный отказ.

Архив (WSTG-BUSL-09): имена членов архива в путь НЕ идут никогда (zip-slip невозможен по
построению), распаковка потоком с потолком байт (zip-бомба), не больше MAX_MEMBERS членов.

Запуск: lab_intake_watcher зовёт process_pending() в своём цикле (Studio, окружение
тенанта); долгий конвейер (аннотация по сети, конституции моделью) идёт ОТДЕЛЬНЫМ процессом
`genome_intake.py --run <файл>`, чтобы вотчер и боты не ждали. Состояние — сайдкар
<файл>.genome.json рядом с файлом (конвенция инбокса: .failed/.norows/.triage.json).
"""
from __future__ import annotations

import hashlib
import i18n
import notify
from link_fetch import display_filename
import json
import logging
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

log = logging.getLogger("genome_intake")

MAX_BYTES = 300 * 1024 * 1024       # потолок распакованного члена/файла: 23andMe ≈ 25 МБ
# VCF полного генома: без сжатия 0,7–1,5 ГБ, .vcf.gz — 0,15–0,4 ГБ (файл владельца: 722 МБ).
MAX_VCF_BYTES = 3 * 1024 * 1024 * 1024
MAX_DOWNLOAD_BYTES = MAX_VCF_BYTES  # потолок скачивания по ссылке (link_fetch): крупнейший формат
# Резерв порогов «геном другого человека поверх своего чипа». НЕ норма, а ЗЕРКАЛО сидов
# system_config (health_db._migrate_patient_profile_and_config); равенство держит
# coherence-тест. Читать через chip_identity_limits(), не эту константу (решение владельца
# 29.09: порог — в настройках, §9 п.4).
_CHIP_FALLBACK = {"genome.chip_disagreement_max": 0.005,     # доля расхождений с чипом
                  "genome.chip_identity_min_matched": 1000}  # меньше общих позиций — не судим
MAX_MEMBERS = 50
SNIFF_BYTES = 256 * 1024
MIN_ROWS = 20                       # столько валидных строк в начале файла = это геном
SIDECAR = ".genome.json"
# Один дом списка форматов для людей (квитанция бота, знакомство) — рядом с тем, что их читает.
# Поставщики сырых данных, которые разборщик узнаёт по содержимому. Один дом перечня: из него
# собираются и русский текст ниже, и строка знакомства на языке человека (i18n, 28.09).
PROVIDERS = ("23andMe", "AncestryDNA", "MyHeritage", "FTDNA", "tellmeGen (Starter/Advanced)", "LivingDNA")
SUPPORTED_TEXT = "исходный файл от " + ", ".join(PROVIDERS[:-1]) + " или " + PROVIDERS[-1]
_SKIP_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".json",
             ".failed", ".norows"}
_CHROM = {**{str(i): str(i) for i in range(1, 23)}, "X": "X", "Y": "Y", "MT": "MT",
          "M": "MT", "23": "X", "24": "Y", "25": "X", "26": "MT", "XY": "X"}
_NOCALL = {"", "0", "00", "-", "--", "–", "—", "NC", "N", "NN", "?", "??"}
_SPLIT = re.compile(r"[\t, ]+")
_RSID = re.compile(r"^(rs|i)\d+$")


def _row(line: str):
    """Строка любого из форматов-массивов → (rsid, chrom, pos, genotype) или None."""
    if not line or line[0] in "#@":
        return None
    parts = [p.strip().strip('"') for p in _SPLIT.split(line.strip())]
    parts = [p for p in parts if p != ""]   # двойной таб 23andMe: rsid, '', chrom, pos, gt
    if len(parts) == 5:
        rsid, chrom, pos, a1, a2 = parts
        gt = (a1 + a2) if (a1 + a2).upper() not in _NOCALL else "--"
    elif len(parts) == 4:
        rsid, chrom, pos, gt = parts
    else:
        return None
    rsid = rsid.lower()
    chrom = _CHROM.get(chrom.upper().removeprefix("CHR"))
    if not _RSID.match(rsid) or chrom is None or not pos.isdigit():
        return None
    gt = gt.upper()
    if gt in _NOCALL or len(gt) > 2 or set(gt) - set("ACGTDI"):
        gt = "--"
    return rsid, chrom, int(pos), gt


def _read_capped(fh, cap: int | None = None) -> bytes:
    """Прочитать поток целиком, но не больше cap байт — иначе ValueError (zip-бомба)."""
    cap = MAX_BYTES if cap is None else cap
    out = bytearray()
    while chunk := fh.read(1 << 20):
        out += chunk
        if len(out) > cap:
            raise ValueError(i18n.t("intake.genome.too_large", size_mb=cap // (1024 * 1024)))
    return bytes(out)


def chip_identity_limits(conn=None) -> tuple[float, int]:
    """(доля расхождений с чипом, минимум общих позиций) — из system_config тенанта.

    Выше доли — геном другого человека, фаза A ничего не пишет. Свой WGS против своего
    чипа дал 0 из 157 950 (замер 28.09), у разных людей расходятся десятки процентов.
    `conn` — БД того же тенанта (фаза A передаёт свою). Нет таблицы — резерв, громко."""
    out = {}
    for key, fallback in _CHIP_FALLBACK.items():
        try:
            import config_db
            out[key] = float(config_db.get_config(key, fallback, conn=conn))
        except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО
            log.warning("%s недоступен в БД, взят резерв %s (§14: тихий fallback запрещён)",
                        key, fallback)
            out[key] = fallback
    return out["genome.chip_disagreement_max"], int(out["genome.chip_identity_min_matched"])


def _kind(head: bytes) -> dict | None:
    """Первые байты текста → {'format', 'supported'} или None (это не геном)."""
    if head[:2] == b"\x1f\x8b":                  # .vcf.gz / bgzip: смотрим распакованное начало
        import zlib
        try:                                      # начало файла усечено — decompressobj это терпит
            head = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head, SNIFF_BYTES)
        except zlib.error:
            return None
    text = head.decode("utf-8", errors="ignore").lstrip("﻿")
    if text.startswith("##fileformat=VCF"):
        return {"format": "vcf", "supported": True}
    lines = text.splitlines()[:400]
    if len(lines) > 3 and lines[0].startswith("@") and lines[2].startswith("+"):
        return {"format": "fastq", "supported": False}
    rows = sum(1 for ln in lines if _row(ln))
    if rows < MIN_ROWS:
        return None
    header = next((ln.lower() for ln in lines if ln and ln[0] != "#"), "")
    fmt = ("ancestry" if "allele1" in header else
           "myheritage_ftdna" if "result" in header else
           "tellmegen_csv" if header.startswith(("rsid,", '"rsid"')) else "23andme_like")
    return {"format": fmt, "supported": True}


def sniff(path: Path) -> dict | None:
    """Файл (или .zip) → {'format', 'supported', 'member'} либо None, если это не геном.
    Членов архива не распаковывает — читает только начало каждого."""
    path = Path(path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
                if len(infos) > MAX_MEMBERS:
                    return {"format": "zip_too_many_files", "supported": False, "member": None}
                for i in infos:
                    with zf.open(i) as fh:
                        k = _kind(fh.read(SNIFF_BYTES))
                    if k and k["format"] == "vcf":   # VCF распаковывать в память нельзя — гигабайты
                        return {"format": "vcf_in_zip", "supported": False, "member": i.filename}
                    if k:
                        return {**k, "member": i.filename}
            return None
        with open(path, "rb") as fh:
            k = _kind(fh.read(SNIFF_BYTES))
        return {**k, "member": None} if k else None
    except (OSError, zipfile.BadZipFile, RuntimeError) as e:   # битый/зашифрованный архив
        log.warning(f"sniff {path.name}: {e}")
        return None


def _normalize(path: Path, member: str | None, out: Path) -> tuple[int, str]:
    """Источник → TSV в форме 23andMe (её читает genome_parser). Возвращает (строк, file_id).
    file_id = sha256 исходного содержимого: тот же файл дважды — та же идентичность
    (identity-guard genome_parser отличит повтор от чужого генома)."""
    if member is not None:
        with zipfile.ZipFile(path) as zf, zf.open(member) as fh:
            raw = _read_capped(fh)
    else:
        with open(path, "rb") as fh:
            raw = _read_capped(fh)
    file_id = "sha256:" + hashlib.sha256(raw).hexdigest()[:32]
    n = 0
    with open(out, "w", encoding="utf-8") as w:
        w.write(f"# genome_intake: нормализовано из {path.name}\n# file_id: {file_id}\n")
        for line in raw.decode("utf-8", errors="ignore").splitlines():
            r = _row(line)
            if r:
                w.write("%s\t%s\t%d\t%s\n" % r)
                n += 1
    return n, file_id


def _state(path: Path) -> dict | None:
    sc = path.with_name(path.name + SIDECAR)
    try:
        return json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else None
    except (OSError, ValueError):
        return {"status": "unreadable"}


def _set_state(path: Path, **st) -> None:
    path.with_name(path.name + SIDECAR).write_text(
        json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _tell(msg: str, reply_markup=None) -> None:
    """Человеку-тенанту (notify → его chat_id), не оператору: это ЕГО геном."""
    try:
        import notify
        kw = {"reply_markup": reply_markup} if reply_markup is not None else {}
        notify.notify(msg, **kw)
    except Exception as e:  # silent-ok: доставка best-effort, статус уже в сайдкаре
        log.error(f"genome_intake: уведомление не ушло: {e}")


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _spawn(path: Path) -> int:
    logf = open(path.with_name(path.name + ".genome.log"), "a")
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run", str(path)],
                         stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
                         env=os.environ.copy())
    return p.pid


def process_pending(inbox: Path, spawn=_spawn) -> int:
    """Один проход по входящим тенанта: новый геном → фоновый конвейер. Возвращает число
    файлов, по которым что-то сделано. Зовётся из цикла lab_intake_watcher."""
    inbox = Path(inbox)
    n = 0
    for f in sorted(inbox.iterdir()) if inbox.is_dir() else []:
        if not f.is_file() or f.name.startswith(".") or ".genome." in f.name \
                or f.suffix.lower() in _SKIP_EXT:
            continue
        st = _state(f)
        if st and st.get("status") in ("queued", "running") and not _alive(st.get("pid")):
            _set_state(f, **{**st, "status": "failed", "error": "процесс конвейера умер"})
            _tell(i18n.t("intake.genome.interrupted", name=display_filename(f.name)))
            n += 1
            continue
        if st:
            continue
        k = sniff(f)
        if k is None:
            continue
        try:
            if f.stat().st_size > (MAX_VCF_BYTES if k["format"] == "vcf" else MAX_BYTES):
                continue
        except OSError:
            continue
        if not k["supported"]:
            # человеку об этом уже сказала квитанция бота (handlers.messages._receipt_text)
            _set_state(f, status="unsupported", format=k["format"])
            n += 1
            continue
        _set_state(f, status="queued", format=k["format"], member=k["member"])
        pid = spawn(f)
        _set_state(f, status="queued", format=k["format"], member=k["member"], pid=pid)
        n += 1
    return n


def run_one(path: Path) -> dict:
    """Нормализовать и прогнать genome_pipeline для одного файла (в своём процессе)."""
    import health_db as db
    import infra_config
    path = Path(path)
    st = _state(path) or {}
    if not infra_config.is_primary():
        raise SystemExit("genome_intake: только на основной машине (§8)")
    _set_state(path, **{**st, "status": "running", "pid": os.getpid()})
    if st.get("format") == "vcf":
        return run_vcf(path, st)
    try:
        member = st["member"] if "member" in st else (sniff(path) or {}).get("member")
        out_dir = path.parent / "genome"
        out_dir.mkdir(exist_ok=True)
        norm = out_dir / (path.stem + ".23andme.txt")
        rows, file_id = _normalize(path, member, norm)
        if rows == 0:
            raise ValueError(i18n.t("intake.genome.no_rows"))
        db.init_db()
        with db.get_conn() as c:
            existing = c.execute("SELECT COUNT(*) FROM raw_snps").fetchone()[0]
        if existing:
            res = {**st, "status": "already_loaded", "rows": rows, "existing": existing}
            _set_state(path, **res)
            _tell(i18n.t("intake.genome.already_loaded", existing=existing, name=display_filename(path.name)))
            return res
        import genome_pipeline
        summary = genome_pipeline.run_pipeline(filepath=str(norm))
        res = {**st, "status": "done", "rows": rows, "file_id": file_id,
               "summary": {k: v for k, v in (summary or {}).items() if isinstance(v, (int, str))}}
        _set_state(path, **res)
        from bot import actions
        _tell(i18n.t("intake.genome.loaded", rows=rows, name=display_filename(path.name)),
              reply_markup=actions.keyboard([
                  actions.button(i18n.t("actions.genome.question"), "gq", "")]))
        return res
    except Exception as e:
        log.error(f"genome_intake run {path.name}: {e}", exc_info=True)
        _set_state(path, **{**st, "status": "failed", "error": str(e)[:300]})
        known = {
            i18n.t("intake.genome.too_large", size_mb=MAX_BYTES // (1024 * 1024)): "intake.genome.file_too_large",
            i18n.t("intake.genome.no_rows"): "intake.genome.file_no_rows",
        }
        key = known.get(str(e), "common.error.our_side") if isinstance(e, ValueError) else "common.error.our_side"
        file_id = hashlib.sha256(path.name.encode()).hexdigest()[:16]
        _tell(notify.fault(f"genome_intake.run_one file_id={file_id}: {type(e).__name__}: {e}",
                           person_key=key, size_mb=MAX_BYTES // (1024 * 1024)))
        return {"status": "failed", "error": str(e)}


def run_vcf(path: Path, st: dict) -> dict:
    """VCF полного генома → vcf_import_pipeline (все фазы) отдельным процессом того же тенанта.

    Поверх своего чипа VCF уточняет генотипы; чужой геном поверх чипа отсекает фаза A
    (доля несовпадений выше порога chip_identity_limits() — отказ до записи)."""
    _tell(f"Принял «{path.name}» — это полный геном (VCF). Разбор идёт часами: позиции чипа, "
          "фармакогены, поиск значимых вариантов в ClinVar. Итог пришлю отдельным сообщением.")
    log_path = path.with_name(path.name + ".genome.vcf.log")   # «.genome.» — проход входящих его пропускает
    with open(log_path, "a") as logf:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "vcf_import_pipeline.py"),
                            "--phase", "all", "--vcf", str(path)],
                           stdout=logf, stderr=subprocess.STDOUT, env=os.environ.copy())
    if r.returncode == 0:
        res = {**st, "status": "done", "format": "vcf"}
        _set_state(path, **res)
        _tell(f"Полный геном из «{path.name}» разобран. Спросить о нём можно командой /genome.")
        return res
    tail = log_path.read_text(errors="ignore").strip().splitlines()[-1:] if log_path.exists() else []
    why = "это, похоже, геном другого человека: он расходится с твоим чипом" \
        if tail and "GenomeIdentityError" in tail[0] else f"разбор оборвался (код {r.returncode})"
    res = {**st, "status": "failed", "format": "vcf", "error": (tail[0] if tail else "")[:300]}
    _set_state(path, **res)
    _tell(f"Не загрузил полный геном из «{path.name}»: {why}.")
    return res


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Геном из входящих тенанта → genome_pipeline")
    ap.add_argument("--run", help="файл во входящих: нормализовать и прогнать конвейер")
    a = ap.parse_args()
    if a.run:
        print("GENOME_INTAKE", run_one(Path(a.run)))

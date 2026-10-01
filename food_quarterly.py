"""
food_quarterly.py — квартальная доставка пищевого профиля ДОКУМЕНТОМ в Telegram (per-tenant).

Решение владельца 2026-07-14: раз в 3 месяца, отдельный документ каждому. Рендер ДЕТЕРМИНИРОВАННЫЙ
(food_profile.render_food_document) — LLM не решает. Идемпотентно: маркер квартала в data-дире
тенанта не даёт дубля. Пьедестал — daily job (jobs/scheduled), вызывает maybe_deliver per-tenant.
"""
from __future__ import annotations
from _time_inject import get_today  # seam

import datetime
import logging
import os
import subprocess
from pathlib import Path

import health_db as db
import food_profile as fp
import i18n
from clinical_kb import clinical_kb_language
from secrets_paths import secrets_dir

log = logging.getLogger(__name__)
_QUARTER_MONTHS = {1, 4, 7, 10}


def _quarter(d: datetime.date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _marker() -> Path:
    base = Path(os.environ.get("HEALTH_DATA_DIR") or (Path.home() / "health"))
    return base / "data" / "food_profile_last_quarter.txt"


def should_deliver(today: datetime.date | None = None) -> bool:
    """Первые 7 дней квартального месяца (1/4/7/10) и ещё не слали в этом квартале."""
    today = today or get_today()
    if today.month not in _QUARTER_MONTHS or today.day > 7:
        return False
    m = _marker()
    return not (m.exists() and m.read_text().strip() == _quarter(today))


def deliver(today: datetime.date | None = None) -> str:
    """Строит профиль текущего тенанта, рендерит, шлёт документом в ЕГО Telegram, ставит маркер."""
    today = today or get_today()
    prof = db.get_profile_context()
    lang = clinical_kb_language(profile=prof)
    name = next(iter((prof.get("identity", {}).get("name") or "").split()),
                i18n.t("food.document.unnamed_person", lang))
    model = fp.build_food_profile(month=today.month, profile=prof, lang=lang)
    doc = fp.render_food_document(name, model)

    sd = secrets_dir()
    tok = (sd / "telegram_token").read_text().strip()
    cid = (sd / "telegram_chat_id").read_text().strip()
    path = Path("/tmp") / f"food_profile_{name}.md"
    path.write_text(doc)
    subprocess.run(
        ["curl", "-sS", "-m", "30", "-F", f"chat_id={cid}",
         "-F", "caption=" + i18n.t('food.document.caption', lang),
         "-F", f"document=@{path}",
         f"https://api.telegram.org/bot{tok}/sendDocument"],
        capture_output=True, timeout=45)

    m = _marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(_quarter(today))
    log.info("food_quarterly доставлен: %s (%s)", name, _quarter(today))
    return doc


def has_personal_basis(conn=None, profile: dict | None = None) -> bool:
    """Есть ли у человека хоть одно личное основание профиля: проблема в медкарте, геном или
    рост с весом. 01.10: человек, поставивший систему из выпуска, в первый же запуск получил
    документ «Выгодные продукты по геному и медкарте» — общий список без единого его факта,
    который владелец узнал как свой (тот же каркас продуктов). Без основания документ
    обещает персональность, которой нет; он придёт в квартале, когда основание появится."""
    import food_profile as _fp
    if _fp._bmi(profile if profile is not None else db.get_profile_context()) is not None:
        return True
    c = conn or db.get_conn()
    for table in ("problem_list", "genetic_variants"):
        try:
            if c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return True
        except Exception:  # silent-ok: таблицы нет — основания из неё тоже нет
            continue
    return False


def maybe_deliver(today: datetime.date | None = None) -> bool:
    """Гвард: доставить, если пора, ещё не слали и есть личное основание. Не роняет вызывающего."""
    try:
        if should_deliver(today) and has_personal_basis():
            deliver(today)
            return True
    except Exception as e:
        log.warning("food_quarterly maybe_deliver упал (не блокируем): %r", e)
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(deliver()[:400])

#!/usr/bin/env python3.11
"""lab_intake_watcher.py — авто-распознавание новых файлов в инбоксе тенанта (L2).

Поллит HEALTH_DATA_DIR/incoming; каждый новый устоявшийся файл → lab_backfill
(сериализованно, очередь-1) → уведомление РЕВЬЮЕРУ (владельцу) со ссылкой на ревью.

Роли (multitenancy): распознаём в БД тенанта (HEALTH_DATA_DIR=partner), но шлём
через `notify.notify_operator` — СЛУЖЕБНЫЙ канал, всегда владельцу, потому что
value-review это роль владельца. Прежняя формулировка («процесс НЕ ставит
HEALTH_SECRETS_DIR, поэтому notify уходит владельцу») была верна до мультитенантности
30.06 и ложна после: плист партнёра HEALTH_SECRETS_DIR ставит, и запросы на ревью
трое суток шли самому партнёру — 19278 сообщений (01.08). Теперь маршрут задан
вызовом, а не побочным следствием окружения.

Идемпотентность: по basename в lab_results_staging. Провал → sidecar .failed
(без ретрай-шторма) + громкое уведомление. Studio-only. launchd KeepAlive.
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import json
import logging
import socket
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import health_db
import infra_config
import lab_backfill
import notify

log = logging.getLogger("lab_intake_watcher")
# basicConfig НЕ на импорте: датчик целостности импортирует этот модуль ради
# watched_dirs(), и настройка корневого логгера на импорте — побочный эффект,
# который меняет вывод чужого процесса. Конфигурируем в main(), где мы хозяева.

POLL_SEC = 60
DEBOUNCE_SEC = 30
DASHBOARD_URL = f"http://{infra_config.STUDIO_HOST}:{infra_config.DASHBOARD_PORT}"  # Tailscale mesh only (НЕ funnel)

# Папка владельца в iCloud, куда он кладёт документы руками. Инбокс тенанта
# (`incoming/`) остаётся: туда пишет телеграм-бот (handlers/messages.handle_document).
_ICLOUD_CR = infra_config.cloud_dir("CR")   # дом пути — infra_config (BL-PUB-12)

# Проверяем расширение ДО чтения файла: JSON-сайдкар изображения не является
# изображением и не должен попадать в распознаватель.
DOC_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}


def _tenant() -> str:
    return Path(health_db.DB_PATH).parent.parent.name or "self"


def _incoming() -> Path:
    return Path(health_db.DB_PATH).parent.parent / "incoming"


def watched_dirs() -> list[Path]:
    """Источники файлов. iCloud CR/ — ТОЛЬКО у владельца: это его личная папка,
    и читать её из процесса партнёра было бы утечкой между тенантами.

    ПУБЛИЧНО намеренно: датчик тишины входа обязан смотреть в те же каталоги,
    что и вотчер, иначе у списка источников заводится второй дом и он молча
    разъедется. Это КОНФИГУРАЦИЯ, а не суждение — суждение «взять/не взять»
    датчик считает сам и по другому пути (§17), иначе он подтвердил бы
    согласованность вотчера с самим собой.
    """
    dirs = [_incoming()]
    if _tenant() == "health" and _ICLOUD_CR.exists():
        dirs.append(_ICLOUD_CR)
    return dirs


_watched = watched_dirs   # алиас для прежних читателей внутри модуля


STATE_NAME = "lab_intake_state.json"


def heartbeat_for(data_dir: Path) -> Path:
    """Путь к файлу-пульсу тенанта, чьи данные лежат в data_dir.

    ЕДИНСТВЕННЫЙ дом формулы пути. Датчик пульса обходит ЧУЖИХ тенантов и обязан
    спрашивать её здесь, а не собирать путь у себя: иначе у формулы заводится
    второй дом и он разъезжается молча (ср. `watched_dirs` — та же причина).
    """
    return Path(data_dir) / "data" / STATE_NAME


def _state_path() -> Path:
    """Состояние живёт в data/ тенанта, а НЕ сайдкарами рядом с файлами:
    в iCloud CR/ сайдкары были бы мусором в личной папке владельца."""
    return heartbeat_for(Path(health_db.DB_PATH).parent.parent)


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:  # noqa: BLE001 — нет файла/битый: начинаем с чистого
        return {}


def _save_state(st: dict) -> None:
    try:
        _state_path().write_text(json.dumps(st, ensure_ascii=False))
    except OSError as e:
        log.error(f"не смог сохранить состояние: {e}")


def force_lab(path: Path) -> None:
    """Человек сказал «это анализы» — записать его вердикт для вотчера.

    Публичный вход для кнопки «🧪 Анализы» (handlers/callbacks → import_all.
    apply_confirmed_type). Кнопка НЕ распознаёт сама: распознавание идёт две
    минуты, и телеграм-колбэк всё это время выглядел бы зависшим. Вместо этого
    здесь снимается вердикт «не лаб» и файл помечается принудительным — вотчер
    возьмёт его следующим опросом, минуя и гейт, и водяной знак, и пришлёт
    обычное уведомление со ссылкой на ревью.

    Почему вердикт человека сильнее водяного знака: файл может лежать в CR/
    с прошлого года, и именно это делает кнопку осмысленной — она про архив
    ровно так же, как про новое.
    """
    st = _load_state()
    st["notlab"] = sorted(set(st.get("notlab", [])) - {path.name})
    st["forced"] = sorted(set(st.get("forced", [])) | {path.name})
    _save_state(st)
    log.info(f"человек подтвердил тип «lab»: {path.name} — возьмём следующим опросом")


def _is_lab(path: Path) -> bool:
    """Гейт: распознаватель зовём ТОЛЬКО на лабораторные бланки.

    Архив может содержать документы разных типов. Текстовая классификация
    исключает неподходящие файлы до платного vision-вызова.
    """
    import import_all
    try:
        return import_all.classify(path, import_all.extract_text(path)) == "lab"
    except Exception as e:  # noqa: BLE001 — нечитаемый файл не лаб и не авария
        log.warning(f"classify failed {path.name}: {e}")
        return False


def _processed() -> set[str]:
    """basename'ы, уже присутствующие в staging (идемпотентность)."""
    try:
        c = sqlite3.connect(f"file:{health_db.DB_PATH}?mode=ro", uri=True)
        rows = c.execute("SELECT DISTINCT source_file FROM lab_results_staging").fetchall()
        c.close()
        return {Path(r[0]).name for r in rows if r[0]}
    except Exception as e:
        log.warning(f"_processed query failed: {e}")
        return set()


def _stable(p: Path) -> bool:
    """Файл дописан: mtime старше DEBOUNCE_SEC (дебаунс на дозапись)."""
    try:
        return (time.time() - p.stat().st_mtime) > DEBOUNCE_SEC
    except OSError:
        return False


def heartbeat_path() -> Path:
    """Публичное имя файла-пульса для датчика (§14). Пульс — mtime состояния.

    Отдельного поля и отдельного файла нет намеренно: состояние уже пишется в
    data/ тенанта, и `touch` каждый поллинг делает его mtime честным сигналом
    «цикл крутится». PID из launchctl этого не доказывает — процесс может жить
    и не опрашивать (KeepAlive перезапустит только упавший).
    """
    return _state_path()


def select_intake_jobs(plists, module_name: str) -> list[tuple[str, Path]]:
    """Чистое ядро периметра: [(label, dict плиста)] → [(label, файл-пульса)].

    Джоба наша, если ИСПОЛНЯЕТ этот модуль, а не если названа как он. Партнёрская
    зовётся `com.larry.health.watcher.partner`, поиск по имени «lab-intake» её не
    находил, и датчик пульса трое суток считал, что её не существует.
    Джоба без HEALTH_DATA_DIR пропускается: пульс без тенанта не адресуем.
    """
    out = []
    for label, d in plists:
        args = [a for a in (d.get("ProgramArguments") or []) if isinstance(a, str)]
        if not any(Path(a).name == module_name for a in args):
            continue
        data_dir = (d.get("EnvironmentVariables") or {}).get("HEALTH_DATA_DIR")
        if not data_dir:
            continue
        out.append((label, heartbeat_for(Path(data_dir))))
    return out


def intake_jobs() -> list[tuple[str, Path]]:
    """ПЕРИМЕТР ВЫЧИСЛЯЕТСЯ, а не описывается (§18).

    Публично намеренно: список тенантов, за которыми следит датчик пульса, обязан
    иметь один дом, и этот дом — launchd, а не проза в докстроке датчика. Пустой
    список — законный ответ «вотчер не установлен нигде», а не ошибка.
    """
    import plistlib
    import plist_env_liveness
    la = plist_env_liveness.agents_dir()   # в контейнере — плисты из шаблонов расписания
    plists = []
    for p in sorted(la.glob("*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception as e:  # noqa: BLE001 — битый чужой плист не наша авария
            log.warning(f"плист не разобран {p.name}: {e}")
            continue
        plists.append((d.get("Label", p.stem), d))
    return select_intake_jobs(plists, Path(__file__).name)


def process_once() -> int:
    _state_path().touch(exist_ok=True)   # пульс §14: доказывает ЦИКЛ, а не процесс
    import daemon_liveness                # пульс службы в контейнере (этап 2б); натив — no-op
    daemon_liveness.beat()
    state = _load_state()
    if "watermark" not in state:
        # ПЕРВЫЙ запуск: отсекаем всё, что лежало до включения. Иначе вотчер уехал бы
        # по многолетнему архиву CR/ и оплатил бы vision-прогон каждого документа.
        # Водяной знак ставится ОДИН раз и переживает рестарты.
        state["watermark"] = time.time()
        state.setdefault("notlab", [])
        _save_state(state)
        log.info(f"водяной знак поставлен: {datetime.fromtimestamp(state['watermark'])}; "
                 f"файлы старше него не берём")
    watermark = float(state["watermark"])
    notlab = set(state.get("notlab", []))
    forced = set(state.get("forced", []))   # вердикт человека, сильнее обоих гейтов

    done = _processed()
    tenant = _tenant()
    n = 0
    # Ссылки из облака и пути на диске (нить genome-link, 24.09): заявки бота → файлы во
    # входящих; дальше их берут те же разборщики, что и файлы из Telegram. Первым — чтобы
    # скачанное попало к ним в этом же проходе.
    try:
        import link_fetch
        link_fetch.process_requests(_incoming())
    except Exception as e:
        log.error(f"[{tenant}] link_fetch: {e}", exc_info=True)
    # Геном во входящих (нить document-intake, 24.09) — свой разборщик, своим процессом:
    # здесь только поиск и запуск. Ошибка генома не останавливает разбор анализов.
    try:
        import genome_intake
        genome_intake.process_pending(_incoming())   # n — счётчик АНАЛИЗОВ, не смешиваем
    except Exception as e:
        log.error(f"[{tenant}] genome_intake: {e}", exc_info=True)
    # Заключения врачей и прочие документы → события медкарты + предложения под гейт человека
    # (import_medical_events.process_incoming). Тот же принцип: ошибка не валит анализы.
    try:
        import import_medical_events
        import_medical_events.process_incoming(_incoming())
    except Exception as e:
        log.error(f"[{tenant}] import_medical_events: {e}", exc_info=True)
    files = [f for d in _watched() if d.exists() for f in sorted(d.glob("*"))]
    for f in files:
        # Подкаталоги (imaging/, reports/) сюда не попадают: is_file() их отсекает.
        # Табличный распознаватель ждёт только корень (см. contracts/doc_intake.json).
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in DOC_EXTS:
            continue   # сайдкары (.failed/.norows/.triage.json) и всё нечитаемое
        if f.with_suffix(f.suffix + ".failed").exists():
            continue
        if f.with_suffix(f.suffix + ".norows").exists():
            continue
        if f.name in done:
            continue
        if not _stable(f):
            continue
        human = f.name in forced
        try:
            if not human and f.stat().st_mtime < watermark:
                continue          # лежал до включения вотчера — не наш
        except OSError:
            continue
        if not human and f.name in notlab:
            continue              # уже смотрели: не лабораторная таблица
        if not human and not _is_lab(f):
            # Не ошибка и не событие: в CR/ лежат документы разных типов.
            # Запоминаем вердикт, чтобы не перечитывать файл каждую минуту (для
            # сканов это означало бы OCR по кругу), и молчим — сообщать не о чем.
            notlab.add(f.name)
            state["notlab"] = sorted(notlab)
            _save_state(state)
            log.info(f"[{tenant}] не лабораторная таблица, пропуск: {f.name}")
            continue
        run_id = f"intake_{get_now():%Y%m%d_%H%M%S}"
        log.info(f"[{tenant}] recognize {f.name} run={run_id}")
        try:
            summary = lab_backfill.run_backfill(run_id, None, None, str(f), None)
            rows = summary.get("rows", 0)
            pend = summary.get("pending", 0)
            if rows == 0:
                # РАСХОЖДЕНИЕ, а не успех. Файл попал сюда потому, что кто-то счёл его
                # лабораторной таблицей (doc_triage или рука), а распознаватель не увидел
                # ни одной строки. Молчаливое «0 строк, 0 на ревью» читалось бы как
                # «документ пустой» — именно так выглядел бы промах классификатора.
                # Сайдкар .norows обязателен: без него basename не попадает в staging,
                # файл берётся КАЖДЫЙ поллинг, и сигнал превращается в шторм раз в минуту.
                f.with_suffix(f.suffix + ".norows").write_text(f"run={run_id} rows=0")
                notify.fault("lab_intake_watcher: no rows recognized; retry stopped", person_key=None)
                log.warning(f"[{tenant}] 0 строк на {f.name} — помечен .norows")
                n += 1
                continue
            import i18n
            if pend:
                notify.notify_operator(i18n.t("owner.card.intake",
                    url=f"{DASHBOARD_URL}/lab-review/{run_id}?tenant={tenant}"))
            else:
                notify.weekly(i18n.t("owner.weekly.intake"))
            n += 1
        except Exception as e:
            log.error(f"[{tenant}] recognize failed {f.name}: {e}", exc_info=True)
            f.with_suffix(f.suffix + ".failed").write_text(str(e)[:500])
            notify.fault(f"lab_intake_watcher: recognition failed ({type(e).__name__}); retry stopped",
                         person_key=None)
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not infra_config.is_primary():
        raise SystemExit("lab_intake_watcher: Studio-only (canonical host)")
    log.info(f"watcher up; tenant={_tenant()} источники={[str(d) for d in _watched()]}")
    while True:
        try:
            process_once()
        except Exception as e:
            log.error(f"watcher loop error: {e}", exc_info=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()

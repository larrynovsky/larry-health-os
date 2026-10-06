#!/usr/bin/env python3.11
"""lab_intake_watcher.py — авто-распознавание новых файлов в инбоксе тенанта (L2).

Поллит HEALTH_DATA_DIR/incoming; каждый новый устоявшийся файл → lab_backfill
(сериализованно, очередь-1) → уведомление человеку со ссылкой на его ревью.

Роли (решение владельца 02.10): результат бланка — содержательное сообщение
в собственный чат через notify.notify. У отдельного тенанта остаётся также
операторская ссылка без медицинских значений; его подтверждение от неё не зависит.

Замысел гейта «бланк или нет», ожидания пустого ключа и повтора — INTENT: lab_intake_gate
(subsystem_intent.yaml).

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
import lab_recognizer
import notify
import i18n

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


def _save_state(st: dict) -> bool:
    try:
        _state_path().write_text(json.dumps(st, ensure_ascii=False))
        return True
    except OSError as e:
        log.error(f"не смог сохранить состояние: {e}")
        return False


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


def _is_lab(path: Path) -> bool | None:
    """Гейт: распознаватель зовём ТОЛЬКО на лабораторные бланки.

    True — бланк; False — не бланк (его разберёт маршрут заключений); None — файл не читается.

    Два суда по порядку цены. Сначала слова (`import_all.classify`, бесплатно). Их «да» — окончательное,
    их «нет» — НЕТ: словарь слов — данные установки (§9), и на новой установке он пуст (`'{}'`), а
    встроенные признаки — английская шапка, латинская триада анализа крови, грузинский Synevo. Русский
    бланк Инвитро на такой установке получал «общий медицинский документ», и человек слышал «не удалось
    распознать», хотя модель его ни разу не видела (нить lab-intake-retry, 05.10: установка на Windows,
    «большинство чистых PDF не распознаёт»). Поэтому «нет» слов переспрашивается у модели по первой
    странице (`doc_triage`, младшая модель, один кадр) — у неё безопасная сторона «lab» при любом
    сомнении и при сбое, тот же принцип, что у фото из бота.
    """
    import import_all
    try:
        if path.suffix.lower() == ".pdf":
            import fitz
            with fitz.open(str(path)) as doc:
                if doc.page_count > lab_recognizer._MAX_PAGES:
                    raise lab_recognizer.PageLimitExceeded(doc.page_count, lab_recognizer._MAX_PAGES)
        if import_all.classify(path, import_all.extract_text(path)) == "lab":
            return True
        first = lab_recognizer._render_pages(path, [1])[0]
    except lab_recognizer.PageLimitExceeded:
        raise
    except Exception as e:  # noqa: BLE001 — нечитаемый файл не лаб и не авария
        log.warning(f"classify failed {path.name}: {e}")
        return None
    import doc_triage
    verdict = doc_triage.classify_image(first, "image/jpeg")
    log.info(f"слова: не бланк; модель: {verdict['label']} (fallback={verdict['fallback']}) — {path.name}")
    return verdict["label"] == doc_triage.LAB


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


def _tell_person(key: str, **params) -> None:
    """Содержательное — в чат текущего тенанта; недоставка не выдаётся за успех."""
    _tell_person_text(i18n.t(key, **params))


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
    if "outcome_told" not in state:
        # Первый запуск итога по файлу (06.10): всё, что УЖЕ лежит отвергнутым, считается
        # сказанным — иначе после выката каждый старый файл получил бы новое сообщение разом.
        state["outcome_told"] = sorted(str(f) for f in _outcome_candidates(state))
        _save_state(state)
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
        na = f.with_suffix(f.suffix + ".notadmitted")
        if na.exists():
            if not _lab_reading_admitted():
                continue
            na.unlink(missing_ok=True)   # чтение анализов стало допущено (сменили ключ или модель) — разбираем сами
        if f.name in done:
            continue
        wk = f.with_suffix(f.suffix + KEY_WAIT)
        waiting = wk.exists()
        if waiting and _age(wk) < KEY_RETRY_SEC:
            continue   # ждём ключ: повтор не чаще KEY_RETRY_SEC (пустой ключ отвечает отказом бесплатно)
        rt = f.with_suffix(f.suffix + RETRY_WAIT)
        if not retry_due(rt):
            continue   # временный отказ поставщика: следующая попытка ещё не пришла
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
        run_id = f"intake_{get_now():%Y%m%d_%H%M%S}"
        name = _shown(f)
        try:
            lab = True if human else _is_lab(f)
            if not lab:
                # Не бланк (или не читается) — вердикт ГЕЙТА, а не итог по файлу: тот же файл
                # уже читает маршрут заключений (import_medical_events, выше в этом проходе).
                # Человеку говорит _settle_outcomes, когда закончат оба (нить file-outcome, 06.10).
                notlab.add(f.name)
                state["notlab"] = sorted(notlab)
                _save_state(state)
                wk.unlink(missing_ok=True)
                rt.unlink(missing_ok=True)
                log.info(f"[{tenant}] не лабораторная таблица ({lab}), пропуск: {f.name}")
                continue
            log.info(f"[{tenant}] recognize {f.name} run={run_id}")
            summary = lab_backfill.run_backfill(run_id, None, None, str(f), None)
            rows = summary.get("rows", 0)
            wk.unlink(missing_ok=True)
            rt.unlink(missing_ok=True)
            if rows == 0 and summary.get("errors"):
                # Распознаватель упал, а не «прочитал и не нашёл»: run_backfill проглатывает сбой
                # документа в счётчик errors. Это сбой у нас — ветка сбоя ниже, не «0 строк».
                raise RuntimeError(f"lab_backfill: {summary['errors']} ошибок, 0 строк; "
                                   f"первая — {summary.get('first_error', 'не записана')}")
            if rows == 0:
                # Ноль строк — итог РАЗБОРА АНАЛИЗОВ, не файла. Гейт при сомнении говорит «бланк»,
                # и заключение врача с таблицей на первой странице законно попадает сюда; его
                # читает маршрут заключений. Что сказать человеку и сбой ли это — решает
                # _settle_outcomes по обоим разборщикам (нить file-outcome, 06.10).
                # Сайдкар .norows обязателен: без него basename не попадает в staging,
                # файл берётся КАЖДЫЙ поллинг, и распознавание крутилось бы раз в минуту.
                f.with_suffix(f.suffix + ".norows").write_text(f"run={run_id} rows=0")
                log.warning(f"[{tenant}] 0 строк на {f.name} — помечен .norows")
                n += 1
                continue
            from urllib.parse import quote, urlencode
            url = (f"{DASHBOARD_URL}/lab-review/{quote(run_id, safe='')}?" +
                   urlencode({"tenant": tenant, "show": "waiting"}))
            # Анализы нашлись — но тот же файл мог лечь в медкарту и как документ (письмо врача с
            # маркерами в прозе: замер 06.10: из письма врача распознаватель вынул упомянутый в тексте маркер).
            # Обе правды — в одном сообщении, если разбор документов уже закончил (обычно да: он
            # идёт раньше в этом же проходе); иначе его часть доскажет _settle_outcomes.
            import import_medical_events
            doc = import_medical_events.doc_outcome(f)
            text = i18n.t("person.lab.review", file=name, n=rows, url=url)
            if doc is None:
                state.setdefault("lab_told", [])
                state["lab_told"] = sorted(set(state["lab_told"]) | {str(f)})
                _save_state(state)
            elif _is_document_part(doc):
                text += "\n" + _document_text(f, doc, also=True)
            _tell_person_text(text)
            from secrets_paths import is_owner
            if not is_owner():
                notify.notify_operator(i18n.t("owner.card.intake",
                    url=url))
            n += 1
        except Exception as e:
            # Установка поставщика без допуска чтения анализов (DeepSeek, решение владельца
            # 02.10, «А»): не сбой, а предел. Не журнал сбоев (его человек не видит, а ночной
            # ремонт чинить тут нечего) и не .failed (он навсегда): свой маркер, который снимается,
            # когда чтение станет допущено. Канал — служебный (решение владельца 01.08, сторож
            # test_watcher_keeps_operator_and_person_channels): поставщик — на всю установку, отказ бывает
            # только на установке не-anthropic, а там оператор и есть тот, кто прислал документ.
            import hai_core
            if isinstance(e, lab_recognizer.PageLimitExceeded):
                f.with_suffix(f.suffix + ".failed").write_text(f"pages={e.pages} limit={e.limit}")
                _tell_person("person.lab.too_long", file=name, pages=e.pages, limit=e.limit)
                continue
            if isinstance(e, hai_core.ModelNotAdmitted):
                f.with_suffix(f.suffix + ".notadmitted").write_text(str(e)[:500])
                log.warning(f"[{tenant}] чтение анализов не допущено: {f.name} ждёт допуска")
                if notify.notify_operator(i18n.t("person.lab.not_admitted")) == "none":
                    notify.fault("lab_intake_watcher: lab reading not admitted; person not reached",
                                 person_key=None)
                continue
            import llm_client
            if llm_client.is_account_problem(e):
                # Ключ или баланс у поставщика (05.10, установка с нулевым балансом OpenAI): чинит
                # только человек, и после пополнения тот же файл пройдёт. Не .failed навсегда, а
                # ожидание с повтором раз в KEY_RETRY_SEC; сказать — один раз, а не каждый повтор.
                wk.write_text(str(e)[:300])
                log.warning(f"[{tenant}] поставщик отказал по ключу/счёту: {f.name} ждёт ({e})")
                if not waiting:
                    _tell_person("person.lab.key_problem", file=name, provider=_provider_name())
                    from secrets_paths import is_owner
                    if not is_owner():
                        notify.notify_operator(i18n.t("person.lab.key_problem", file=name,
                                                      provider=_provider_name()))
                continue
            transient = llm_client.is_transient(e)
            if transient and not note_transient(rt, e):
                # Обрыв, перегрузка, частота у поставщика (06.10: пять файлов OpenAI упали за
                # секунды, через 40 минут прошли). Человек ничего не делает и ничего не слышит,
                # пока не исчерпан потолок RETRY_BACKOFF_MIN — дальше обычный сбой ниже.
                log.warning(f"[{tenant}] временный отказ поставщика: {f.name} — повтор позже "
                            f"({llm_client.safe_cause(e)})")
                continue
            wk.unlink(missing_ok=True)
            rt.unlink(missing_ok=True)
            log.error(f"[{tenant}] recognize failed {f.name}: {e}", exc_info=True)
            f.with_suffix(f.suffix + ".failed").write_text(llm_client.safe_cause(e, limit=500))
            notify.fault(f"lab_intake_watcher: recognition failed ({type(e).__name__}); retry stopped",
                         person_key=None)
            # После потолка повторов «пришлите через несколько часов» было бы неправдой:
            # несколько часов уже прошли (холодное чтение 06.10: «дойдёт ли он сам?»).
            _tell_person("person.lab.failed_after_retries" if transient else "person.lab.failed",
                         file=name)
    _settle_outcomes(state)
    return n


def _outcome_candidates(state: dict) -> list[Path]:
    """Файлы входящих, по которым разбор анализов ничего не дал (гейт «не бланк» или 0 строк),
    и снимки из reports/ (их разбор анализов не берёт вовсе). Итог по ним зависит от разбора
    документов."""
    inbox = _incoming()
    notlab = set(state.get("notlab", []))
    out = [f for f in sorted(inbox.glob("*")) if f.is_file() and f.suffix.lower() in DOC_EXTS
           and (f.name in notlab or f.with_suffix(f.suffix + ".norows").exists())]
    out += [Path(p) for p in state.get("lab_told", []) if Path(p).exists()]
    reports = inbox / "reports"
    if reports.is_dir():
        out += [f for f in sorted(reports.glob("*")) if f.is_file() and f.suffix.lower() in DOC_EXTS]
    return out


def _settle_outcomes(state: dict) -> None:
    """ИТОГ ПО ФАЙЛУ — один на файл и после ОБОИХ разборщиков (нить file-outcome, 06.10).

    Файл во входящих читают двое: разбор анализов (таблица значений) и разбор документов
    (событие медкарты, диагнозы, лекарства). До 06.10 человеку говорил только первый — и говорил
    за весь файл: заключение врача, принятое в медкарту вторым, человек слышал как «не удалось
    прочитать ни одного значения анализов; в базу ничего не добавлено». Ошибка гейта «бланк или нет»
    обязана стоить лишнего вызова модели, а не ложного ответа человеку.

    Здесь — файлы, по которым анализы не дали ничего. Пока разбор документов не закончен
    (не брался, ждёт ключа), молчим. «Ничего» звучит, только когда пусто у обоих; только тогда
    это и сбой для журнала."""
    import import_medical_events
    told = set(state.get("outcome_told", []))
    notlab = set(state.get("notlab", []))
    for f in _outcome_candidates(state):
        if str(f) in told:
            continue
        doc = import_medical_events.doc_outcome(f)
        if doc is None:
            continue
        told.add(str(f))
        state["outcome_told"] = sorted(told)
        lab_part_told = str(f) in set(state.get("lab_told", []))
        if lab_part_told:
            state["lab_told"] = sorted(set(state["lab_told"]) - {str(f)})
        if not _save_state(state):
            return            # не записали «сказано» — не говорим: иначе повтор каждый проход
        if lab_part_told:     # анализы уже сказаны; досказать документ, если он есть
            if _is_document_part(doc):
                _tell_person_text(_document_text(f, doc, also=True))
            continue
        _tell_person_text(_outcome_text(f, doc, gate_said_not_lab=f.name in notlab))
        if doc.get("status") == "failed" and f.with_suffix(f.suffix + ".norows").exists():
            notify.fault("lab_intake_watcher: no rows recognized and document reader found nothing",
                         person_key=None)


def _is_document_part(doc: dict) -> bool:
    """Есть что сказать про документ РЯДОМ с анализами: документ принят, и это не сам бланк
    (бланк разбор документов кладёт событием «результат анализа» — повторять это незачем)."""
    return doc.get("status") == "imported" and doc.get("kind") != "lab_result"


def _document_text(f: Path, doc: dict, also: bool = False) -> str:
    """also — часть про документ рядом с найденными анализами: холодное чтение 06.10 приняло
    два блока об одном файле за два разных файла, поэтому вторая часть начинается «Кроме чисел»."""
    name = _shown(f)
    date = doc.get("date")
    dated = bool(date and date != "unknown")
    if also:
        parts = [i18n.t("person.file.document_also", date=date) if dated
                 else i18n.t("person.file.document_also_nodate")]
    else:
        parts = [i18n.t("person.file.document", file=name, date=date) if dated
                 else i18n.t("person.file.document_nodate", file=name)]
    items = [i18n.t(key, n=doc[k]) for k, key in (("diagnoses", "person.file.prop_diagnoses"),
                                                   ("medications", "person.file.prop_medications"))
             if doc.get(k)]
    if items:
        parts.append(i18n.t("person.file.proposals", items=", ".join(items)))
    return " ".join(parts)


def _outcome_text(f: Path, doc: dict, gate_said_not_lab: bool) -> str:
    name = _shown(f)
    st = doc.get("status")
    if st == "imported":
        parts = [_document_text(f, doc)]
    elif st == "already":
        parts = [i18n.t("person.file.already", file=name)]
    else:   # failed / ignored — ни анализов, ни документа
        # «Пришлите этот же файл» и «пришлите другой файл» в одном тексте читались как
        # противоречие (холодное чтение 06.10) — поэтому один текст с развилкой «если… если нет».
        maybe_lab = gate_said_not_lab and f.parent == _incoming()
        return i18n.t("person.file.nothing_maybe_lab" if maybe_lab else "person.file.nothing",
                      file=name)
    # «Пришлите как анализы» — только когда разборщики разошлись (гейт: не бланк; документы:
    # результат анализа). Живой прогон 06.10: на 14 заключениях подсказка звучала каждый раз,
    # а расхождений не было ни одного — это был шум.
    disagree = st == "imported" and doc.get("kind") == "lab_result"
    if gate_said_not_lab and f.parent == _incoming() and disagree:
        parts.append(i18n.t("person.file.as_lab_hint"))
    return " ".join(parts)


def _tell_person_text(text: str) -> None:
    if notify.notify(text, fallback=False) != "telegram":
        notify.fault("lab_intake_watcher: person not reached", person_key=None)


KEY_WAIT = ".waitkey"           # сайдкар «ждём ключ/баланс»; mtime — время последней попытки
RETRY_WAIT = ".waitretry"       # сайдкар «временный отказ поставщика»: {attempts, cause}; mtime — последняя
# Потолок повторов при временном отказе — решение владельца 06.10: шесть повторов с растущим
# интервалом (~3 часа). Меньше — человека чаще просят прислать файл заново; больше — дольше тишина.
RETRY_BACKOFF_MIN = (5, 10, 20, 40, 60, 60)


def retry_due(side: Path) -> bool:
    """Пора ли пробовать снова после временного отказа (сайдкара нет — пора)."""
    if not side.exists():
        return True
    try:
        attempts = int(json.loads(side.read_text()).get("attempts", 1))
    except (OSError, ValueError, AttributeError):
        return True
    wait = RETRY_BACKOFF_MIN[min(max(attempts, 1), len(RETRY_BACKOFF_MIN)) - 1] * 60
    return _age(side) >= wait


def note_transient(side: Path, exc: BaseException) -> bool:
    """Записать ещё один временный отказ. True — потолок исчерпан (сайдкар снят: дальше —
    обычный сбой); False — ждём следующей попытки. Общий дом для разбора анализов и документов."""
    import llm_client
    try:
        attempts = int(json.loads(side.read_text()).get("attempts", 0)) if side.exists() else 0
    except (OSError, ValueError, AttributeError):
        attempts = 0
    attempts += 1
    if attempts > len(RETRY_BACKOFF_MIN):
        side.unlink(missing_ok=True)
        return True
    side.write_text(json.dumps({"attempts": attempts, "cause": llm_client.safe_cause(exc)},
                               ensure_ascii=False))
    return False
KEY_RETRY_SEC = 30 * 60        # системная механика: как часто пробовать снова, пока ключ пуст


def _age(p: Path) -> float:
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return float("inf")


def _shown(f: Path) -> str:
    """Имя файла, как его прислал человек: без хеша инбокса (`…__38860e362d5e0b69.pdf`)."""
    from link_fetch import display_filename
    return display_filename(f.name)


def _provider_name() -> str:
    import llm_client
    p = llm_client.provider()
    return {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Gemini",
            "deepseek": "DeepSeek"}.get(p, p)


# Сайдкары отказов: каждый останавливает повторы навсегда (или до своего снятия). Повторная
# присылка того же файла — единственный способ человека сказать «попробуй ещё», поэтому их
# список живёт здесь, рядом с теми, кто их пишет. `.events.failed` пишет маршрут заключений.
_REFUSAL_SIDECARS = (".failed", ".norows", ".notadmitted", KEY_WAIT, RETRY_WAIT, ".events.failed",
                     ".events" + RETRY_WAIT)


def retry_after_refusal(path: Path) -> bool:
    """Тот же файл прислан снова. Если прошлый разбор ОТКАЗАЛ — снять отказ и взять заново.

    True — отказ был и снят (бот отвечает «беру ещё раз»); False — отказа не было (файл разобран
    или ещё в очереди — это настоящий дубль). До 05.10 бот отвечал «уже получал — пропускаю дубль»
    на любой повтор: файл, отвергнутый при нулевом балансе, нельзя было разобрать никогда —
    отпечаток содержимого тот же, переименование не помогает.

    Повтор файла с вердиктом «не бланк» — это человек спорит с вердиктом: тогда файл идёт в
    распознавание мимо гейта (`force_lab`), как по кнопке «🧪 Анализы»."""
    path = Path(path)
    cleared = False
    for suf in _REFUSAL_SIDECARS:
        side = path.with_name(path.name + suf)
        if side.exists():
            side.unlink(missing_ok=True)
            cleared = True
    if path.name in set(_load_state().get("notlab", [])):
        force_lab(path)
        cleared = True
    st = _load_state()
    if str(path) in set(st.get("outcome_told", [])):
        # Итог по файлу скажется заново, когда разборщики закончат повтор (нить file-outcome).
        st["outcome_told"] = sorted(set(st["outcome_told"]) - {str(path)})
        _save_state(st)
    if cleared:
        log.info(f"повтор после отказа: {path.name} — снова в очереди")
    return cleared


def _lab_reading_admitted() -> bool:
    """Обе роли чтения анализов (два прохода) допущены у поставщика установки."""
    import hai_core
    try:
        hai_core.get_model("opus")
        hai_core.get_model("sonnet")
        return True
    except hai_core.ModelNotAdmitted:
        return False


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

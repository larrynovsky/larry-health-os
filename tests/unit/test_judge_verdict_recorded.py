"""Оракул: суждение судьи адресата хранится — и НЕ протекает в глаза человеку.

Судья адресата должен сохранять причину и при снятии кандидата,
и при создании задачи. Ротируемый лог не заменяет хранимый вердикт,
а одинаковая константа в reason теряет объяснение конкретного решения.

Следствие тихое и именно поэтому дорогое: «судья стал строже» и «разговоров
просто не было» снаружи неразличимы. Это §18 — утверждение «канал работает»
без гасителя.

ЧТО ЗДЕСЬ ГЛАВНОЕ. Не «данные сохраняются» (это половина), а что слой ДАННЫХ и
слой ПУБЛИКАЦИИ разведены. `tasks.reason` рендерится человеку в Telegram
курсивом под текстом задачи (`format_tasks_message`). Записать туда машинное
рассуждение значило бы показывать владельцу внутренности судьи под каждым вопросом.
Тест ниже краснеет именно на этой мутации, и она самая вероятная — «поле же
называется reason, зачем второе».

ГРАНИЦА ЧЕСТНО. Тест доказывает, что суждение ЗАПИСАНО и не показано. Что оно
ВЕРНО — не судится ничем и не может: судья это модель, а правильность адресата
решает человек, отвечая или отклоняя вопрос.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]


@pytest.fixture()
def стенд(tmp_path, monkeypatch):
    """Настоящая схема проекта в одноразовой БД — выдуманная руками уже
    пропускала недочиненные колонки (урок соседнего теста миграции)."""
    import health_db as hdb
    # Явно, а не в надежде на conftest. Тот ставит ALLOW_WRITE_NONPRIMARY через
    # `setdefault` на импорте, но в ПОЛНОМ прогоне переменная к этому файлу
    # приезжает снятой — и `get_conn` уходит в ветку read-only, где `init_db`
    # падает «unable to open database file» на ещё не существующем файле.
    # Поодиночке файл при этом зелёный: классическая утечка окружения между
    # тестами. Соседи (test_canon_names_backfill, test_build_specialized_context)
    # лечат это тем же способом — своим setenv, а не общим.
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    # ЦЕЛИКОМ `init_db`, а не две нужные миграции поимённо. Первая редакция звала
    # `_migrate_tasks` + `_ensure_memory_facts_table` и падала на `resolution_type`:
    # эта колонка живёт в СОСЕДНЕЙ миграции (`_migrate_consultations_and_resolution`),
    # и подобранный вручную набор — та же выдуманная схема, только из настоящих
    # кусков. Схему собирает одна точка входа; её и зовём.
    hdb.init_db()
    hdb._ensure_memory_facts_table()   # память заводится лениво, не из init_db
    return hdb


def _колонки(hdb, таблица: str) -> set[str]:
    with hdb.get_conn() as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({таблица})")}


# ── слой данных существует ───────────────────────────────────────────────────

def test_у_суждения_есть_дом(стенд):
    hdb = стенд
    assert {"judge_verdict", "judge_reason"} <= _колонки(hdb, "tasks"), (
        "у суждения судьи нет своих полей в tasks — оно снова будет теряться "
        "или, хуже, поедет в публикационный reason")
    assert "retire_reason" in _колонки(hdb, "memory_facts"), (
        "причина снятия кандидата негде хранить — вернулись к логу с ротацией")


def test_миграция_идемпотентна(стенд):
    """Вторая (и третья) миграция не роняет и не плодит колонок.

    Обе таблицы мигрируются при каждом старте, так что неидемпотентность
    означала бы падение бота на рестарте — а он рестартует на каждый коммит.
    """
    hdb = стенд
    до = (_колонки(hdb, "tasks"), _колонки(hdb, "memory_facts"))
    hdb.init_db(); hdb._ensure_memory_facts_table()
    hdb.init_db(); hdb._ensure_memory_facts_table()
    assert (_колонки(hdb, "tasks"), _колонки(hdb, "memory_facts")) == до


# ── запись: обе половины решения ─────────────────────────────────────────────

def test_причина_снятия_сохраняется_а_не_только_логируется(стенд):
    """Снятый кандидат сохраняет причину решения в хранилище."""
    import memory_facts_db as mf
    hdb = стенд
    with hdb.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, source) "
                  "VALUES ('question', 'как кофеин влияет на сон', 'conversation')")
        fid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    assert mf.retire_fact(fid, "судья: не пациенту — вопрос к науке")
    with hdb.get_conn() as c:
        active, причина = c.execute(
            "SELECT active, retire_reason FROM memory_facts WHERE id=?",
            (fid,)).fetchone()
    assert active == 0, "факт не снят — предмет теста не наступил"
    assert причина == "судья: не пациенту — вопрос к науке", (
        f"причина снятия не сохранена ({причина!r}): через неделю у этого "
        f"кандидата снова нельзя будет узнать, почему его похоронили")


def test_суждение_при_подъёме_сохраняется(стенд):
    """Вторая половина: почему вопрос ЗАДАЛИ. Её не было вовсе."""
    import tasks_db as td
    hdb = стенд
    tid = td.save_task(
        source="memory_question", type_="question", content="какая сейчас доза?",
        reason="вопрос накоплен ассистентом в разговоре",
        fingerprint="question:mem:101",
        judge_verdict="patient", judge_reason="дозу знает только сам пациент")
    assert tid
    with hdb.get_conn() as c:
        вердикт, причина = c.execute(
            "SELECT judge_verdict, judge_reason FROM tasks WHERE id=?",
            (tid,)).fetchone()
    assert вердикт == "patient", f"вердикт судьи не записан: {вердикт!r}"
    assert причина == "дозу знает только сам пациент"


# ── ГЛАВНОЕ: слои не склеены ─────────────────────────────────────────────────

def test_суждение_судьи_не_показывается_человеку(стенд):
    """САМЫЙ ВАЖНЫЙ ТЕСТ ФАЙЛА.

    `format_tasks_message` собирает то, что уедет в Telegram. Машинное
    обоснование в этот текст попасть не должно: человек получает вопрос, а не
    протокол размышлений модели о нём. Мутация «класть judge_reason в reason»
    краснеет здесь — и это ровно та мутация, которую сделает следующий, кто
    увидит два похожих поля и решит, что одно лишнее.
    """
    import tasks_db as td
    import task_agent as ta
    hdb = стенд
    td.save_task(
        source="memory_question", type_="question",
        content="согласовали ли дозу магния с врачом?",
        reason="вопрос накоплен ассистентом в разговоре",
        fingerprint="question:mem:102",
        judge_verdict="unsure",
        judge_reason="СЛУЖЕБНОЕ-СУЖДЕНИЕ-МОДЕЛИ")

    задачи = hdb.get_open_tasks(limit=10)
    текст = ta.format_tasks_message(задачи)

    assert "согласовали ли дозу магния" in текст, (
        "задача не попала в сообщение — тест судит пустоту")
    assert "СЛУЖЕБНОЕ-СУЖДЕНИЕ-МОДЕЛИ" not in текст, (
        f"обоснование судьи уехало человеку в Telegram — слой данных склеен со "
        f"слоем публикации:\n{текст}")
    assert "вопрос накоплен ассистентом" in текст, (
        "публикационная строка пропала — правка сломала то, что работало")


def test_публикация_и_данные_это_разные_поля(стенд):
    """Характеризация разведения: одно значение не подменяет другое."""
    import tasks_db as td
    hdb = стенд
    tid = td.save_task(
        source="memory_question", type_="question", content="вопрос",
        reason="ПУБЛИКАЦИЯ", judge_verdict="patient", judge_reason="ДАННЫЕ")
    with hdb.get_conn() as c:
        pub, данные = c.execute(
            "SELECT reason, judge_reason FROM tasks WHERE id=?", (tid,)).fetchone()
    assert (pub, данные) == ("ПУБЛИКАЦИЯ", "ДАННЫЕ"), (
        f"поля подменяют друг друга: reason={pub!r}, judge_reason={данные!r}")


def test_задача_без_судьи_пишется_как_прежде(стенд):
    """Обратная совместимость: у клинических задач судьи нет, и это не ошибка.

    Без этого теста fail-closed мог бы уехать не туда — например, в требование
    вердикта у всех задач подряд, а его выносят только вопросам.
    """
    import tasks_db as td
    hdb = стенд
    tid = td.save_task(source="gp_report", type_="action",
                       content="сдать ферритин", reason="снижен в динамике")
    assert tid
    with hdb.get_conn() as c:
        вердикт, причина = c.execute(
            "SELECT judge_verdict, judge_reason FROM tasks WHERE id=?",
            (tid,)).fetchone()
    assert вердикт is None and причина is None


def test_ретайр_на_свежей_базе_не_падает(tmp_path, monkeypatch):
    """`retire_fact` сам обеспечивает схему — как его соседи по модулю.

    ПОЧЕМУ ОТДЕЛЬНАЯ ФИКСТУРА, А НЕ ОБЩАЯ. Общая заводит `memory_facts` явно, и
    на ней пропажа `_ensure_memory_facts_table()` внутри `retire_fact` не видна
    вовсе — свёртка 15.09 это показала: мутация «убрать ensure» пережила все
    шесть остальных тестов. Здесь база СВЕЖАЯ, как у человека, который поставил
    систему сегодня: таблицы памяти ещё нет, и первый же ретайр обязан работать,
    а не падать «no such column retire_reason».

    Класс не выдуманный: `retire_fact` была единственной публичной функцией
    своего модуля без вызова ensure, и до 15.09 это не стреляло только потому,
    что она писала лишь в `active` — колонку из исходного CREATE TABLE.
    """
    import health_db as hdb
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")   # см. фикстуру `стенд`
    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "fresh.db")
    hdb.init_db()                      # памяти НЕ заводим — она ленивая
    import memory_facts_db as mf

    with hdb.get_conn() as c:
        есть = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_facts'"
        ).fetchone()
    assert есть is None, (
        "memory_facts уже есть на свежей базе — обстановка не воспроизводит "
        "случай, ради которого тест написан")

    # Первым делом зовём ИМЕННО ретайр — ни один другой вход схему не трогал.
    # Снимать нечего, поэтому ждём честное False, а НЕ исключение: разница между
    # «нечего снимать» и «модуль сломан» и есть предмет.
    try:
        снято = mf.retire_fact(1, "судья: не пациенту — к науке")
    except Exception as e:                      # noqa: BLE001 — предмет теста
        pytest.fail(
            f"ретайр упал на свежей базе ({type(e).__name__}: {e}). Он пишет "
            f"`retire_reason`, а эту колонку заводит только "
            f"`_ensure_memory_facts_table` — значит вызов ensure потерян.")
    assert снято is False, "снимать было нечего, а функция сказала, что сняла"

    # И схема после этого есть — ретайр её обеспечил сам.
    with hdb.get_conn() as c:
        колонки = {r[1] for r in c.execute("PRAGMA table_info(memory_facts)")}
    assert "retire_reason" in колонки, (
        "после ретайра таблицы памяти нет или она без колонки причины")

    # Теперь по-настоящему: факт есть — причина сохраняется.
    with hdb.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, source) "
                  "VALUES ('question','какая доза?','conversation')")
        fid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    assert mf.retire_fact(int(fid), "судья: не пациенту — к науке")
    with hdb.get_conn() as c:
        причина = c.execute("SELECT retire_reason FROM memory_facts WHERE id=?",
                            (fid,)).fetchone()[0]
    assert причина == "судья: не пациенту — к науке"


def test_подъём_из_памяти_довозит_суждение_до_базы(стенд, monkeypatch):
    """ПРОВОДКА, а не только приёмник.

    Свёртка 15.09 показала дыру: все тесты выше зовут `save_task` НАПРЯМУЮ, и
    мутация «убрать judge_verdict/judge_reason из вызова в
    `promote_memory_questions`» пережила их все. То есть поля были, приёмник
    работал, а судья до них не доезжал — ровно тот класс, что «файл доставки
    жив, но его никто не зовёт».

    Судья здесь подставной: предмет теста — проводка от вердикта до строки в
    базе, а не качество модели.
    """
    import task_agent as ta
    import memory_facts_db as mf
    import config_db as cfg
    hdb = стенд

    with hdb.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, source) "
                  "VALUES ('question','какая сейчас доза магния?','conversation')")
        fid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    monkeypatch.setattr(cfg, "get_config", lambda ключ, default=None: {
        "questions.memory_window_days": 45,
        "questions.promote_batch": 5,
        "questions.max_per_day": 20,
    }.get(ключ, default))
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        str(fid): {"verdict": "patient", "reason": "ПРИЧИНА-СУДЬИ",
                   "ru": "какая сейчас доза магния?"}})

    созданы = ta.promote_memory_questions()
    assert len(созданы) == 1, f"вопрос не поднят — проводка не проверена: {созданы}"

    with hdb.get_conn() as c:
        вердикт, причина, публикация = c.execute(
            "SELECT judge_verdict, judge_reason, reason FROM tasks WHERE id=?",
            (созданы[0]["id"],)).fetchone()
    assert вердикт == "patient", f"вердикт не доехал до базы: {вердикт!r}"
    assert причина == "ПРИЧИНА-СУДЬИ", f"причина судьи не доехала: {причина!r}"
    assert публикация != "ПРИЧИНА-СУДЬИ", (
        "причина судьи заняла публикационное поле — человек увидит её в Telegram")


def test_отказ_судьи_довозит_причину_до_ретайра(стенд, monkeypatch):
    """Вторая половина проводки: там, где судья сказал «нет».

    Без неё след оставался бы только у согласия, и «судья стал строже» опять
    было бы неотличимо от «кандидатов не приходило» — то есть долг
    BL-JUDGE-VERDICT-UNRECORDED-1 закрылся бы наполовину.
    """
    import task_agent as ta
    import config_db as cfg
    hdb = стенд

    with hdb.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, source) "
                  "VALUES ('question','как кофеин влияет на сон?','conversation')")
        fid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    monkeypatch.setattr(cfg, "get_config", lambda ключ, default=None: {
        "questions.memory_window_days": 45,
        "questions.promote_batch": 5,
        "questions.max_per_day": 20,
    }.get(ключ, default))
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        str(fid): {"verdict": "not_patient", "reason": "это вопрос к науке",
                   "ru": ""}})

    созданы = ta.promote_memory_questions()
    assert созданы == [], "не-пациенту всё равно подняли в задачи"

    with hdb.get_conn() as c:
        active, причина = c.execute(
            "SELECT active, retire_reason FROM memory_facts WHERE id=?",
            (fid,)).fetchone()
    assert active == 0, "кандидат не снят — предмет теста не наступил"
    assert причина and "это вопрос к науке" in причина, (
        f"причина отказа судьи не доехала до базы: {причина!r}")

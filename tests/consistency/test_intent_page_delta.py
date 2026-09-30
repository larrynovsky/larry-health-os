"""tests/consistency/test_intent_page_delta.py — дельта записи + чанкер доставки.

entry_delta (в intent_registry, чистая) — источник истины раздела «что поменялось»
тёплой страницы (hybrid: факт из реестра, LLM только переводит). _tg_chunks (в
doc_agent) режет тёплый текст под лимит Telegram при доставке в бот.
RST: позитивный контроль на каждую (умеет вернуть непустое/красное на сломанном).
"""
from __future__ import annotations

import os

import pytest

import intent_registry as ir

# doc_agent на импорте зовёт secrets_dir(); bot.filters (импортируется позже другими
# тестами при ПОЛНОМ сборе) fail-closes на telegram_chat_id при импорте. Правило:
# если резолвнутый secrets_dir УЖЕ содержит telegram_chat_id (канон Studio) — НЕ трогаем
# окружение, иначе owner_env-тесты видят фиктивный chat_id вместо реального (инцидент
# 2026-07-15, 2-я волна). Стаб — ТОЛЬКО когда реальных секретов нет (staging/tenant):
# полный (chat_id/token), иначе pytest tests/ всё-разом падает на сборе bot.filters.
# setdefault утёк бы на процесс — но в staging реальных секретов нет, утечка безвредна,
# а owner_env там deselected.
from secrets_paths import secrets_dir as _sd  # noqa: E402
if not (_sd() / "telegram_chat_id").exists():
    _stub = os.path.join(os.environ.get("TMPDIR", "/tmp"), "health_test_secrets_noread")
    os.makedirs(_stub, exist_ok=True)
    for _f, _v in (("telegram_chat_id", "0"), ("telegram_token", "TEST:TOKEN")):
        _fp = os.path.join(_stub, _f)
        if not os.path.exists(_fp):
            open(_fp, "w").write(_v)
    os.environ["HEALTH_SECRETS_DIR"] = _stub
import doc_agent as da  # noqa: E402

pytestmark = pytest.mark.consistency


def _e(eid, invs, intent="и"):
    return {"id": eid, "intent": intent,
            "invariants": [{"id": i, "claim": c, "status": s} for i, c, s in invs]}


# ── дельта записи реестра (intent_registry.entry_delta) ──
def test_delta_new_subsystem():
    assert ir.entry_delta(None, _e("x", [])) == ["новая подсистема в реестре"]


def test_delta_status_flip():
    assert ir.entry_delta(_e("x", [("a", "c", "open")]),
                          _e("x", [("a", "c", "holds")])) == ["a: статус open→holds"]


def test_delta_claim_edit():
    # 2026-07-28: факт расширен запретом догадки о направлении (см.
    # test_pravka_claim_zapreshchaet_dogadku_o_napravlenii). Прежний ассерт на точную
    # строку «a: правка claim» ЗАМОРАЖИВАЛ голый факт как норму — а именно голого факта
    # генератору и не хватило, чтобы не перевернуть смысл. Проверяем предмет, не формулировку.
    d = ir.entry_delta(_e("x", [("a", "старый", "holds")]),
                       _e("x", [("a", "новый", "holds")]))
    assert len(d) == 1 and d[0].startswith("a: правка claim"), d


def test_delta_added_and_removed():
    d = ir.entry_delta(_e("x", [("a", "c", "holds")]), _e("x", [("b", "c", "open")]))
    assert "+ инвариант b (status=open)" in d
    assert "− инвариант a удалён" in d


def test_delta_intent_edit():
    assert "правка замысла (intent)" in ir.entry_delta(
        _e("x", [], intent="было"), _e("x", [], intent="стало"))


def test_delta_no_change_is_honest():
    # working==HEAD: НЕ выдумывает изменение, говорит правду
    e = _e("x", [("a", "c", "holds")])
    d = ir.entry_delta(e, e)
    assert len(d) == 1 and "не менялась" in d[0]


# ── чанкер доставки (doc_agent._tg_chunks): позитивные контроли ──
def test_chunks_short_single():
    assert da._tg_chunks("коротко") == ["коротко"]


def test_chunks_empty():
    assert da._tg_chunks("") == []


def test_chunks_splits_over_limit_no_overflow():
    big = "\n".join(f"строка {i}" for i in range(2000))
    ch = da._tg_chunks(big, limit=500)
    assert len(ch) > 1                       # реально порезал
    assert all(len(c) <= 500 for c in ch)    # ни один кусок не превышает лимит
    assert "строка 0" in ch[0] and "строка 1999" in ch[-1]  # без потери краёв


def test_chunks_hard_splits_giant_line():
    ch = da._tg_chunks("x" * 1200, limit=500)
    assert ch and all(len(c) <= 500 for c in ch)
    assert "".join(ch) == "x" * 1200         # склеивается обратно без потерь


# ── вставка раздела «Что изменилось» после заголовка (механизм конституций) ──
def test_insert_section_after_title():
    body = "# Заголовок: подсистема\n\n## Зачем он есть\n\nтекст"
    out = da._insert_change_section(body, "## Что изменилось\n\nСмысла не менялось.")
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("# Заголовок")           # заголовок остался первым
    assert lines[1] == "## Что изменилось"              # раздел сразу после него
    assert out.index("## Что изменилось") < out.index("## Зачем он есть")


def test_insert_section_no_title_prepends():
    out = da._insert_change_section("текст без заголовка", "## Что изменилось\n\nX")
    assert out.startswith("\n## Что изменилось")         # нет '# ' → вставка сверху


# ── Границы доказанного (`limits`) — правка 2026-07-28 ────────────────────────────────
# Класс дефекта, найденный исполнением: раздел «пределы» тёплой страницы кормился ТОЛЬКО
# non-holds инвариантами. Значит подъём статуса до `holds` ФИЗИЧЕСКИ удалял оговорки из
# того, что видит читатель: доказан один сценарий из трёх — страница сообщала «обещание
# надёжно выполнено». Оговорка в комментарии yaml генератору не видна, оговорка внутри
# claim была пересказана им как УСИЛЕНИЕ. Ниже — сторожа на все четыре шва починки.

def _e_lim(status="holds", limits=("гонка писателей не покрыта",)):
    e = _e("x", [("inv1", "обещание", status)])
    e["invariants"][0]["limits"] = list(limits)
    return e


def test_limited_invariant_viden_nezavisimo_ot_statusa():
    """⭐ Ядро починки: `holds` с границей ОБЯЗАН попадать в список ограниченных."""
    assert [i["id"] for i in ir.limited_invariants(_e_lim())] == ["inv1"]
    assert ir.limits_of(_e_lim()["invariants"][0]) == ["гонка писателей не покрыта"]
    # позитивный контроль: без поля — пусто, иначе тест зелен при любом коде
    assert ir.limited_invariants(_e("x", [("inv1", "обещание", "holds")])) == []


def test_limits_menyayut_provenance_no_tolko_u_svoej_zapisi():
    """Границы — смысловое поле: их правка ОБЯЗАНА объявлять страницу устаревшей.

    И одновременно: запись БЕЗ границ обязана сохранить прежний хэш побайтово. Иначе
    введение поля объявило бы устаревшими страницы всех 17 подсистем разом — сторож,
    который шумит на всех, перестают читать."""
    bare = _e("x", [("inv1", "обещание", "holds")])
    assert ir.entry_provenance(_e_lim()) != ir.entry_provenance(bare), \
        "правка границ не двигает провенанс → страница молча разойдётся с реестром"
    assert ir.entry_provenance(_e_lim(limits=("A",))) != ir.entry_provenance(_e_lim(limits=("B",)))
    # ключ отсутствует, а не пуст: пустой список тоже не должен менять хэш
    empty = _e("x", [("inv1", "обещание", "holds")])
    empty["invariants"][0]["limits"] = []
    assert ir.entry_provenance(empty) == ir.entry_provenance(bare), \
        "пустые границы сдвинули хэш → массовая ложная устарелость"


def test_delta_nazyvaet_granicu_granicej_a_ne_faktom_pravki():
    """Дельта обязана нести НАПРАВЛЕНИЕ: добавленная граница — ограничение, не достижение."""
    facts = ir.entry_delta(_e("x", [("inv1", "обещание", "open")]), _e_lim())
    assert any("статус open→holds" in f for f in facts)
    assert any("ГРАНИЦА доказанного" in f and "гонка писателей" in f for f in facts), \
        f"добавленная граница не попала в якорь дельты: {facts}"
    # снятие границы читается как расширение доказанного, а не как потеря
    back = ir.entry_delta(_e_lim(), _e("x", [("inv1", "обещание", "holds")]))
    assert any("снята граница" in f and "доказано шире" in f for f in back), back


def test_pravka_claim_zapreshchaet_dogadku_o_napravlenii():
    """Прецедент 2026-07-28: по голому «правка claim» генератор пересказал ДОБАВЛЕННОЕ
    ограничение как заверение. Направление машинно не выводится — факт обязан это сказать."""
    facts = ir.entry_delta(_e("x", [("inv1", "было", "holds")]),
                           _e("x", [("inv1", "стало", "holds")]))
    claim_fact = next(f for f in facts if "правка claim" in f)
    assert "направление НЕ определено машинно" in claim_fact, claim_fact
    assert "НЕ как усиление" in claim_fact, claim_fact


def test_otmyvanie_lovitsya_i_u_holds_s_granicej():
    """`holds` с границей отмывается так же: «работает» вместо «работает в пределах»."""
    e = _e_lim()
    e["invariants"][0]["warm_guard"] = {"absent": ["покрывает все случаи"]}
    assert ir.status_laundering(e, "Гейт покрывает все случаи.") == ["inv1: «покрывает все случаи»"]
    assert ir.status_laundering(e, "Гейт держится в названных пределах.") == []


def test_promt_stranicy_nesyot_granicy_v_razdel_predelov():
    """Шов писателя: границы обязаны доехать до промпта, иначе поле есть, а эффекта нет."""
    e = _e_lim()
    e["code_anchors"] = ["mod.py::f"]
    e["title"] = "Подсистема"
    prompt = da._warm_page_prompt(e)
    assert "Границы доказанного" in prompt
    assert "гонка писателей не покрыта" in prompt
    assert "РАЗНЫЕ" in prompt, "запрет подменять «держится» на «проверено везде» пропал"

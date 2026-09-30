"""Контроли ветки приёма меддокументов в роутере фото (handlers/symptom).

Стерегут три вещи, каждая из которых уже ломалась или могла сломаться молча:
  1. off по умолчанию — новая ветка не включается сама;
  2. shadow НЕ меняет маршрут — иначе «наблюдение» было бы боем под другим именем;
  3. решение принимается ДО проверки подписи — ровно тот порядок, отсутствие
     которого 2026-07-28 увело семь документов в свободный текст.
"""
import asyncio
import io

import pytest
from PIL import Image

import doc_triage
import handlers.symptom as sym


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (70, 70, 70)).save(buf, format="PNG")
    return buf.getvalue()


class _Msg:
    def __init__(self, caption=""):
        self.caption, self.replies = caption, []
        self.photo = ["stub"]

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def send_action(self, *a, **kw):
        pass

    @property
    def chat(self):
        return self


class _Update:
    def __init__(self, caption=""):
        self.message = _Msg(caption)
        self.effective_chat = type("C", (), {"id": 42})()


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Фото скачано, делегирование в generic — заглушено, intake пишет в tmp."""
    calls = {"generic": 0, "intake": []}

    async def _bytes(update, context):
        return _png()

    async def _generic(update, context):
        calls["generic"] += 1

    monkeypatch.setattr(sym, "_photo_bytes", _bytes)
    monkeypatch.setattr(sym, "_delegate_to_generic_photo", _generic)

    import doc_intake
    real = doc_intake.intake

    def _spy(data, media_type="image/jpeg", base=None, store=True, client=None):
        r = real(data, media_type, str(tmp_path), store, client=calls["client"])
        calls["intake"].append(r)
        return r

    monkeypatch.setattr(doc_intake, "intake", _spy)
    calls["tmp"] = tmp_path
    return calls


class _Block:
    def __init__(self, t):
        self.type, self.text = "text", t


class _Client:
    def __init__(self, answer):
        answer = f"{answer} other" if " " not in answer else answer
        outer = self

        class _M:
            def create(self, **kw):
                return type("R", (), {"content": [_Block(outer.answer)]})()
        self.answer, self.messages = answer, _M()


@pytest.fixture(autouse=True)
def _stub_model(monkeypatch):
    import hai_core
    monkeypatch.setattr(hai_core, "get_model", lambda role: f"model-{role}")


def _run(update, mode, wired, answer=doc_triage.LAB):
    wired["client"] = _Client(answer)
    import handlers.symptom as s
    orig = s._intake_mode
    s._intake_mode = lambda: mode
    try:
        # asyncio.run, а не get_event_loop(): в общем прогоне соседние тесты
        # оставляют закрытый цикл, и контроль краснел бы не по своей причине.
        return asyncio.run(s._maybe_intake_document(update, None, update.message.caption))
    finally:
        s._intake_mode = orig


def test_mode_off_does_not_touch_intake(wired):
    u = _Update()
    assert _run(u, "off", wired) is False
    assert wired["intake"] == []                      # классификатор даже не звался
    assert not (wired["tmp"] / "incoming").exists()


def test_shadow_classifies_but_does_not_change_route(wired):
    u = _Update()
    assert _run(u, "shadow", wired) is False          # маршрут прежний
    assert len(wired["intake"]) == 1                  # но решение принято
    assert wired["intake"][0]["label"] == doc_triage.LAB
    assert wired["intake"][0]["stored"] is False      # и на диск ничего не легло
    assert not (wired["tmp"] / "incoming").exists()


def test_on_lab_without_caption_is_taken(wired):
    """Кадр без подписи — ровно тот случай, что утёк 2026-07-28."""
    u = _Update(caption="")
    assert _run(u, "on", wired) is True
    assert wired["intake"][0]["stored"] is True
    assert "анализ" in u.message.replies[-1].lower()
    assert wired["generic"] == 1                      # разбор в чат остался мгновенным


def test_on_personal_falls_through_to_normal_path(wired):
    u = _Update(caption="вот царапина на колене")
    assert _run(u, "on", wired, answer=doc_triage.PERSONAL) is False
    assert wired["intake"][0]["stored"] is False
    assert not (wired["tmp"] / "incoming").exists()   # фото тела в инбокс не попало


def test_on_report_gets_honest_receipt(wired):
    u = _Update()
    assert _run(u, "on", wired, answer=doc_triage.REPORT) is True
    assert "пока нет" in u.message.replies[-1]        # не обещаем разбор, которого нет


def test_duplicate_frame_not_resubmitted(wired):
    u1, u2 = _Update(), _Update()
    _run(u1, "on", wired)
    assert _run(u2, "on", wired) is True
    assert "уже получал" in u2.message.replies[-1]


def test_unknown_mode_is_off(wired):
    assert _run(_Update(), "включено", wired) is False
    assert wired["intake"] == []


# ── Приоритет жалобы над классификацией кадра (решение владельца 2026-07-28) ───

def _entry(update, monkeypatch, *, symptom: bool, intake_calls: list):
    """Прогон on_photo_entry с заглушенным симптом-путём: нас интересует ПОРЯДОК,
    а не элиситация. Обрыв на media_intake — законный ранний выход уже ПОСЛЕ того,
    как порядок решён."""
    import handlers.symptom as s
    import media_intake
    import visual_db

    async def _fake_intake(u, c, cap):
        intake_calls.append(cap)
        return True                      # если дошли — документ «принят»

    async def _bytes(u, c):
        return _png()

    async def _generic(u, c):
        pass

    monkeypatch.setattr(s, "_classify_symptom", lambda cap: symptom)
    monkeypatch.setattr(s, "_maybe_intake_document", _fake_intake)
    monkeypatch.setattr(s, "_photo_bytes", _bytes)
    monkeypatch.setattr(s, "_delegate_to_generic_photo", _generic)
    monkeypatch.setattr(visual_db, "get_awaiting_followup_case", lambda cid: None)
    monkeypatch.setattr(media_intake, "sanitize_and_store",
                        lambda *a, **k: (_ for _ in ()).throw(media_intake.ImageRejected("stop")))
    return asyncio.run(s.on_photo_entry(update, None))


def test_symptom_caption_beats_frame_classification(monkeypatch):
    """Человек написал жалобу на тело — приём документов не должен даже запускаться.
    Иначе ошибка классификации ответила бы «сохранил заключение» на «колено не проходит»."""
    calls = []
    u = _Update(caption="колено не проходит, отёк спускается к голени")
    _entry(u, monkeypatch, symptom=True, intake_calls=calls)
    assert calls == []                                  # ветка приёма не тронута


def test_non_symptom_caption_lets_intake_decide(monkeypatch):
    calls = []
    u = _Update(caption="вот мои свежие анализы")
    _entry(u, monkeypatch, symptom=False, intake_calls=calls)
    assert calls == ["вот мои свежие анализы"]


def test_no_caption_lets_intake_decide(monkeypatch):
    """Кадр без подписи — тот самый случай, что утёк 2026-07-28."""
    calls = []
    _entry(_Update(caption=""), monkeypatch, symptom=False, intake_calls=calls)
    assert calls == [""]

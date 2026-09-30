"""Контроли doc_triage: класс документа на картинке + безопасная сторона.

Главный инвариант — не «модель угадала» (это TEST с человеком-оракулом, машине
недоступен), а «ЛЮБОЙ сбой суждения виден и разрешается в lab_table». Поэтому
контроли инжектят фейковый vision-клиент и проверяют механику, а не качество.

Отдельно стережём `fallback`: без него «модель сказала lab_table» неотличимо от
«модель молчала», и shadow-режим перестаёт быть замером.
"""
import io

import pytest

import doc_triage


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, answer, boom=False):
        self._answer, self._boom, self.calls = answer, boom, []

    def create(self, **kw):
        self.calls.append(kw)
        if self._boom:
            raise RuntimeError("vision недоступен")
        return _Resp(self._answer)


class _Client:
    def __init__(self, answer="lab_table", boom=False):
        self.messages = _Messages(answer, boom)


def _png(w=10, h=10) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _stub_model(monkeypatch):
    """get_model ходит в БД тенанта — в юните её нет и не надо."""
    import hai_core
    monkeypatch.setattr(hai_core, "get_model", lambda role: f"model-{role}")


# --- позит: каждая метка доезжает как есть -------------------------------------

@pytest.mark.parametrize("label", doc_triage.labels())
def test_each_label_round_trips(label):
    r = doc_triage.classify_image(_png(), "image/png", client=_Client(f"{label} other"))
    assert r["label"] == label
    assert r["fallback"] is False
    assert r["reason"] == ""


def test_answer_with_noise_still_matched():
    """Модель добавила пояснение — метку всё равно достаём."""
    r = doc_triage.classify_image(_png(), "image/png", client=_Client("Это personal other, фото кожи"))
    assert r["label"] == doc_triage.PERSONAL
    assert r["fallback"] is False


# --- безопасная сторона: четыре входа в один и тот же ответ --------------------

def test_empty_input_is_safe_side():
    r = doc_triage.classify_image(b"", "image/png", client=_Client("personal other"))
    assert r["label"] == doc_triage.SAFE_SIDE and r["fallback"] is True
    assert "пуст" in r["reason"]


def test_unknown_media_type_is_safe_side():
    r = doc_triage.classify_image(_png(), "application/pdf", client=_Client("personal other"))
    assert r["label"] == doc_triage.SAFE_SIDE and r["fallback"] is True


def test_vision_failure_is_safe_side_not_raise():
    r = doc_triage.classify_image(_png(), "image/png", client=_Client(boom=True))
    assert r["label"] == doc_triage.SAFE_SIDE and r["fallback"] is True
    assert "сбой vision" in r["reason"]


def test_answer_outside_set_is_safe_side():
    r = doc_triage.classify_image(_png(), "image/png", client=_Client("не знаю"))
    assert r["label"] == doc_triage.SAFE_SIDE and r["fallback"] is True
    assert r["raw"] == "не знаю"          # сырой ответ сохранён для разбора
    assert "вне множества" in r["reason"]


def test_safe_side_is_lab():
    """Не косметика: безопасная сторона обязана быть единственной веткой,
    у которой есть распознаватель И сигнал на промах."""
    assert doc_triage.SAFE_SIDE == doc_triage.LAB


# --- различимость сбоя и настоящего ответа (ради shadow-замера) ----------------

def test_real_lab_answer_distinguishable_from_fallback():
    real = doc_triage.classify_image(_png(), "image/png", client=_Client("lab laboratory"))
    fell = doc_triage.classify_image(_png(), "image/png", client=_Client(boom=True))
    assert real["label"] == fell["label"] == doc_triage.LAB
    assert real["fallback"] is not fell["fallback"]


# --- механика запроса ----------------------------------------------------------

def test_large_image_downscaled_before_send():
    c = _Client("lab laboratory")
    doc_triage.classify_image(_png(3000, 2000), "image/png", client=c)
    sent = c.messages.calls[0]["messages"][0]["content"][0]["source"]["data"]
    import base64
    from PIL import Image
    im = Image.open(io.BytesIO(base64.standard_b64decode(sent)))
    assert max(im.size) <= doc_triage._MAX_EDGE


def test_labels_are_closed_set():
    assert set(doc_triage.labels()) == {
        doc_triage.LAB, doc_triage.REPORT, doc_triage.PERSONAL}


def test_modality_parsed_but_does_not_pick_route():
    """Модальность едет атрибутом: разные модальности при одной форме — один маршрут."""
    a = doc_triage.classify_image(_png(), "image/png", client=_Client("report imaging"))
    b = doc_triage.classify_image(_png(), "image/png", client=_Client("report clinical"))
    assert a["label"] == b["label"] == doc_triage.REPORT
    assert a["modality"] == "imaging" and b["modality"] == "clinical"


def test_unknown_modality_falls_back_but_form_survives():
    r = doc_triage.classify_image(_png(), "image/png", client=_Client("report чтототакое"))
    assert r["label"] == doc_triage.REPORT and r["fallback"] is False
    assert r["modality"] == doc_triage.UNKNOWN_MODALITY


def test_body_is_never_a_document_in_prompt():
    """Правило, найденное владельцем до внедрения: «медматериал без таблицы → report»
    без оговорки про тело увело бы фото кожи в reports/ и отключило симптом-интейк."""
    assert "Тело человека — ВСЕГДА personal" in doc_triage._PROMPT

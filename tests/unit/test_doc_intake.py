"""Контроли doc_intake: класс → каталог, и чего на диск попасть НЕ должно.

Негативные контроли тут важнее позитивных. Позитивный ловит «не положили куда
надо» — это заметно сразу. Негативный ловит «положили куда не надо»: фото тела
в инбоксе меддокументов не сломает ничего видимого, просто окажется не там,
где его ждут, и узнают об этом не скоро.
"""
import io
from pathlib import Path

import pytest
from PIL import Image

import doc_intake
import doc_triage


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, answer, boom=False):
        self._answer, self._boom = answer, boom

    def create(self, **kw):
        if self._boom:
            raise RuntimeError("vision недоступен")
        return _Resp(self._answer)


class _Client:
    def __init__(self, answer=doc_triage.LAB, boom=False):
        answer = f"{answer} other" if " " not in answer else answer
        self.messages = _Messages(answer, boom)


def _png(w=8, h=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (60, 60, 60)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _stub_model(monkeypatch):
    import hai_core
    monkeypatch.setattr(hai_core, "get_model", lambda role: f"model-{role}")


# --- карта маршрутов -----------------------------------------------------------

def test_lab_goes_to_inbox_root(tmp_path):
    """Корень инбокса — единственное место, куда смотрит lab_intake_watcher."""
    assert doc_intake.destination(doc_triage.LAB, str(tmp_path)) == tmp_path / "incoming"


def test_report_goes_to_own_subdir(tmp_path):
    assert doc_intake.destination(doc_triage.REPORT, str(tmp_path)) \
        == tmp_path / "incoming" / "reports"


def test_personal_has_no_destination(tmp_path):
    assert doc_intake.destination(doc_triage.PERSONAL, str(tmp_path)) is None


def test_every_label_has_a_route():
    """Новая метка без маршрута молча уехала бы в корень инбокса к распознавателю."""
    for lab in doc_triage.labels():
        assert lab in doc_intake._SUBDIR


# --- что кладётся и что не кладётся --------------------------------------------

def test_lab_photo_stored_in_root(tmp_path):
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path),
                          client=_Client(doc_triage.LAB))
    assert r["stored"] is True
    assert (tmp_path / "incoming").samefile(r["path"].rsplit("/", 1)[0])


def test_report_not_in_watcher_root(tmp_path):
    """Проза в корне инбокса дала бы табличному распознавателю не то."""
    doc_intake.intake(_png(), "image/png", base=str(tmp_path),
                      client=_Client(doc_triage.REPORT))
    root = tmp_path / "incoming"
    assert list((root / "reports").glob("*")) != []
    assert [p for p in root.iterdir() if p.is_file()] == []


def test_modality_does_not_change_route(tmp_path):
    """Заключение радиолога и выписка терапевта — один маршрут, разный атрибут.
    Именно этой развилки не было в первой редакции, и она дала промах с рентгеном."""
    a = doc_intake.intake(_png(3, 3), "image/png", base=str(tmp_path),
                          client=_Client("report imaging"))
    b = doc_intake.intake(_png(4, 4), "image/png", base=str(tmp_path),
                          client=_Client("report clinical"))
    assert a["path"].rsplit("/", 1)[0] == b["path"].rsplit("/", 1)[0]
    assert a["modality"] == "imaging" and b["modality"] == "clinical"


def test_sidecar_written_next_to_file(tmp_path):
    """Без сайдкара связка «кадр → метка» существует только как сопоставление
    по времени в логе, то есть сверять решения нечем."""
    import json
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path),
                          client=_Client("report endoscopy"))
    side = Path(r["path"] + doc_intake.SIDECAR_SUFFIX)
    assert side.exists()
    d = json.loads(side.read_text())
    assert d["form"] == doc_triage.REPORT and d["modality"] == "endoscopy"
    assert d["fallback"] is False and d["classifier_version"] == doc_triage.VERSION


def test_sidecar_failure_does_not_lose_the_document(tmp_path, monkeypatch):
    """Потеря атрибута хуже, но не смертельна — в отличие от потери документа."""
    monkeypatch.setattr(doc_intake, "_write_sidecar",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("нет места")))
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path), client=_Client(doc_triage.LAB))
    assert r["stored"] is True and r["error"] == ""            # приём НЕ провален
    assert list((tmp_path / "incoming").glob("*.png")) != []   # файл на диске остался


def test_personal_never_touches_disk(tmp_path):
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path),
                          client=_Client(doc_triage.PERSONAL))
    assert r["stored"] is False and r["path"] is None
    assert not (tmp_path / "incoming").exists()


def test_vision_failure_lands_in_lab_root(tmp_path):
    """Безопасная сторона доезжает до маршрута, а не теряется по дороге."""
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path), client=_Client(boom=True))
    assert r["label"] == doc_triage.LAB and r["fallback"] is True and r["stored"] is True


# --- режим наблюдения ----------------------------------------------------------

def test_observation_mode_classifies_but_writes_nothing(tmp_path):
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path), store=False,
                          client=_Client(doc_triage.LAB))
    assert r["label"] == doc_triage.LAB
    assert r["stored"] is False
    assert not (tmp_path / "incoming").exists()


# --- отказы --------------------------------------------------------------------

def test_storage_failure_does_not_raise(tmp_path, monkeypatch):
    import media_intake
    monkeypatch.setattr(media_intake, "sanitize_and_store",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("диск полон")))
    r = doc_intake.intake(_png(), "image/png", base=str(tmp_path), client=_Client(doc_triage.LAB))
    assert r["stored"] is False and "диск полон" in r["error"]


def test_duplicate_reported(tmp_path):
    data = _png()
    doc_intake.intake(data, "image/png", base=str(tmp_path), client=_Client(doc_triage.LAB))
    second = doc_intake.intake(data, "image/png", base=str(tmp_path), client=_Client(doc_triage.LAB))
    assert second["is_duplicate"] is True

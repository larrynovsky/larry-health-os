"""Производитель находок «нить застряла» — заказ BL-STALLED-THREADS-1.

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ. Что молчание нити становится ИМЕНОВАННОЙ находкой ровно один
раз, что письмо говорит о перемене (новые — поимённо, старые — числом), что уходит оно
только в свой день недели и что провал доставки не проглатывается.

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что нить действительно стоит: git молчит и о нити, которую ведут
в дереве без коммитов. Поэтому находка — ВОПРОС владельцу, а не вердикт, и оракула на
«правда ли стоит» здесь нет и быть не может.

weekly_digest.thread_last_activity подменяется: предмет теста — маршрут находки, а не
разбор git-истории (у него свой дом и свои тесты).
"""
import datetime as _dt
import json

import pytest

# Без маркера ночной слой (`pytest tests/unit/ -m unit`) НЕ берёт файл — замер
# 14.09: 1904 теста из 4267 отсеиваются так каждую ночь. Оракул, зелёный только
# при прямом вызове по пути, ночью не существует (§20 наизнанку).
pytestmark = pytest.mark.unit

import night_cycle as nc
import parked_decisions as pd

TODAY = _dt.date(2026, 9, 14)          # понедельник — день письма
TUESDAY = _dt.date(2026, 9, 15)


INDEX = """# Handoff INDEX

| Нить (slug) | Название | Последний Handoff ID | Статус |
|---|---|---|---|
| `stale-one` | что-то | `stale-one@x` | paused |
| `stale-two` | что-то | `stale-two@x` | closed |
| `moving` | что-то | `moving@x` | paused 13.09 |
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    index_md = tmp_path / "INDEX.md"
    index_md.write_text(INDEX, encoding="utf-8")
    monkeypatch.setenv("HEALTH_HANDOFF_INDEX", str(index_md))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    yield tmp_path


def test_closed_thread_is_not_a_finding(env, monkeypatch):
    """Замер 14.09: каталогов нитей 41, молчат 30 — но 26 из них закрыты. Без этого
    фильтра первое же письмо принесло бы тридцать карточек и убило канал."""
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01", "stale-two": "2026-06-01"}))
    _patch_mail(monkeypatch)
    out = nc._stalled_threads(TODAY)
    assert out["stalled_new"] == 1
    store = json.loads((env / "parked.json").read_text(encoding="utf-8"))
    assert "thread-stalled:stale-two" not in store


def _last(**pairs):
    return {k: _dt.date.fromisoformat(v) for k, v in pairs.items()}


def _patch_activity(monkeypatch, mapping):
    import weekly_digest
    monkeypatch.setattr(weekly_digest, "thread_last_activity",
                        lambda as_of, **kw: mapping, raising=True)


def _patch_mail(monkeypatch, result=True):
    import notify
    sent = []

    def _fake(subject, body):
        sent.append((subject, body))
        return result

    monkeypatch.setattr(notify, "email_owner", _fake, raising=True)
    return sent


def test_only_threads_past_the_threshold_are_parked(env, monkeypatch):
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01", "moving": "2026-09-13"}))
    _patch_mail(monkeypatch)
    out = nc._stalled_threads(TODAY)
    assert out["stalled_new"] == 1
    ids = {c["gate_id"] if "gate_id" in c else k
           for k, c in json.loads((env / "parked.json").read_text(encoding="utf-8")).items()}
    assert "thread-stalled:stale-one" in ids
    assert "thread-stalled:moving" not in ids


def test_thread_unknown_to_git_is_not_parked(env, monkeypatch):
    """Нить, которой нет в истории вовсе, — не находка: «давно не трогали» и «никогда
    не существовала» по одному git неразличимы, а гадать здесь значит звонить впустую."""
    _patch_activity(monkeypatch, {})
    _patch_mail(monkeypatch)
    assert nc._stalled_threads(TODAY)["stalled_new"] == 0
    assert not (env / "parked.json").exists() or \
        json.loads((env / "parked.json").read_text(encoding="utf-8")) == {}


def test_second_week_the_same_thread_is_counted_not_named(env, monkeypatch):
    """Отчёт о ПЕРЕМЕНЕ: во второй раз имя не повторяется, иначе письмо перестают открывать."""
    mapping = _last(**{"stale-one": "2026-09-01"})
    _patch_activity(monkeypatch, mapping)
    sent = _patch_mail(monkeypatch)
    nc._stalled_threads(TODAY)
    second = nc._stalled_threads(TODAY)
    assert second["stalled_new"] == 0 and second["stalled_seen"] == 1
    assert "stale one" in sent[0][1]
    assert "stale one" not in sent[1][1] and "с прошлых недель: 1" in sent[1][1]


def test_letter_goes_only_on_its_weekday(env, monkeypatch):
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    sent = _patch_mail(monkeypatch)
    out = nc._stalled_threads(TUESDAY)
    assert out["stalled_mailed"] is None and sent == []


def test_failed_delivery_is_loud(env, monkeypatch, capsys):
    """Общего датчика на почтовый канал нет — значит провал обязан заметить производитель."""
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    _patch_mail(monkeypatch, result=False)
    out = nc._stalled_threads(TODAY)
    assert out["stalled_mailed"] is False
    assert "НЕ ушло" in capsys.readouterr().err


def test_card_never_gets_a_silence_default(env, monkeypatch):
    """«Добить или закрыть» — вопрос без умолчания: карточка обязана ждать владельца вечно."""
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    _patch_mail(monkeypatch)
    nc._stalled_threads(TODAY)
    card = json.loads((env / "parked.json").read_text(encoding="utf-8"))["thread-stalled:stale-one"]
    assert card.get("auto_after") is None and card.get("default") is None
    assert pd.sweep_defaults(TODAY + _dt.timedelta(days=365)) == []


def test_threshold_matches_the_howto():
    """Срок живёт одним домом: код и страница, которую читает владелец, не имеют права
    разъехаться. Тот же приём, что у SILENCE_DAYS.

    Первая редакция сверяла с записью заказа в `BACKLOG.md` — и покраснела на полном
    прогоне в тот же час, потому что этот же коммит перенёс закрытую запись в архив.
    Догфуд §11 сработал буквально: правило «у числа один дом и сверка» проверилось на
    диффе, который его вводит. Живой дом второго экземпляра — how-to, не история."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2]
            / "docs" / "how-to" / "email_channel.md").read_text(encoding="utf-8")
    assert "застрявших нитях" in text, "рецепт молчит об отчёте — сверять срок не с чем"
    assert f"дольше {nc.STALLED_DAYS} дней" in text, (
        f"night_cycle.STALLED_DAYS={nc.STALLED_DAYS}, а docs/how-to/email_channel.md "
        f"обещает владельцу другой срок")
    assert "понедельник" in text.lower(), (
        "день недели письма тоже обещан владельцу — и тоже обязан сверяться")


# ── Снятие карточки у закрытой нити (решение владельца 23.09, вариант А) ───────

def test_карточка_закрытой_нити_снимается_с_автором_thread_closed(env, monkeypatch):
    """Нить застряла → карточка; нить закрыли в INDEX → карточка снята, автор —
    закрытие нити, а не владелец и не умолчание. Случай 23.09: три такие карточки
    висели на столе и звонили о решённом."""
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    _patch_mail(monkeypatch)
    nc._stalled_threads(TODAY)
    assert pd.get("thread-stalled:stale-one")["status"] == "open"

    index_md = env / "INDEX.md"
    index_md.write_text(INDEX.replace("`stale-one@x` | paused", "`stale-one@y` | closed"),
                        encoding="utf-8")
    out = nc._stalled_threads(TUESDAY)
    rec = pd.get("thread-stalled:stale-one")
    assert out["stalled_retired"] == 1
    assert rec["status"] == "resolved" and rec["decided_by"] == "thread_closed"
    assert "stale-one@y" in rec["decision"]
    assert pd.list_open(TUESDAY) == []


def test_открытая_нить_и_чужие_карточки_не_трогаются(env, monkeypatch):
    """Снимается ТОЛЬКО карточка закрытой нити: открытая остаётся, карточка другого
    вида с тем же slug в имени — тоже, нить без строки в INDEX — тоже (не угадываем)."""
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    _patch_mail(monkeypatch)
    pd.park("warn:stale-two что-то", "owner_decision", "чужая карточка")
    pd.park("thread-stalled:исчезнувшая", "owner_decision", "нити нет в INDEX")
    out = nc._stalled_threads(TODAY)
    assert out["stalled_retired"] == 0
    assert {g["id"] for g in pd.list_open(TODAY)} == {
        "thread-stalled:stale-one", "warn:stale-two что-то", "thread-stalled:исчезнувшая"}


def test_автор_вне_списка_отвергается(env):
    """Авторов ровно три; опечатка в авторе не должна молча записать «кто-то решил»."""
    pd.park("g", "owner_decision", "x")
    with pytest.raises(ValueError):
        pd.record_decision("g", "решено", by="closed")


def test_stalled_mail_and_card_use_plain_choices(env, monkeypatch):
    _patch_activity(monkeypatch, _last(**{"stale-one": "2026-09-01"}))
    sent = _patch_mail(monkeypatch)
    nc._stalled_threads(TODAY)
    card = pd.get("thread-stalled:stale-one")["summary"]
    for text in (card, sent[0][1]):
        assert "Варианты:" in text and "Если промолчишь" in text
        assert text.count("• ") >= 2
        assert "docs/" not in text and "коммит" not in text and "§" not in text

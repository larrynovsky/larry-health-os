"""Память машинного перевода пояснений (arch-en-gen, 29.09): ключ — хеш оригинала,
перевод с порченой формой не сохраняется, мусор убирается, сбой партии не роняет остальные."""
import desc_translation as dt


def _fake(mapping):
    return lambda part: [mapping[t] for t in part]


def test_translation_is_keyed_by_the_russian_text(tmp_path):
    p = tmp_path / "m.yaml"
    ru = "Считает `rows` за {days} дней, не больше 30"
    r = dt.refresh([ru], translate=_fake({ru: "Counts `rows` over {days} days, at most 30"}), path=p)
    assert r == {"added": 1, "rejected": [], "pruned": 0, "missing": 0}
    assert dt.english(ru, dt.load_memory(p)) == "Counts `rows` over {days} days, at most 30"
    # Оригинал поменялся — старая запись больше не попадает: перевод не может молча отстать.
    assert dt.english(ru + ".", dt.load_memory(p)) is None

    # Память переиспользуется: известный текст повторно в модель не уходит.
    def must_not_call(part):
        raise AssertionError(f"re-translated: {part}")
    again = dt.refresh([ru], translate=must_not_call, path=p)
    assert again["added"] == 0 and again["rejected"] == [], again  # сбой партии тоже был бы вызовом


def test_broken_form_is_not_stored(tmp_path):
    p = tmp_path / "m.yaml"
    cases = {
        "Отдаёт `x`": "Returns x",                  # потерян код
        "Порог {thr}": "Threshold",                 # потерян плейсхолдер
        "Три ночи подряд, 3": "Three nights",       # потеряно число
        "Проверка сна": "Проверка sleep",           # кириллица
        "Пусто": "",
    }
    r = dt.refresh(list(cases), translate=_fake(cases), path=p)
    assert r["added"] == 0 and r["missing"] == len(cases)
    reasons = dict(r["rejected"])
    assert reasons["Отдаёт `x`"] == "code" and reasons["Порог {thr}"] == "placeholders"
    assert reasons["Три ночи подряд, 3"] == "numbers" and reasons["Проверка сна"] == "cyrillic"


def test_failed_batch_keeps_others_and_prune_removes_orphans(tmp_path, monkeypatch):
    p = tmp_path / "m.yaml"
    dt.refresh(["Старое"], translate=_fake({"Старое": "Old"}), path=p)
    monkeypatch.setattr(dt, "_BATCH", 1)

    def flaky(part):
        if part == ["Второе"]:
            raise RuntimeError("synthetic outage")
        return ["First"]
    r = dt.refresh(["Первое", "Второе"], translate=flaky, path=p)
    assert r["added"] == 1 and r["pruned"] == 1 and r["missing"] == 1
    assert r["rejected"] == [("Второе", "batch:RuntimeError")]
    assert set(dt.load_memory(p)) == {dt.memory_key("Первое")}


def test_text_without_cyrillic_needs_no_memory():
    assert dt.english("get_conn(read_only)", {}) == "get_conn(read_only)"

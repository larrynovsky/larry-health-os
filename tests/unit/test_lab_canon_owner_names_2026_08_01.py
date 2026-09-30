"""Сконструированные варианты словарных имён и ратчет живости словаря.

Каждое имя проверяется двусторонне: оно сводится к своей статье
и не схлопывает соседнюю. Корпус не описывает очередь или забор человека.
"""
import lab_canon


# ── пять имён: сводится ────────────────────────────────────────────────────────

def test_review_queue_names_resolve():
    assert lab_canon.normalize("Lp a") == "Lp_a"
    assert lab_canon.normalize("Pregnenolone") == "Pregnenolone"
    assert lab_canon.normalize("albumin-bound testosterone") == \
        "Testosterone_albumin_bound"
    assert lab_canon.normalize("RET-He") == "RET_He"
    assert lab_canon.normalize("Ferritin \\ აბგ") == "Ferritin"


# ── и НЕ схлопывает соседа ─────────────────────────────────────────────────────

def test_testosterone_fractions_stay_apart():
    """Связанная с альбумином фракция отличается от свободной и биодоступной.

    Сведение к `Testosterone` подмешало бы значение фракции в тренд общего
    и дало бы «падение» на пустом месте.
    """
    got = {lab_canon.normalize(n) for n in (
        "Тестостерон", "свободный тестостерон", "биологически активный тестостерон",
        "albumin-bound testosterone")}
    assert got == {"Testosterone", "Testosterone_free",
                   "Testosterone_bioavailable", "Testosterone_albumin_bound"}


def test_ret_he_is_not_blood_hemoglobin():
    """RET-He — гемоглобин В ретикулоците (пг), не гемоглобин крови (г/л)."""
    assert lab_canon.normalize("Гемоглобин") == "HGB"
    assert lab_canon.normalize("RET-He") != "HGB"


def test_lp_a_does_not_swallow_other_lipoproteins():
    """Голое «липопротеин» → Lp(a), но соседи с более длинным именем целы."""
    assert lab_canon.normalize("Аполипопротеин А1") == "ApoA1"
    assert lab_canon.normalize("Аполипопротеин В") == "ApoB"
    assert lab_canon.normalize("окисленный липопротеин низкой плотности") == "oxLDL"


def test_backslash_separator_does_not_break_composite_names():
    """НЕГАТИВНЫЙ КОНТРОЛЬ к правке `_key`: хвост режется, только если написан
    ЧУЖОЙ письменностью. Составные имена на латинице и кириллице целы."""
    assert lab_canon.normalize("albumin/globulin") == "Albumin_Globulin_ratio"
    assert lab_canon.normalize("Neutrophils/lymphocytes ratio") == "NLR"
    assert lab_canon.normalize("Кальций/Фосфор") == "Кальций/Фосфор"


# ── ратчет: словарь не должен копить мёртвые статьи ───────────────────────────

# Замер 2026-08-01: из 680 объявленных вариантов 10 были НЕДОСТИЖИМЫ — записаны
# в форме, которую `_key` никогда не произведёт (скобки он срезает). Три из них
# принадлежали `Lp_a` и чинились этой работой. Оставшиеся семь чинить механически
# НЕЛЬЗЯ: `protein (urine)` без скобок станет `protein` и схлопнется с общим
# белком, `ph (urine)` — с кислотностью, `ка (индекс атерогенности)` — со слогом
# «ка». Каждый требует решения, а не sed'а. Ратчет держит статус-кво: новый
# мёртвый синоним запрещён, старые названы поимённо.
KNOWN_DEAD = {
    ("ALT", "alt(gpt)-b"),
    ("AST", "ast(got)-b"),
    ("GGT", "ggt- gamma glutam.trans."),
    ("Atherogenic_index", "ка (индекс атерогенности)"),
    ("Urine_Protein", "protein (urine)"),
    ("Urine_pH", "ph (urine)"),
    # «цилиндры (другие)» ОЖИЛ 14.09 и вычеркнут отсюда: ключ у него срезался до
    # голого «цилиндры», и пока этой формы в словаре не было, вариант со скобками
    # не находил свою статью. Форма добавлена — вариант достижим.
}


def test_no_new_unreachable_synonym():
    """Каждый объявленный вариант обязан находить СВОЮ статью.

    Мёртвый синоним не падает и не шумит — он просто не работает, и имя
    молча заводит второй тренд. Тот же класс, что константа `BLOOD`:
    объявлено, выглядит рабочим, не используется никогда.
    """
    dead = {(canon, v) for canon, variants in lab_canon._SYNONYMS.items()
            for v in variants if lab_canon.normalize(v) != canon}
    new = dead - KNOWN_DEAD
    gone = KNOWN_DEAD - dead
    assert not new, (
        "новый недостижимый синоним: " + ", ".join(f"{c}:{v!r}" for c, v in sorted(new))
        + ". Скорее всего он записан со скобками или хвостовой точкой — `_key` их "
        "срезает. Проверь форму ключа через lab_canon._key(вариант).")
    assert not gone, (
        "синоним ожил: " + ", ".join(f"{c}:{v!r}" for c, v in sorted(gone))
        + ". Вычеркни его из KNOWN_DEAD этим же коммитом — список есть мера "
        "того, сколько словаря ещё не работает.")


def test_hsv_single_type_does_not_fall_into_the_pair():
    """«HSV 1» не смеет свестись к паре `HSV1_2_DNA` (14.09).

    `_key` режет имя по «/», когда в хвосте нет букв: «hsv 1/2» → «hsv 1». Поэтому
    короткий вариант пары физически неотличим от ВПГ первого типа — отдельного
    аналита. Сторож недостижимых синонимов эту мину НЕ поймает: стоит записать
    ключ в срезанной форме, и он станет достижим, то есть «исправен» с его точки
    зрения. Красное здесь значит: кто-то вернул короткий вариант со слэшем.
    """
    for variant in ("hsv 1", "hsv1", "впг 1", "hsv 2", "впг 2"):
        assert lab_canon.normalize(variant) != "HSV1_2_DNA", (
            f"{variant!r} свёлся к паре 1/2 — одиночный тип потерян")

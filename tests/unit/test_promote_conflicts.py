"""Отказ промоута слить два значения — виден, и виден ровно там, где надо.

Предмет: `lab_promote.split_groups` / `pending_conflicts` — единственный дом
правила «когда два числа есть одно измерение», плюс предикат, которым его
спрашивает ночной датчик.

Оракулы разнесены по одному на утверждение: тест, который проверяет сразу всё,
на поломке говорит «что-то сломалось», а не «сломалось вот это».
"""
import pytest

COLS = ("run_id,extractor_version,source_file,page,date,panel,canonical_name,"
        "raw_name,value,unit,value_agreement,review_status")


def _row(page, name, value, status="review", run="r1", panel="hormones"):
    return (run, "v", "blank.pdf", page, "2021-06-14", panel, name, name, value,
            "нмоль/л", "agree", status)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


def _seed(health_db, rows):
    with health_db.get_conn() as c:
        c.executemany(
            f"INSERT INTO lab_results_staging({COLS}) VALUES({','.join('?' * 12)})", rows)
        c.commit()


def _conflicts(health_db):
    import lab_promote
    with health_db.get_conn() as c:
        return lab_promote.pending_conflicts(c, {})


# --- сам предмет: расхождение больше порога не сливается -------------------

def test_divergent_values_become_conflict(db):
    """Синтетика той же формы: один аналит на двух страницах одного бланка —
    один забор, два прибора, значения расходятся. Слить их значило бы выбрать победителя по порядку строк."""
    _seed(db, [_row(3, "Testosterone", 12.4), _row(9, "Testosterone", 18.6)])
    found = _conflicts(db)
    assert len(found) == 1
    # Значения — в conventional (с 2026-08-31 у тестостерона есть правило нмоль/л→ng/dL);
    # ожидание через тот же to_conventional, что и у промоута, а не литерал в сырых единицах.
    import lab_canon
    exp = [lab_canon.to_conventional("Testosterone", v, "нмоль/л")[0] for v in (12.4, 18.6)]
    assert found[0]["values"] == exp, found[0]["values"]
    assert found[0]["pages"] == [3, 9]      # без страниц конфликт не разобрать


def test_agreeing_values_do_not_conflict(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Без него тест выше зелен и у датчика, который считает
    конфликтом любую пару строк: он проверял бы наличие дубля, а не расхождение."""
    _seed(db, [_row(3, "Testosterone", 12.4), _row(9, "Testosterone", 12.41)])
    assert _conflicts(db) == []


def test_threshold_lives_in_data(db):
    """Порог — предмет, а не украшение: правка ЗНАЧЕНИЯ В БД обязана менять вердикт.
    Оракул на §9: если бы код читал литерал, а не данные, второй ассерт остался бы
    красным — и вынос порога оказался бы декоративным."""
    _seed(db, [_row(3, "Testosterone", 10.0), _row(9, "Testosterone", 10.4)])  # 4 %
    assert len(_conflicts(db)) == 1
    with db.get_conn() as c:
        c.execute("UPDATE system_config SET value_num=0.10 WHERE key='lab.conflict_spread'")
        c.commit()
    assert _conflicts(db) == []


def test_fallback_mirrors_the_seed(db):
    """Резерв — ЗЕРКАЛО сида, не альтернативная норма. Расхождение означало бы два
    источника одной величины: при живой БД одно поведение, при недоступной — другое,
    и разница не видна никому."""
    import lab_promote
    with db.get_conn() as c:
        seeded = c.execute(
            "SELECT value_num FROM system_config WHERE key='lab.conflict_spread'"
        ).fetchone()[0]
    assert seeded == lab_promote._SPREAD_FALLBACK


def test_units_are_reconciled_before_comparison(db):
    """Синтетика той же формы: магний 0,85 ммоль/л и 20,91 мг/л — одно вещество,
    одна проба. Сырые числа расходятся в ~24 раза, приведённые (≈2,066 и 2,091 мг/дл) —
    на ≈1,2 %.
    Без приведения гейт объявлял конфликтом РАЗНИЦУ ЕДИНИЦ."""
    rows = [
        ("r1", "v", "blank.pdf", 2, "2021-06-14", "chemistry", "Magnesium",
         "Магний", 0.85, "ммоль/л", "agree", "review"),
        ("r1", "v", "blank.pdf", 11, "2021-06-14", "chemistry", "Magnesium",
         "Магний, Mg", 20.91, "мг/л", "agree", "review"),
    ]
    _seed(db, rows)
    found = _conflicts(db)
    assert len(found) == 1                      # ≈1,2 % всё ещё выше порога 1 %
    lo, hi = found[0]["values"]
    assert (hi - lo) / hi < 0.02, found[0]      # но это уже 1 %, а не 2400 %
    assert found[0]["raw_values"] == ["0.85 ммоль/л", "20.91 мг/л"]  # человеку — сырое


# --- предикат датчика: кого он спрашивает ---------------------------------

def test_rejected_rows_are_not_conflicts(db):
    """Отвергнутая человеком строка — не вторая сторона спора, а вычеркнутая."""
    _seed(db, [_row(3, "Testosterone", 12.4),
               _row(9, "Testosterone", 18.6, status="rejected")])
    assert _conflicts(db) == []


def test_promoted_rows_are_not_conflicts(db):
    """Уже доехавшая в канон строка спор не открывает: датчик спрашивает про то,
    что система отказывается класть в канон ПРЯМО СЕЙЧАС."""
    _seed(db, [_row(3, "Testosterone", 12.4),
               _row(9, "Testosterone", 18.6, status="promoted")])
    assert _conflicts(db) == []


def test_conflict_spans_runs(db):
    """Замер 2026-07-31 показал, что объединение прогонов фантомов не создаёт, но
    РЕАЛЬНОЕ расхождение между прогонами датчик обязан видеть: предикат ходит по
    всему ожидающему, а не по одному run_id."""
    _seed(db, [_row(3, "Testosterone", 12.4, run="full2"),
               _row(9, "Testosterone", 18.6, run="blcanon1")])
    assert len(_conflicts(db)) == 1


# --- доставка: датчик не только считает, но и говорит ---------------------

def test_sensor_warns_on_conflict(db, monkeypatch):
    """Прокси-ловушка: датчик может вернуть верное число и никому не сказать.
    Оракул — факт вызова `warn`, а не возвращённый счётчик."""
    import integrity_tests
    _seed(db, [_row(3, "Testosterone", 12.4), _row(9, "Testosterone", 18.6)])
    said = []
    monkeypatch.setattr(integrity_tests, "warn", lambda *a, **k: said.append(a))
    monkeypatch.setattr(integrity_tests, "_iter_tenant_ro",
                        lambda: iter([("health", db.get_conn().__enter__(), True)]))
    integrity_tests.check_promote_conflicts()
    assert said, "конфликт найден, но никому не доложен"
    assert "Testosterone" in " ".join(str(x) for x in said[0])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- метод в ключе: разводит расходящееся, но не рвёт согласное -----------

def _mrow(page, name, value, method, src="read_study", unit="нмоль/л"):
    return (("r1", "v", "blank.pdf", page, "2021-06-14", "hormones", name, name,
             value, unit, "agree", "review"), method, src)


def _seed_m(health_db, triples):
    with health_db.get_conn() as c:
        for base, method, src in triples:
            cur = c.execute(
                f"INSERT INTO lab_results_staging({COLS}) VALUES({','.join('?' * 12)})",
                base)
            c.execute("UPDATE lab_results_staging SET method=?, method_source=? WHERE id=?",
                      (method, src, cur.lastrowid))
        c.commit()


def test_метод_разводит_расходящиеся_значения(db):
    """То, ради чего шаг: тестостерон масс-спектрометрией и иммуноанализом
    расходится (синтетика той же формы) — один забор, два прибора. Оба обязаны доехать в канон."""
    _seed_m(db, [_mrow(3, "Testosterone", 12.4, "Стероидный профиль (ЖХ-МС/МС)"),
                 _mrow(9, "Testosterone", 18.6, "Андрогенный статус")])
    assert _conflicts(db) == []          # конфликта больше нет
    import lab_promote
    with db.get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM lab_results_staging")]
    kept, _ = lab_promote.prepare(rows, {})
    promote, _ = lab_promote.split_groups(kept, spread=0.01)
    assert sorted(r["value"] for r in promote) == [12.4, 18.6]   # обе точки, не одна


def test_метод_не_рвёт_согласные_значения(db):
    """Синтетическое повторное представление одного измерения в двух панелях
    не должно делить согласованные значения на разные тренды."""
    _seed_m(db, [_mrow(2, "Glucose", 5.2, "Биохимический анализ крови", unit="ммоль/л"),
                 _mrow(4, "Glucose", 5.2, "Индекс HOMA-IR", unit="ммоль/л")])
    import lab_promote
    with db.get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM lab_results_staging")]
    kept, _ = lab_promote.prepare(rows, {})
    promote, _ = lab_promote.split_groups(kept, spread=0.01)
    assert len(promote) == 1, [r["method"] for r in promote]


def test_метод_без_ступени_чтения_в_ключ_не_идёт(db):
    """Метод без провенанса неотличим от назначенного. Вся нить началась с того,
    что материал был назначен, а не измерен."""
    _seed_m(db, [_mrow(3, "Testosterone", 12.4, "выдумка", src="unknown"),
                 _mrow(9, "Testosterone", 18.6, "другая выдумка", src="unknown")])
    assert len(_conflicts(db)) == 1      # различителя нет → честный конфликт

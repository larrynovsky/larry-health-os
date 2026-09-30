"""Сторож КЛАССА «исключение → тишина» (2026-07-25, после 8 находок внешнего ревью).

Класс: `except ...: return None/[]/{}/pass/continue` без единого сигнала превращает ошибку в
результат, неотличимый от нормы. Датчик перестаёт вычисляться, а рапортует «всё хорошо».
Чинился ТОЧЕЧНО трижды — `check_mc_gap` @417feac, затем дважды в этой нити 2026-07-25 — поэтому
здесь структурный сторож, а не четвёртая заплатка.

Два режима, чтобы не навязывать работу чужим сессиям:
  СТРОГО — функции валид-гейта (нить fdr-online): тихий обработчик запрещён; исключения только
    явным списком ALLOW с обоснованием, почему тишина здесь корректна.
  РАТЧЕТ — остальной `integrity_tests.py`: заморожено ЧИСЛО тихих обработчиков. Рост → FAIL с
    подсказкой. Существующие НЕ проверены на легитимность — это замороженный статус-кво, не аудит.

Позитивный контроль внизу: синтетический код с новым тихим обработчиком обязан быть найден.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIGNALS = {"warn", "fail_", "print", "raise"}
# Логгер считается сигналом ТОЛЬКО на уровнях, которые кто-то реально читает. `debug`/`info`
# намеренно НЕ здесь: лог, который никто не смотрит, — это тишина с алиби (F2-03).
LOG_SIGNALS = {"warning", "error", "exception", "critical"}

# Функции нити fdr-online: тишина запрещена.
STRICT_FUNCS = {
    "check_mc_gap", "check_passset_flicker", "check_passset_history_depth",
    "check_family_names_resolve", "check_lab_path_scope", "_lab_path_scope_warn",
    "_write_passset_snapshot", "_write_mc_gap_artifact",
    "_quarantined_pairs", "_passset_members",
    "check_gate_run_receipt", "_write_gate_run_receipt",   # fail-closed веры, 2026-07-26
    "check_gate_artifacts_liveness", "check_quarantine_stuck",  # Ш3/Ш4 ремонта, 2026-07-26
}   # `_family_names_warn` убран 2026-07-26: обёртки больше нет (датчик зарегистрирован через
# check(), который кричит сам) — и это поймал новый манифест-тест, а не глаз.

# A5 (2026-09-22, нить fdr-online): аудит тишины на МЕДИЦИНСКИХ путях и путях валид-гейта —
# решение владельца 14.09 «аудировать только их, остальное оставить с явной записью». Эти
# датчики аудированы поштучно и переведены в строгий режим: новая тишина в них — FAIL.
# Итог аудита 22.09 (integrity_tests.py): в зоне 32 тихих обработчика. 21 получил сигнал —
# 10 per-tenant чтений, где «нет таблицы» под `except Exception` прятало ЛЮБУЮ ошибку
# (→ `_absent_table`, иное → warn/raise), кардио-сенсор ЭКГ, выключавшийся молча при сбое
# импорта, сенсор HV-6, выходивший с 0 при сбое чтения, проверка диагноз-литералов, и 8
# «битая строка/дата → тихий пропуск». 7 оказались делегированием (ALLOW ниже, сверено), 4 —
# прежние обоснованные ALLOW гейта (июль). longitudinal: Gate 0.5 был fail-open без следа →
# печать + meta `daily_gate05_applied`.
MEDICAL_FUNCS = {
    "check_symptom_prompt_discipline", "check_ecg_nonsinus", "check_lab_freshness",
    "check_lab_canon_health", "check_promotion_backlog_stale", "check_lab_no_billing_rows",
    "check_memory_facts_invariants", "check_clinical_kb_populated", "check_clinical_kb_replica_fresh",
    "check_visual_verdict_rate", "check_effect_allele_coverage", "check_carrier_status_allele_coupling",
    "check_cpic_canon_consistent", "check_assessments_freshness", "check_literature_freshness",
    "check_survivorship_agent_freshness", "check_pending_proposals_ageing",
    "check_open_hypotheses_ageing", "check_unresolved_evaluations",
    "check_recommendation_engine_health", "check_constitution_conflicts_unresolved",
    "check_oura_column_completeness", "check_lab_intake_blind_spot", "scan_hollow_constitutions",
    "check_partner_epochs_ready", "_apply_gate",
}
STRICT_FUNCS = STRICT_FUNCS | MEDICAL_FUNCS

# Датчики ЖИВОСТИ (A6, 23.09, нить producer-census; пакет П5 плана остатков, «делай все этапы»).
# 14.09 владелец решил аудировать только медицину и гейт, остальное — «явной записью»
# NOT_AUDITED. 23.09 план остатков, принятый им целиком, включил и эту запись. Итог: в семи
# датчиках 11 тихих обработчиков; 8 получили сигнал — нечитаемый штамп времени пять датчиков
# принимали за «всё хорошо» (reschedule — вечное «свежий старт»), «нет таблицы» под
# `except Exception` у reschedule и гейта брифа прятало любую ошибку (→ `_absent_table`);
# 3 — делегирование, сверено (ALLOW ниже). Запись NOT_AUDITED пуста: вне строгой зоны тишины нет.
LIVENESS_FUNCS = {
    "check_arbiter_liveness", "check_consolidation_delivery_liveness", "check_db_integrity",
    "check_reschedule_liveness", "_iter_tenant_ro", "check_tenant_dbs_reachable",
    "check_morning_brief_gate_liveness",
}
STRICT_FUNCS = STRICT_FUNCS | LIVENESS_FUNCS
NOT_AUDITED: dict[str, int] = {}

# Разрешённая тишина в СТРОГОЙ области — каждая запись с обоснованием.
ALLOW = {
    ("integrity_tests.py", "check_passset_history_depth"): 2,   # битый JSON и битый штамп уже
    # кричит check_passset_flicker на том же артефакте (fail-loud там); дублировать = два алерта
    # на одно событие. Делегирование ЯВНОЕ и проверяемое: тот же файл, тот же прогон.
    ("longitudinal_analysis.py", "_write_mc_gap_artifact"): 1,  # git-sha опционален: freshness
    # артефакта держится на ДАТЕ, а не на sha; отсутствие git не мешает датчику работать.
    ("integrity_tests.py", "check_gate_artifacts_liveness"): 2,  # (1) квитанции нет/битая —
    # ветка check_gate_run_receipt, не эта; дублировать = два алерта на одно событие.
    # (2) нечитаемый штамп артефакта кладётся в `stale` и УЕЗЖАЕТ в warn ниже — сканер видит
    # только тело handler'а и потому считает ветку тихой (тот же случай, что _write_passset_snapshot).
    ("integrity_tests.py", "check_gate_run_receipt"): 1,        # ОТСУТСТВИЕ квитанции: это
    # liveness-ветка (артефакт после applied-прогона обязан быть), у неё отдельный датчик в Ш4
    # плана ремонта. Битая квитанция здесь кричит — молчит только «файла нет».
    ("longitudinal_analysis.py", "_write_passset_snapshot"): 1,  # ветка `hist, _reset = [], ...`:
    # сигнал печатается СРАЗУ ниже (`pass-set история сброшена`) и едет в снимок (history_reset) —
    # сканер видит только тело handler'а и потому считает её тихой.
    # ── A5 (22.09): делегирование на медицинских путях — сигнал уходит в список, который ниже
    # кричит (assert/warn/raise). Каждое сверено глазами с концом функции 22.09.
    ("integrity_tests.py", "check_clinical_kb_populated"): 2,     # bad.append → assert not bad
    ("integrity_tests.py", "check_clinical_kb_replica_fresh"): 1, # bad.append → assert not bad
    ("integrity_tests.py", "check_recommendation_engine_health"): 1,  # broken.append → warn
    ("integrity_tests.py", "check_cpic_canon_consistent"): 1,     # _count → None → assert n_cat and …
    ("integrity_tests.py", "check_oura_column_completeness"): 1,  # regressions.append → AssertionError
    ("integrity_tests.py", "check_lab_intake_blind_spot"): 1,     # OSError на stat: файл исчез между
    # листингом и stat — застрявшего файла больше нет, сообщать не о чем.
    # ── A6 (23.09): делегирование в датчиках живости, сверено глазами с концом функции.
    ("integrity_tests.py", "check_db_integrity"): 1,          # bad.append → assert not bad
    ("integrity_tests.py", "check_tenant_dbs_reachable"): 1,  # bad.append → assert not bad
    ("integrity_tests.py", "_iter_tenant_ro"): 1,             # неоткрывшаяся БД тенанта: те же пути и
    # тот же connect судит check_tenant_dbs_reachable (assert) — второй крик на то же событие = шум.
}

# Ратчет по ВСЕМУ integrity_tests.py (замороженный статус-кво, не аудит).
# Перезамерено 2026-07-26 после расширения правила по находке F2-03: было 34 при узком правиле
# (ловились только литеральные пустые возвраты и любой вызов снимал подозрение), стало 41 —
# +7 обработчиков, которые прятались за `logging`-вызовом, `return set()/dict()` или мёртвой
# веткой. Рост числа = расширение зрения сторожа, НЕ новая тишина в коде.
# +1 (2026-07-26, fail-closed веры): `check_gate_run_receipt` молчит на ОТСУТСТВУЮЩЕЙ квитанции
# (liveness — отдельный датчик, см. ALLOW). Битую квитанцию он кричит. Осознанный подъём.
# +2/−1 (2026-07-26, Ш4): `check_gate_artifacts_liveness` даёт две ветки под ALLOW, а
# `_fdr_qual_drift_warn` ПЕРЕСТАЛ быть тихим (P2-03: раньше любое исключение = «не Studio»).
# Чистый итог 42→43: одна настоящая тишина в проекте стала меньше, две — делегированы явно.
RATCHET_INTEGRITY_TOTAL = 14   # A6 23.09: 22 → 14 (живость аудирована; остаток — только ALLOW). A5 22.09: 43 → 22
RATCHET_LONGITUDINAL_TOTAL = 2   # A5 22.09: 3 → 2 (_apply_gate: Gate 0.5 больше не молчит)   # F2-06/R9: писатели артефактов раньше не сторожились вовсе


def silent_handlers(src: str) -> list[tuple[str, int]]:
    """Чистое ядро: (имя функции, строка) для каждого обработчика без сигнала."""
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    out = []
    def _is_empty_result(v) -> bool:
        """Пустой результат в любой форме: None/False/0, литерал [] {} () set(), а также вызовы
        конструкторов без аргументов — `set()`, `dict()`, `list()`, `tuple()` (F2-03: раньше
        распознавались только литералы, и `return set()` проходил мимо)."""
        if v is None:
            return True
        if isinstance(v, ast.Constant) and v.value in (None, False, 0):
            return True
        if isinstance(v, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
            return not getattr(v, "elts", None) and not getattr(v, "keys", None)
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                and v.func.id in {"set", "dict", "list", "tuple", "frozenset"} and not v.args:
            return True
        return False

    def _signal_calls(stmt) -> bool:
        """Сигнал засчитывается ТОЛЬКО из живой ветки и только известным именем (F2-03: раньше
        любой вызов в теле — включая `logging.debug` и вызов внутри `if False:` — снимал
        подозрение). Вложенные определения функций пропускаем: их тело здесь не исполняется."""
        # Обход РЕКУРСИВНЫЙ, не ast.walk: walk уже развернул всё поддерево, и `continue` не
        # пропускал мёртвую ветку — warn внутри `if False:` всё равно засчитывался как сигнал.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False                       # тело вложенного определения здесь не исполняется
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Constant) and not stmt.test.value:
            return any(_signal_calls(s) for s in stmt.orelse)      # живой только else
        if isinstance(stmt, ast.Raise):
            return True
        if isinstance(stmt, ast.Call):
            f = stmt.func
            if isinstance(f, ast.Name) and f.id in SIGNALS:
                return True
            if isinstance(f, ast.Attribute) and (f.attr in SIGNALS or f.attr in LOG_SIGNALS):
                return True
        return any(_signal_calls(ch) for ch in ast.iter_child_nodes(stmt))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # «Тихий» = ничего не сообщает наружу. Проверяем ОТСУТСТВИЕ сигнала (ниже) и то, что
        # тело состоит из безобидных форм. Вызов сам по себе безобидной формой СЧИТАЕТСЯ: сигнал
        # определяется именем, а не фактом вызова (F2-03: `logging.debug(e)` давал алиби тишине).
        quiet = all(
            isinstance(s, (ast.Pass, ast.Continue, ast.Break, ast.Assign, ast.AugAssign,
                           ast.FunctionDef, ast.AsyncFunctionDef, ast.Expr))
            or (isinstance(s, ast.Return) and _is_empty_result(s.value))
            or (isinstance(s, ast.If) and isinstance(s.test, ast.Constant) and not s.test.value)
            for s in node.body)
        signalled = any(_signal_calls(s) for s in node.body)
        if quiet and not signalled:
            # F2-03: атрибуция по БЛИЖАЙШЕЙ функции позволяла обойти строгую зону вложенной
            # функцией (`check_family_names_resolve` → `_scan`). Собираем ВСЮ цепочку имён:
            # принадлежность строгой зоне определяется по любому звену.
            chain, cur = [], parent.get(node)
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    chain.append(cur.name)
                cur = parent.get(cur)
            out.append((chain[0] if chain else "<module>", node.lineno, tuple(chain)))
    return out




def _scan(name: str):
    return silent_handlers((ROOT / name).read_text(encoding="utf-8"))


def test_strict_manifest_is_not_stale():
    """F2-06: строгая зона задана ИМЕНАМИ — безобидное переименование функции молча выводило её
    из-под охраны (scanner видел `check_..._v2`, strict-фильтр — пусто, ратчет не менялся).
    Теперь манифест обязан соответствовать коду: пропала функция → FAIL, обнови осознанно."""
    src = "\n".join((ROOT / m).read_text(encoding="utf-8")
                    for m in ("integrity_tests.py", "longitudinal_analysis.py"))
    defined = {n.name for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = sorted(STRICT_FUNCS - defined)
    assert not missing, (
        f"строгая зона ссылается на несуществующие функции: {missing}. Их переименовали или "
        "удалили — обнови STRICT_FUNCS осознанно, иначе класс риска вышел из-под охраны молча."
    )


def test_strict_zone_has_no_unjustified_silence():
    """В функциях валид-гейта тихий обработчик запрещён — кроме обоснованных в ALLOW.
    Принадлежность зоне считается по ВСЕЙ цепочке охватывающих функций (F2-03: вложенная
    `_scan` внутри `check_family_names_resolve` обходила проверку по ближайшему имени)."""
    bad = []
    for mod in ("integrity_tests.py", "longitudinal_analysis.py"):
        found: dict[str, int] = {}
        for _fn, _line, chain in _scan(mod):
            owner = next((c for c in chain if c in STRICT_FUNCS), None)
            if owner:
                found[owner] = found.get(owner, 0) + 1
        for fn, n in found.items():
            allowed = ALLOW.get((mod, fn), 0)
            if n > allowed:
                bad.append(f"{mod}::{fn}: тихих обработчиков {n}, разрешено {allowed}")
    assert not bad, (
        "Новая тишина в датчике валид-гейта: " + "; ".join(bad) +
        ". Исключение должно СИГНАЛИТЬ (warn/print/logger.warning) либо быть внесено в ALLOW с "
        "обоснованием, почему тишина корректна и кто ловит это событие вместо него."
    )


def test_integrity_ratchet_not_growing():
    """Число тихих обработчиков в integrity_tests.py не растёт (ратчет статус-кво)."""
    total = len(_scan("integrity_tests.py"))
    assert total <= RATCHET_INTEGRITY_TOTAL, (
        f"тихих обработчиков стало {total} (было заморожено {RATCHET_INTEGRITY_TOTAL}): новый "
        "`except: return/pass/continue` без сигнала. Добавь сигнал или осознанно подними "
        "RATCHET_INTEGRITY_TOTAL с обоснованием в коммите."
    )


def test_longitudinal_ratchet_not_growing():
    """F2-06/R9: писатели артефактов тоже под ратчетом — раньше `longitudinal_analysis.py` не
    сторожился вовсе, и тихий обработчик вне STRICT_FUNCS не ловился ничем."""
    total = len(_scan("longitudinal_analysis.py"))
    assert total <= RATCHET_LONGITUDINAL_TOTAL, (
        f"тихих обработчиков в longitudinal_analysis.py стало {total} "
        f"(заморожено {RATCHET_LONGITUDINAL_TOTAL})."
    )


# ── Позит-контроли: каждая форма, которую сторож обязан различать ────────────
_QUIET_FORMS = {
    "return None": "def f():\n    try:\n        g()\n    except Exception:\n        return None\n",
    "return set()": "def f():\n    try:\n        g()\n    except Exception:\n        return set()\n",
    "return dict()": "def f():\n    try:\n        g()\n    except Exception:\n        return dict()\n",
    "assign empty": "def f():\n    try:\n        g()\n    except Exception:\n        out = []\n    return out\n",
    "pass": "def f():\n    try:\n        g()\n    except Exception:\n        pass\n",
    "sys.exit": "def f():\n    try:\n        g()\n    except Exception:\n        sys.exit(0)\n",
    "dead warn branch": "def f():\n    try:\n        g()\n    except Exception:\n        if False:\n            warn('x')\n        return None\n",
    "logging.debug alibi": "def f():\n    try:\n        g()\n    except Exception as e:\n        logging.debug(e)\n        return None\n",
    "nested func": ("def check_family_names_resolve(c):\n    def _inner(x):\n        try:\n"
                    "            return x.execute('SELECT 1')\n        except Exception:\n"
                    "            return []\n    return _inner(c)\n"),
}
_LOUD_FORMS = {
    "warn": "def f():\n    try:\n        g()\n    except Exception as e:\n        warn('x', str(e))\n        return None\n",
    "print": "def f():\n    try:\n        g()\n    except Exception as e:\n        print(e)\n        return None\n",
    "logger.warning": "def f():\n    try:\n        g()\n    except Exception as e:\n        logger.warning(e)\n        return None\n",
    "reraise": "def f():\n    try:\n        g()\n    except Exception:\n        raise\n",
}


def test_scanner_detects_every_declared_quiet_form():
    """⭐ Каждая форма из докстринга обязана быть найдена. Формы взяты из находки F2-03:
    ревьюер прогнал их живым исполнением и сторож молчал на всех."""
    missed = [name for name, src in _QUIET_FORMS.items() if not silent_handlers(src)]
    assert not missed, f"сторож не видит тишину в формах: {missed}"


def test_scanner_ignores_loud_forms():
    """Обратная сторона: сигналящий код не должен давать ложных срабатываний."""
    false_pos = [name for name, src in _LOUD_FORMS.items() if silent_handlers(src)]
    assert not false_pos, f"ложное срабатывание на: {false_pos}"


def test_nested_silence_attributed_to_strict_owner():
    """F2-03: вложенная функция внутри строгой — принадлежность зоне по ЦЕПОЧКЕ, не по имени."""
    res = silent_handlers(_QUIET_FORMS["nested func"])
    assert res, "тишина во вложенной функции не найдена"
    assert "check_family_names_resolve" in res[0][2], res


def test_scanner_catches_the_defect_it_was_born_from():
    """Тот самый образец, который прошёл 3068 тестов и был найден человеком, а не suite."""
    sample = ("def check_x(con):\n"
              "    try:\n"
              "        rows = con.execute('SELECT 1').fetchall()\n"
              "    except Exception:\n"
              "        rows = []\n"
              "    return rows\n")
    assert [r[:2] for r in silent_handlers(sample)] == [("check_x", 4)]


def test_not_audited_record_matches_code():
    """A5: запись «не аудировано» обязана совпадать с кодом — иначе она стареет молча и через
    месяц читается как список, а не как замер. Сумма = ратчет минус разрешённое в строгой зоне."""
    found: dict[str, int] = {}
    for fn, _line, chain in _scan("integrity_tests.py"):
        if not any(c in STRICT_FUNCS for c in chain):
            found[fn] = found.get(fn, 0) + 1
    assert found == NOT_AUDITED, (
        f"тишина вне строгой зоны разошлась с записью NOT_AUDITED: код {found}, запись {NOT_AUDITED}. "
        "Новая тишина в неаудированном датчике — добавь сигнал; ушедшая — убери из записи."
    )

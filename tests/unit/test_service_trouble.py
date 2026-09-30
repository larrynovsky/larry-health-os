"""Гипотеза «человеку плохо от системы» — оракулы на чистых ядрах.

Независимо придуманная сцена: системное уведомление об учебной выгрузке
возвращается боту с жалобой на недоступную ссылку. Проверяется маршрут эскалации,
а не воспроизведение переписки.

Судья инъектируется, поэтому здесь нет сети и нет модели.
"""
import service_trouble as st

TH = {"service_trouble.ask_at": 0.4, "service_trouble.escalate_at": 0.75}
SCENE = "[example_tenant] Учебная выгрузка example_report готова; ссылка недоступна"


def _assess(text, judge=None, is_owner=False, **kw):
    return st.assess(text, "Кнопка учебного отчёта находится в меню",
                     tenant="example_tenant", dashboard_host="100.64.0.1",
                     is_owner=is_owner, judge=judge, thresholds=TH, **kw)


# ── позитивный контроль: придуманная сцена ───────────────────────────────────────

def test_the_scene_escalates_without_any_judge():
    """ГЛАВНЫЙ оракул. Придуманная сцена должна дойти до оператора даже если модель
    молчит — иначе механизм не решает задачу, ради которой заведён."""
    v = _assess(SCENE)
    assert v.signals["_echoed_system_text"] is True
    assert v.action == "escalate", v


def test_dashboard_link_alone_is_enough():
    """Человек может переслать только ссылку — хост тоже наш маркер."""
    v = _assess("а что это? http://100.64.0.1:8001/lab-review/intake_2026 ?")
    assert v.action == "escalate", v


# ── негативный контроль: обычный разговор о здоровье ─────────────────────────

def test_ordinary_health_question_stays_silent():
    v = _assess("Спал плохо, болит голова. Это может быть от давления?")
    assert v.signals["_echoed_system_text"] is False
    assert v.action == "silent", v


def test_judge_alone_at_medium_confidence_asks_the_person():
    """Средняя уверенность → бот переспрашивает САМ, а не будит оператора."""
    v = _assess("что-то бот странно себя ведёт", judge=lambda u, a: (True, 0.5, "жалоба на бота"))
    assert v.action == "ask", v
    assert v.why == "жалоба на бота"


def test_owner_is_never_asked_only_escalated():
    """Решение владельца 2026-08-02: уточняющий вопрос — только тенанту."""
    v = _assess("бот странный", judge=lambda u, a: (True, 0.5, "x"), is_owner=True)
    assert v.action == "escalate", v


# ── устойчивость ─────────────────────────────────────────────────────────────

def test_judge_failure_does_not_lose_the_echo():
    """Отказ модели не имеет права обнулить наблюдаемый признак."""
    def boom(u, a):
        raise RuntimeError("нет сети")
    v = _assess(SCENE, judge=boom)
    assert v.action == "escalate", v


def test_judge_saying_no_keeps_the_floor():
    """Судья может не понять — эхо системного текста всё равно ставит пол."""
    v = _assess(SCENE, judge=lambda u, a: (False, 0.0, ""))
    assert v.confidence >= 0.8, v


# ── чистые ядра ──────────────────────────────────────────────────────────────

def test_burst_counts_only_inside_the_window():
    assert st._burst([1000.0, 1100.0, 1200.0], now=1300.0) is True
    assert st._burst([0.0, 100.0, 1200.0], now=1300.0) is False


def test_outgoing_anomaly_needs_a_norm():
    """Без нормы аномалии не бывает — иначе первый же день даёт ложную тревогу."""
    sig = st._collect_signals("привет", "health_partner", "h", [], 0.0,
                             outgoing_last_hour=500, outgoing_norm=0)
    assert sig["outgoing_anomaly"] is False
    sig = st._collect_signals("привет", "health_partner", "h", [], 0.0,
                             outgoing_last_hour=500, outgoing_norm=2)
    assert sig["outgoing_anomaly"] is True


def test_ladder_boundaries():
    assert st._decide(0.39, False, 0.4, 0.75) == "silent"
    assert st._decide(0.40, False, 0.4, 0.75) == "ask"
    assert st._decide(0.75, False, 0.4, 0.75) == "escalate"

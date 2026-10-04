#!/usr/bin/env python3.11
"""
lifestyle_agents.py — 4 lifestyle specialists для ежедневного утреннего отчёта.

Агенты: sleep, movement, stress/hrv, energy/recovery.
Каждый проверяет наличие данных — если нет, молчит.
GP читает briefs тех, у кого данные есть, и синтезирует отчёт.

MDT-участие: generate_mdt_opinion() — async метод для deliberative council.
  Раунд A: round_a_opinions=None  → независимая позиция
  Раунд B: round_a_opinions=dict  → видит коллег, может не согласиться
"""

import llm_client
import asyncio
import hai_core
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)

DB_PATH = __import__("health_db").DB_PATH  # respects HEALTH_DATA_DIR
SPEC_DIR = Path(__file__).parent / "specialists"  # промпты в git (Ф0b), не в iCloud


# ── Base ──────────────────────────────────────────────────────────────────────

def _guarded_text(text: str, who: str) -> str:
    """Судит текст lifestyle-агента против канона и ПОПРАВЛЯЕТ (не блокирует).

    Профильный снимок содержит лишь часть имён: отсутствие показателя в нём
    не означает отсутствия измерений в каноне. Например, ограниченная выборка
    может не включить показатель, о котором агент советует спросить человека.
    Текст поступает в бриф и консилиум; политика — поправка, не блок.
    """
    try:
        import gp_context as _gc
        hits, note = _gc.judge_absence_claims(text)
    except Exception as e:
        log.warning("%s: судья отсутствия не отработал: %s", who, e)
        return text
    if not hits:
        return text
    log.warning("%s: утверждение об отсутствии против канона (%d): %s", who, len(hits),
                "; ".join(f"{h['test']} ({h['last_date']})" for h in hits))
    return text + chr(10) + chr(10) + note


class LifestyleAgent:
    agent_type: str
    agent_name: str

    def has_data(self, day: dict) -> bool:
        raise NotImplementedError

    def generate_brief(self, day: dict, stats7: dict, stats30: dict, target: date) -> str:
        raise NotImplementedError

    genome_domain: str = None   # "sleep" | "movement" | "stress" | "energy"
    # Агент использует activity_date по умолчанию; SleepAgent переопределит
    uses_sleep_date: bool = False

    # Mapping from agent genome_domain → promethease domain key
    _PROMETHEASE_DOMAIN_MAP: dict[str, str] = {
        "sleep":    "sleep",
        "movement": "recovery",
        "stress":   "stress",
        "energy":   "metabolism",
    }

    def _genome_addendum(self) -> str:
        """Компактный геномный блок для домена агента (genome_context + Promethease)."""
        if not self.genome_domain:
            return ""
        parts = []
        # Tier-1: существующий genome_context (23andMe)
        try:
            import genome_context as gc
            block = gc.build_lifestyle_genome_block(self.genome_domain, max_variants=8)
            if block:
                parts.append(block)
        except Exception as e:
            log.debug(f"genome_context для {self.agent_name}: {e}")
        # Tier-1: Promethease SNP context
        try:
            import promethease_context as pc
            prom_domain = self._PROMETHEASE_DOMAIN_MAP.get(self.genome_domain)
            if prom_domain:
                block = pc.build_lifestyle_block(prom_domain)
                if block:
                    parts.append(block)
        except Exception as e:
            log.debug(f"promethease_context для {self.agent_name}: {e}")
        return ("\n" + "\n".join(parts)) if parts else ""

    def get_mdt_data_brief(self, sleep_date: date, activity_date: date) -> str:
        """Возвращает текстовый бриф для MDT-пакета данных. Использует существующий run()."""
        return self.run(sleep_date, activity_date) or f"Данные по домену {self.agent_name} отсутствуют."

    async def generate_mdt_opinion(
        self,
        sleep_date: date,
        activity_date: date,
        patient_question: str = "",
        round_a_opinions: dict | None = None,
        client=None,
    ) -> str:
        """
        MDT-участие через Claude API.
        round_a_opinions=None  → Раунд A (независимая позиция)
        round_a_opinions=dict  → Раунд B (видит коллег, может не согласиться)
        """
        data_brief = self.get_mdt_data_brief(sleep_date, activity_date)

        # Системный промпт из ЕДИНОГО загрузчика (тот же путь, что у monthly_consilium):
        # consilium_roster.lifestyle_prompt = specialists/lifestyle_<key>.md + %%PATIENT_PROFILE%%.
        import patient_context as pc
        import consilium_roster
        system_prompt = consilium_roster.lifestyle_prompt(self.genome_domain, pc.build_patient_brief())

        # Сборка сообщения пользователя
        msg_parts = []
        if patient_question:
            msg_parts.append(
                "ВОПРОС ПАЦИЕНТА (ответь на него в первую очередь):\n" + patient_question
            )

        msg_parts.append(f"ДАННЫЕ ПО ДОМЕНУ [{self.agent_name}]:\n{data_brief}")

        if round_a_opinions:
            filtered = {k: v for k, v in round_a_opinions.items() if k != self.agent_name}
            if filtered:
                opinions_block = "\n\n".join(f"=== {k} ===\n{v}" for k, v in filtered.items())
                msg_parts.append("МНЕНИЯ КОЛЛЕГ (Раунд A):\n" + opinions_block)
                msg_parts.append(
                    "Твоя задача (Раунд B): прочитай мнения коллег. "
                    "Укажи связи, которые они могли упустить по твоему домену. "
                    "Если видишь конфликт — не молчи. Несогласие ценно, если обосновано."
                )
        else:
            msg_parts.append(
                "Твоя задача (Раунд A): дай независимое мнение строго по своему домену. "
                "Конкретно, с цифрами, с практическим выводом."
            )

        user_message = "\n\n".join(msg_parts)

        response = await asyncio.wait_for(
            client.messages.create(task="lifestyle_agents.generate_mdt_opinion",
                model=hai_core.model_for("lifestyle"),
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            ),
            timeout=45.0,
        )
        return _guarded_text(llm_client.answer_text(response), self.agent_name)

    def run(self, sleep_date: date, activity_date: date) -> str | None:
        """Возвращает текст брифа если данные есть, None если нет."""
        db.init_db()
        target = sleep_date if self.uses_sleep_date else activity_date
        day = db.get_day(str(target))
        if not self.has_data(day):
            log.info(f"{self.agent_name}: нет данных за {target}")
            return None
        stats7  = db.get_stats(7,  target)
        stats30 = db.get_stats(30, target)
        brief = self.generate_brief(day, stats7, stats30, target)
        genome = self._genome_addendum()
        if genome:
            brief = brief + "\n" + genome
        log.info(f"{self.agent_name}: бриф сформирован за {target} (геном: {'да' if genome else 'нет'})")
        return brief


# ── Sleep ─────────────────────────────────────────────────────────────────────

class SleepAgent(LifestyleAgent):
    agent_type = "lifestyle_sleep"
    agent_name = "Sleep"
    genome_domain = "sleep"
    uses_sleep_date = True

    def has_data(self, day: dict) -> bool:
        sl = day.get("sleep") or {}
        return (sl.get("totalSleep") or 0) > 0

    def generate_brief(self, day: dict, stats7: dict, stats30: dict, target: date) -> str:
        sl   = day.get("sleep") or {}
        cont = sl.get("contributors") or {}

        total   = sl.get("totalSleep", 0)
        deep_m  = int((sl.get("deep",  0) or 0) * 60)
        rem_m   = int((sl.get("rem",   0) or 0) * 60)
        awake_m = int((sl.get("awake", 0) or 0) * 60)
        score   = sl.get("sleep_score")
        eff     = cont.get("efficiency")
        restful = cont.get("restfulness")
        deep_sc = cont.get("deep_sleep")
        rem_sc  = cont.get("rem_sleep")

        start_s = (sl.get("sleepStart") or "")
        end_s   = (sl.get("sleepEnd")   or "")
        start_s = start_s[11:16] if len(start_s) > 11 else "—"
        end_s   = end_s[11:16]   if len(end_s)   > 11 else "—"

        avg7_deep_m  = int((stats7.get("avg_deep",  0) or 0) * 60)
        avg30_deep_m = int((stats30.get("avg_deep", 0) or 0) * 60)

        lines = [
            f"SLEEP {target}",
            f"Score: {score or '—'}/100  |  Duration: {total:.1f}h  |  Awake: {awake_m}m",
            f"Deep: {deep_m}m (score {deep_sc or '—'}/100)  |  REM: {rem_m}m (score {rem_sc or '—'}/100)",
            f"Bedtime: {start_s} → {end_s}  |  Efficiency: {eff or '—'}/100  |  Restfulness: {restful or '—'}/100",
            f"7d avg (n={stats7.get('n_days',0)}): {stats7.get('avg_sleep','—')}h  deep {avg7_deep_m}m  score {stats7.get('avg_sleep_score','—')}",
            f"30d avg (n={stats30.get('n_days',0)}): {stats30.get('avg_sleep','—')}h  deep {avg30_deep_m}m",
        ]

        flags = []
        # sleep_score-порог из БД тенанта (личный p10, не литерал; правило владельца «всё высчитывается»).
        # Единый primary = absolute_thresholds; sleep_score засеян+персонализирован (Ф3a-расширение).
        if score and score < db.get_threshold("sleep_score", "floor"):
            flags.append(f"низкий sleep score ({score}/100)")
        # глубокий сон/длительность — личные полы из БД (правило владельца «всё высчитывается»).
        # sleep_deep пол хранится в ЧАСАХ → ×60 к минутам deep_m. sleep_total пол в часах.
        if deep_m < db.get_threshold("sleep_deep", "floor") * 60:
            flags.append(f"мало глубокого сна ({deep_m}м, ниже персонального порога)")
        if total < db.get_threshold("sleep_total", "floor"):
            flags.append(f"короткий сон ({total:.1f}ч, ниже персонального порога)")
        # потолок пробуждений — личный p90 из БД тенанта (правило владельца «всё выводится из данных»).
        # Хранится в ЧАСАХ (как sleep_deep) → ×60 к минутам awake_m.
        if awake_m > db.get_threshold("sleep_awake", "ceiling") * 60:
            flags.append(f"много пробуждений ({awake_m}м, выше персонального порога)")
        if flags:
            lines.append("⚠ " + "; ".join(flags))

        return "\n".join(lines)


# ── Movement ──────────────────────────────────────────────────────────────────

class MovementAgent(LifestyleAgent):
    agent_type = "lifestyle_movement"
    agent_name = "Movement"
    genome_domain = "movement"

    def has_data(self, day: dict) -> bool:
        return bool(day.get("steps"))

    def generate_brief(self, day: dict, stats7: dict, stats30: dict, target: date) -> str:
        steps     = day.get("steps")       or 0
        active    = day.get("active_kcal") or 0
        dist      = day.get("distance_km") or 0
        act_score = day.get("activity_score")

        avg7_steps  = stats7.get("avg_steps")  or 0
        avg30_steps = stats30.get("avg_steps") or 0

        lines = [
            f"MOVEMENT {target}",
            f"Steps: {steps:,}  |  Distance: {dist:.1f}km  |  Active kcal: {active:.0f}",
        ]
        if act_score:
            lines.append(f"Activity score: {act_score}/100")
        lines.append(f"7d avg (n={stats7.get('n_days',0)}): {int(avg7_steps):,} steps  |  30d avg (n={stats30.get('n_days',0)}): {int(avg30_steps):,} steps")

        flags = []
        if steps < db.get_threshold("steps", "floor", variant="target"):
            flags.append(f"мало шагов ({steps:,}, цель >7000)")
        if avg7_steps and steps < avg7_steps * db.get_threshold("steps", "floor"):
            flags.append(f"значительно ниже личной нормы ({steps:,} vs avg {int(avg7_steps):,})")
        if flags:
            lines.append("⚠ " + "; ".join(flags))

        return "\n".join(lines)


# ── Stress / HRV ──────────────────────────────────────────────────────────────

class StressAgent(LifestyleAgent):
    agent_type = "lifestyle_stress"
    agent_name = "Stress/HRV"
    genome_domain = "stress"

    def has_data(self, day: dict) -> bool:
        return bool((day.get("hrv") or {}).get("avg"))

    def generate_brief(self, day: dict, stats7: dict, stats30: dict, target: date) -> str:
        hrv_val  = (day.get("hrv") or {}).get("avg")
        rhr_raw  = day.get("resting_heart_rate")
        # rhr может быть dict {value, unit} или числом
        rhr = rhr_raw.get("value") if isinstance(rhr_raw, dict) else rhr_raw
        mindful  = day.get("mindful_min")  or 0
        daylight = day.get("daylight_min") or 0

        avg7_hrv  = stats7.get("avg_hrv")  or 0
        avg30_hrv = stats30.get("avg_hrv") or 0
        avg7_rhr  = stats7.get("avg_rhr")  or 0

        # Стресс-баланс (Oura daily_stress / daily_resilience)
        stress           = day.get("stress") or {}
        resilience       = day.get("resilience") or {}
        stress_summary   = stress.get("day_summary") or ""
        stress_high_s    = stress.get("stress_high") or 0    # секунды
        recovery_high_raw = stress.get("recovery_high")      # None = ДАННЫХ НЕТ (не ноль!)
        recovery_high_s  = recovery_high_raw or 0
        resilience_level = resilience.get("level") or ""
        stress_min   = int(stress_high_s / 60)
        recovery_min = int(recovery_high_s / 60)

        lines = [
            f"STRESS/HRV {target}",
            f"HRV: {hrv_val:.1f}ms  |  RHR: {rhr or '—'} bpm",
            f"7d avg HRV (n={stats7.get('n_days',0)}): {avg7_hrv}ms  |  30d avg (n={stats30.get('n_days',0)}): {avg30_hrv}ms",
        ]
        if avg7_rhr:
            lines.append(f"7d avg RHR: {avg7_rhr} bpm")

        # Блок стресс/восстановление
        if stress_min > 0 or recovery_min > 0:
            ratio_str = f"{stress_min/recovery_min:.1f}:1" if recovery_min > 0 else "∞"
            sr_line = f"Стресс: {stress_min}м  |  Восстановление: {recovery_min}м  |  ratio {ratio_str}"
            if stress_summary:
                sr_line += f"  |  статус: {stress_summary}"
            lines.append(sr_line)
        if resilience_level:
            lines.append(f"Resilience: {resilience_level}")

        # Показываем только ненулевые значения — 0 означает "нет данных", не "ноль минут"
        parts = []
        if mindful > 0:
            parts.append(f"Mindfulness: {mindful}min")
        if daylight > 0:
            parts.append(f"Daylight: {daylight}min")
        if parts:
            lines.append("  |  ".join(parts))

        # Sprint 3 / Р-2 (2026-05-22, replaces F-117 stub):
        # Читаем извлечённые scores из вечернего чекина (если был).
        # checkin_agent.finalize_checkin сохраняет stress_score (1-10),
        # mood_score (1=neg / 2=neutral / 3=pos), energy_score (1=low / 2=med / 3=high)
        # в плоские колонки таблицы checkins.
        try:
            with db.get_conn() as _con:
                _row = _con.execute(
                    "SELECT stress_score, mood_score, energy_score FROM checkins "
                    "WHERE date=? AND time_of_day='evening' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(target),)
                ).fetchone()
            if _row:
                _MOOD_LABEL   = {1: "negative", 2: "neutral", 3: "positive"}
                _ENERGY_LABEL = {1: "low", 2: "medium", 3: "high"}
                ss = _row["stress_score"]
                ms = _row["mood_score"]
                es = _row["energy_score"]
                checkin_parts = []
                if ss is not None:
                    checkin_parts.append(f"stress={ss}/10")
                if ms is not None:
                    checkin_parts.append(f"mood={_MOOD_LABEL.get(ms, ms)}")
                if es is not None:
                    checkin_parts.append(f"energy={_ENERGY_LABEL.get(es, es)}")
                if checkin_parts:
                    lines.append(f"Checkin (eve): {'  |  '.join(checkin_parts)}")
        except Exception as _e:
            log.debug(f"checkin scores read: {_e}")

        flags = []
        if hrv_val and avg30_hrv:
            try:
                if hrv_val < float(avg30_hrv) * db.get_threshold("hrv", "floor", variant="lifestyle"):
                    flags.append(f"HRV ↓ vs 30d ({hrv_val:.0f} vs {avg30_hrv:.0f}ms, −{100*(1-hrv_val/float(avg30_hrv)):.0f}%)")
            except Exception:
                pass
        if rhr and avg7_rhr:
            try:
                if rhr > float(avg7_rhr) + 5:
                    flags.append(f"RHR ↑ ({rhr} vs avg {avg7_rhr:.0f})")
            except Exception:
                pass
        # Стресс-баланс флаги.
        # ratio-порог 2.5 — методич-АБСОЛЮТ (реш. владельца): метрика уже само-относительна
        # (знаменатель — своё восстановление), планка значимости — не личное число.
        # Отсутствие recovery нельзя превращать в измеренный ноль и затем
        # объяснять им стресс. Если данных нет, это отдельное состояние.
        if recovery_min > 0 and stress_min > 0 and (stress_min / recovery_min) > 2.5:
            flags.append(f"высокий стресс/recovery ratio ({stress_min/recovery_min:.1f}:1)")
        elif recovery_high_raw is None and stress_min > 0:
            flags.append("данных о восстановлении за день нет")
        if flags:
            lines.append("⚠ " + "; ".join(flags))

        return "\n".join(lines)


# ── Energy / Recovery ─────────────────────────────────────────────────────────

class EnergyAgent(LifestyleAgent):
    agent_type = "lifestyle_energy"
    agent_name = "Energy/Recovery"
    genome_domain = "energy"

    def has_data(self, day: dict) -> bool:
        return day.get("readiness_score") is not None

    def generate_brief(self, day: dict, stats7: dict, stats30: dict, target: date) -> str:
        readiness = day.get("readiness_score")
        act_score = day.get("activity_score")
        spo2_avg  = (day.get("spo2") or {}).get("avg")

        avg7_rd  = stats7.get("avg_readiness")  or "—"
        avg30_rd = stats30.get("avg_readiness") or "—"

        lines = [
            f"ENERGY/RECOVERY {target}",
            f"Readiness: {readiness}/100  |  Activity score: {act_score or '—'}/100",
        ]
        if spo2_avg:
            lines.append(f"SpO2: {spo2_avg:.1f}%")
        lines.append(f"7d avg readiness (n={stats7.get('n_days',0)}): {avg7_rd}  |  30d avg (n={stats30.get('n_days',0)}): {avg30_rd}")

        flags = []
        # brief-neutralization (правило владельца: всё изменяемое высчитывается): порог readiness —
        # персональный пол из БД тенанта (p10_personal), не литерал. Чинит split-brain: было
        # `<60`, а засеянный персональный пол — другое число (расходились).
        if readiness < db.get_threshold("readiness", "floor"):
            flags.append(f"низкий readiness ({readiness}/100) — тело требует восстановления")
        # похвала readiness>=85 УДАЛЕНА (решение владельца): порог-литерал не ведёт к действию (это
        # украшение, не сигнал), и 85 по шкале Oura недостижим для data-бедного тенанта → мёртвый
        # позитив-контур. Убрано целиком, а не персонализировано.
        if spo2_avg and spo2_avg < db.get_threshold("spo2", "floor"):
            flags.append(f"SpO2 снижен ({spo2_avg:.1f}%) — обратить внимание")
        if flags:
            lines.append("⚠ " + "; ".join(flags))

        return "\n".join(lines)


# ── Runner ────────────────────────────────────────────────────────────────────

AGENTS = [SleepAgent(), MovementAgent(), StressAgent(), EnergyAgent()]


def run_lifestyle_agents(sleep_date: date, activity_date: date) -> dict[str, str]:
    """
    Запускает все lifestyle-агенты.
    sleep_date    — дата для сна (Oura пишет на дату пробуждения = сегодня)
    activity_date — дата для движения, HRV, readiness (вчера для утреннего отчёта;
                    для исторических запросов = sleep_date)
    Возвращает {agent_type: brief} только для агентов с данными.
    """
    results = {}
    for agent in AGENTS:
        try:
            brief = agent.run(sleep_date, activity_date)
            if brief:
                results[agent.agent_type] = brief
        except Exception as e:
            log.warning(f"{agent.agent_name} ошибка: {e}", exc_info=True)
    log.info(f"Lifestyle agents: {list(results.keys())} (sleep={sleep_date}, activity={activity_date})")
    return results

"""Динамический профиль пациента для lifestyle-промтов."""


def build_profile_block() -> str:
    """Возвращает актуальный профиль пациента из DB."""
    try:
        import health_db as _db
        from datetime import date as _date
        from _time_inject import get_today  # seam
        prof  = _db.get_profile_context()
        ident = prof.get("identity", {})
        med   = prof.get("medical", {})
        # NEUTRAL (brief-neutralization Фаза 0): НИКАКИХ личных дефолтов. Пустое поле у тенанта
        # НЕ должно подставлять факты владельца (была спящая мина: пустой diagnosis/birth_date
        # подставлял диагноз и дату рождения владельца). Отсутствует — значит отсутствует.
        _bd   = ident.get("birth_date")
        try:
            age = (get_today() - _date.fromisoformat(_bd)).days // 365 if _bd else None
        except Exception:
            age = None
        name  = ident.get("name") or "[пациент]"
        dx    = (med.get("diagnosis") or "").strip()

        with _db.get_conn() as c:
            hr = c.execute(
                "SELECT hrv FROM daily_metrics WHERE hrv>5 "
                "AND date>=date('now','-90 days') ORDER BY hrv"
            ).fetchall()
            dr = c.execute(
                "SELECT sleep_deep FROM daily_metrics WHERE sleep_deep>0 "
                "AND date>=date('now','-90 days') ORDER BY sleep_deep"
            ).fetchall()
            d3 = c.execute(
                "SELECT AVG(sleep_deep) FROM daily_metrics "
                "WHERE sleep_deep>0 AND date>=date('now','-30 days')"
            ).fetchone()[0]

        def p(rows, col, pct):
            v = [r[col] for r in rows]
            return round(v[max(0, int(len(v)*pct/100)-1)], 1) if v else None

        h10, h90 = p(hr, "hrv", 10), p(hr, "hrv", 90)
        d3m = round(d3 * 60) if d3 else None
        hs = f"{h10}–{h90} мс (p10–p90, 90д)" if h10 else "нет данных"
        ds = f"{d3m} мин" if d3m else "нет данных"

        # NEUTRAL: строка собирается только из данных текущего тенанта.
        # Персональные умолчания удалены; отсутствующее поле опускается.
        # Базовые уровни ВСР/сна берутся из per-tenant daily_metrics.
        head = name
        if age is not None:
            head += f", {age} лет"
        if dx:
            head += f". {dx}"
        return (
            f"{head}.\n"
            f"Типичный диапазон ВСР: {hs}. Deep sleep 30d avg: {ds}."
        )
    except Exception as e:
        return f"[профиль недоступен: {e}]"

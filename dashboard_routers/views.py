"""dashboard_routers.views — 15 GET-routes (HTML pages).

Sprint 5 finish (2026-05-22): извлечено из dashboard.py.

GET endpoints:
  /, /profile, /hypotheses, /protocols, /tasks, /problems,
  /medical-record,
  /labs, /constitutions, /constitutions/{slug}, /genome, /audit
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import json
import i18n
import sqlite3
from collections import defaultdict

import markdown as _md
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from dashboard_db import _q, _has_table
from dashboard_state import templates, CONSTITUTIONS_DIR, _CONST_TITLE_KEYS
from dashboard_views import _PROFILE_SECTION_KEYS, _PROFILE_LABEL_KEYS, _PROFILE_AUTO_WRITERS


router = APIRouter(tags=["views"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    counts = {
        "profile":         _q("SELECT COUNT(*) AS n FROM patient_profile")[0]["n"],
        "hypotheses_open": _q("SELECT COUNT(*) AS n FROM memory WHERE category='hypothesis' AND active=1")[0]["n"],
        "hypotheses_all":  _q("SELECT COUNT(*) AS n FROM memory WHERE category='hypothesis'")[0]["n"],
        "protocols_active": _q("SELECT COUNT(*) AS n FROM protocols WHERE status='active'")[0]["n"],
        "protocols_total":  _q("SELECT COUNT(*) AS n FROM protocols")[0]["n"],
        "tasks_open":      _q("SELECT COUNT(*) AS n FROM tasks WHERE status='open'")[0]["n"],
        "problems_active": _q("SELECT COUNT(*) AS n FROM problem_list WHERE status LIKE 'active%' OR status LIKE 'watch%'")[0]["n"],
        "proposals_pending": _q("SELECT COUNT(*) AS n FROM problem_list_proposals WHERE status='pending'")[0]["n"],
        "labs_tests":         (_q("SELECT COUNT(DISTINCT test_name) AS n FROM lab_results")[0]["n"]
                               if _has_table("lab_results") else 0),
        "constitutions":      len(list(CONSTITUTIONS_DIR.glob("*.md"))) if CONSTITUTIONS_DIR.exists() else 0,
        "events_total":       _q("SELECT COUNT(*) AS n FROM events")[0]["n"],
        "episodes_total":     _q("SELECT COUNT(*) AS n FROM episodes_of_care")[0]["n"],
    }
    lang = i18n.lang_of()
    _now = get_now()
    weekday_name = i18n.t(f"weekday.{(_now.weekday() + 1) % 7}", lang)
    today_meta = i18n.t("dashboard.date.day_month", lang, day=_now.day,
                        month=i18n.t(f"dashboard.date.month.{_now.month}", lang))

    epigraph = None
    try:
        row = _q("SELECT value_text FROM patient_profile WHERE key='identity.epigraph' LIMIT 1")
        if row and row[0]["value_text"]:
            epigraph = row[0]["value_text"]
    except (sqlite3.Error, KeyError, IndexError):
        epigraph = None

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "counts": counts,
        "weekday_name": weekday_name,
        "today_meta": today_meta,
        "epigraph": epigraph,
        "generated_at": _now.strftime("%Y-%m-%d %H:%M"),
    })


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    rows = _q("""
        SELECT key, value_text, value_json, category, updated_at, updated_by
        FROM patient_profile
        ORDER BY category, key
    """)
    rows = [dict(r) for r in rows]
    # Статус лечения живёт в медкарте (эпизоды) — показываем выведенный, а не ручную строку.
    try:
        import treatment_summary as _ts
        _st = _ts.treatment_status_text()
    except Exception:  # silent-ok: без медкарты страница показывает только строки профиля
        _st = None
    if _st:
        rows = [r for r in rows if r["key"] != "medical.treatment_status"]
        rows.append({"key": "medical.treatment_status", "value_text": _st, "value_json": None,
                     "category": "medical", "updated_at": "", "updated_by": "medkarta"})
    lang = i18n.lang_of()
    by_category: dict[str, list] = {}
    for r in rows:
        d = dict(r)
        label_key = _PROFILE_LABEL_KEYS.get(d["key"])
        d["label"] = i18n.t(label_key, lang) if label_key else d["key"]
        d["auto"] = d["updated_by"] in _PROFILE_AUTO_WRITERS
        sec = d["category"] or i18n.t("dashboard.profile.no_section", lang)
        section_key = _PROFILE_SECTION_KEYS.get(sec)
        by_category.setdefault(i18n.t(section_key, lang) if section_key else sec, []).append(d)
    return templates.TemplateResponse(request, "profile.html", {
        "request": request,
        "by_category": by_category,
        "total": len(rows),
    })


@router.get("/hypotheses", response_class=HTMLResponse)
def hypotheses(request: Request):
    rows = _q("""
        SELECT id, date, key, value, confidence, source, active, created_at, updated_at
        FROM memory
        WHERE category='hypothesis'
        ORDER BY active DESC, created_at DESC
        LIMIT 200
    """)
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["value"]) if d["value"] else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            d["payload"] = {"_raw": d["value"]}
        out.append(d)
    return templates.TemplateResponse(request, "hypotheses.html", {
        "request": request,
        "hypotheses": out,
        "count_active": sum(1 for h in out if h["active"]),
        "count_total": len(out),
    })


@router.get("/protocols", response_class=HTMLResponse)
def protocols(request: Request):
    rows = _q("""
        SELECT id, title, behavior, rationale, frequency, status,
               linked_hypothesis_id, linked_experiment_id, domain,
               created_at, retired_at, review_date, notes
        FROM protocols
        ORDER BY status DESC, created_at DESC
    """)
    return templates.TemplateResponse(request, "protocols.html", {
        "request": request,
        "protocols": [dict(r) for r in rows],
    })


@router.get("/tasks", response_class=HTMLResponse)
def tasks(request: Request):
    rows = _q("""
        SELECT id, created_at, source, type, priority, content,
               deadline, status, resolution_type, resolved_at, resolved_text
        FROM tasks
        WHERE status='open'
           OR (status='snoozed' AND (deadline IS NULL OR deadline <= date('now')))
           OR status='answered'
        ORDER BY status, priority, created_at DESC
    """)
    return templates.TemplateResponse(request, "tasks.html", {
        "request": request,
        "tasks": [dict(r) for r in rows],
    })


@router.get("/problems", response_class=HTMLResponse)
def problems(request: Request):
    rows = _q("""
        SELECT id, problem_id, title, description, plain_summary, status, priority, domain,
               first_seen, last_updated, watch_trigger, watch_deadline,
               supporting_data, notes, created_at, review_date
        FROM problem_list
        ORDER BY
            CASE
                WHEN status LIKE 'active%' THEN 1
                WHEN status LIKE 'watch%' THEN 2
                ELSE 3
            END,
            priority,
            last_updated DESC
    """)
    problems_list = [dict(r) for r in rows]
    count_active = sum(1 for p in problems_list if p["status"] and ("active" in p["status"] or "watch" in p["status"]))
    return templates.TemplateResponse(request, "problems.html", {
        "request": request,
        "problems": problems_list,
        "count_active": count_active,
        "count_total": len(problems_list),
    })


@router.get("/proposals", response_class=HTMLResponse)
def proposals(request: Request):
    """Pending problem_list_proposals (gp_weekly + literature_curator) на ревью.

    Отдельная страница делает очередь видимой без ручной команды в боте
    (/problems → /approve|/reject).
    """
    import json as _json
    rows = _q("""
        SELECT id, source, created_at, proposed, repeats, last_proposed_at
        FROM problem_list_proposals
        WHERE status='pending'
        ORDER BY created_at
    """)
    lang = i18n.lang_of()
    items = []
    for r in rows:
        d = dict(r)
        lines = []
        try:
            parsed = _json.loads(d.get("proposed") or "[]")
            if isinstance(parsed, dict):
                parsed = [parsed]
            for ch in parsed:
                act = ch.get("action", "?")
                pid = ch.get("problem_id")
                tag = f" [{pid}]" if pid else ""
                if act == "update_field":
                    lines.append(i18n.t("dashboard.proposal.update", lang, tag=tag,
                                        field=ch.get("field"), value=str(ch.get("new_value"))[:140]))
                elif act == "literature_review_required":
                    lines.append(i18n.t("dashboard.proposal.literature", lang, tag=tag,
                                        summary=str(ch.get("summary") or "")[:180]))
                elif act in ("add", "add_problem", "create_problem"):
                    _nv = ch.get("new_value") if isinstance(ch.get("new_value"), dict) else {}
                    lines.append(i18n.t("dashboard.proposal.new_problem", lang,
                                        title=ch.get("title") or _nv.get("title") or
                                        str(ch.get("description") or _nv.get("description") or "")[:140]))
                else:
                    lines.append(f"{act}{tag}: {str(ch.get('summary') or ch.get('title') or ch)[:160]}")
        except Exception:
            lines = [str(d.get("proposed"))[:200]]
        d["lines"] = lines
        items.append(d)
    return templates.TemplateResponse(request, "proposals.html", {
        "request": request,
        "proposals": items,
        "count": len(items),
    })


@router.get("/medical-record", response_class=HTMLResponse)
def medical_record(request: Request,
                   event_type: str | None = None,
                   episode_id: int | None = None):
    lang = i18n.lang_of()
    eps_rows = _q("""
        SELECT e.id, e.title, e.start_date, e.end_date, e.status, e.notes,
               p.title AS problem_name, p.domain AS problem_domain
        FROM episodes_of_care e
        LEFT JOIN problem_list p ON p.problem_id = e.primary_problem_id
        ORDER BY CASE WHEN e.start_date IS NULL THEN 1 ELSE 0 END, e.start_date DESC
    """)
    episodes_list = [dict(r) for r in eps_rows]

    where_clauses = []
    params = []
    if event_type:
        where_clauses.append("ev.event_type = ?")
        params.append(event_type)
    if episode_id:
        where_clauses.append("ev.episode_id = ?")
        params.append(episode_id)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    ev_rows = _q(f"""
        SELECT ev.id, ev.event_type, ev.effective_date, ev.status,
               ev.performer, ev.performer_role, ev.location, ev.episode_id, ev.notes,
               ev.attachments,
               en.class       AS enc_class,
               en.specialty,
               en.reason_text,
               en.subjective,
               en.objective,
               en.assessment,
               en.plan        AS enc_plan,
               de.type        AS diag_type,
               de.modality,
               de.interpreted_report,
               de.abnormal_flags
        FROM events ev
        LEFT JOIN encounters en ON en.event_id = ev.id
        LEFT JOIN diagnostic_events de ON de.event_id = ev.id
        {where}
        ORDER BY CASE WHEN ev.effective_date IS NULL THEN 1 ELSE 0 END, ev.effective_date DESC, ev.id DESC
    """, tuple(params))
    events_all = [dict(r) for r in ev_rows]

    by_episode: dict[int, list] = defaultdict(list)
    ungrouped = []
    for ev in events_all:
        eid = ev.get("episode_id")
        if eid:
            by_episode[eid].append(ev)
        else:
            ungrouped.append(ev)

    # Итог забора из канона привязывается к событию-анализу того же файла и даты.
    # Забор без события показывается отдельной карточкой, иначе существующие анализы
    # могли бы отсутствовать в медкарте только из-за отсутствия связанного события.
    if not episode_id and event_type in (None, "", "lab_result"):
        import labs_db as _ldb
        draws = {(d["file"], d["date"]): d for d in _ldb.draw_summaries()}
        for ev in events_all:
            if ev["event_type"] not in ("lab_result", "lab") or not ev.get("effective_date"):
                continue
            try:
                att = json.loads(ev.get("attachments") or "{}")
                if isinstance(att, str):          # в каноне встречается JSON, упакованный дважды
                    att = json.loads(att)
                src = (att.get("source_file") if isinstance(att, dict) else "") or ""
            except ValueError:
                src = ""
            key = _ldb.draw_key(src, ev["effective_date"])
            same_day = [k for k in draws if k[1] == ev["effective_date"]]
            if key not in draws and len(same_day) == 1:
                key = same_day[0]
            if key in draws:
                ev["lab_summary"] = draws.pop(key)
        for d in draws.values():
            ungrouped.append({"event_type": "lab_result", "effective_date": d["date"],
                              "status": "completed", "notes": i18n.t("dashboard.medical_record.lab_note", lang, label=d["label"]),
                              "lab_summary": d})
        ungrouped.sort(key=lambda e: e.get("effective_date") or "", reverse=True)

    # Опросники (self_observation + functional_test) — по-человечески, а не `k=v` импортёра.
    _pro = [e for e in events_all if e["event_type"] == "self_observation" and e.get("modality")]
    if _pro:
        import assessment_importer as _ai
        for ev in _pro:
            scores = {}
            for part in (ev.get("interpreted_report") or "").split(","):
                k, _, v = part.strip().partition("=")
                try:
                    scores[k] = float(v)
                except ValueError:
                    pass
            ev["pro_text"] = _ai.describe_scores(ev["modality"], scores)

    for ep in episodes_list:
        ep["events"] = by_episode.get(ep["id"], [])

    # Свежее — сверху: новые события вне эпизодов раньше оказывались под старыми
    # событиями эпизодов, поэтому верх страницы создавал ложное впечатление давности.
    # Блок «вне эпизодов» идёт первым, когда в нём событие новее любого события эпизодов.
    def _newest(evs):
        return max((e.get("effective_date") or "" for e in evs), default="")
    ungrouped_first = _newest(ungrouped) > max((_newest(ep["events"]) for ep in episodes_list),
                                               default="")

    EVENT_TYPES = [
        ("", i18n.t("dashboard.medical_record.type.all", lang)),
        ("encounter", i18n.t("dashboard.medical_record.type.encounter", lang)),
        ("lab_result", i18n.t("dashboard.medical_record.type.lab_result", lang)),
        ("imaging", i18n.t("dashboard.medical_record.type.imaging", lang)),
        ("procedure", i18n.t("dashboard.medical_record.type.procedure", lang)),
    ]

    return templates.TemplateResponse(request, "medical_record.html", {
        "request":        request,
        "episodes":       episodes_list,
        "ungrouped":      ungrouped,
        "ungrouped_first": ungrouped_first,
        "total_events":   len(events_all),
        "current_type":   event_type or "",
        "event_types":    EVENT_TYPES,
    })


@router.get("/labs", response_class=HTMLResponse)
def labs(request: Request):
    if not _has_table("lab_results"):
        return templates.TemplateResponse(request, "labs.html",
                                          {"request": request, "latest": [], "recent": []})
    latest = _q("""
        SELECT lr.test_name, lr.date, lr.value, lr.value_text, lr.unit,
               lr.ref_low, lr.ref_high, lr.status, lr.source,
               COALESCE(lr.specimen, 'blood') AS specimen
        FROM lab_results lr
        INNER JOIN (
            SELECT test_name, MAX(date) AS max_date
            FROM lab_results
            GROUP BY test_name
        ) m ON lr.test_name = m.test_name AND lr.date = m.max_date
        ORDER BY
            COALESCE(lr.specimen, 'blood'),
            CASE lr.status WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'low' THEN 2 ELSE 3 END,
            lr.test_name
    """)
    recent = _q("""
        SELECT id, date, test_name, value, value_text, unit,
               ref_low, ref_high, status, source,
               COALESCE(specimen, 'blood') AS specimen
        FROM lab_results
        ORDER BY COALESCE(specimen, 'blood'), date DESC, test_name
        LIMIT 50
    """)
    return templates.TemplateResponse(request, "labs.html", {
        "request": request,
        "latest": [dict(r) for r in latest],
        "recent": [dict(r) for r in recent],
    })


@router.get("/constitutions", response_class=HTMLResponse)
def constitutions_index(request: Request):
    # Источник — БД (таблица constitutions, Option 3 2026-06-18). Файлы вне git.
    items = []
    try:
        import health_db
        rows = health_db.list_constitutions()
    except Exception:  # silent-ok: таблица отсутствует до первой генерации
        rows = []
    for r in rows:
        items.append({
            "slug": r["domain"],
            "title": r.get("title") or (i18n.t(_CONST_TITLE_KEYS[r["domain"]])
                                        if r["domain"] in _CONST_TITLE_KEYS else r["domain"]),
            "size_kb": round((r.get("size_bytes") or 0) / 1024, 1),
            "updated": (r.get("generated_at") or "")[:10],
        })
    # Геном: история обновлений ClinVar (2026-06-27)
    genome_updates = []
    try:
        import health_db as _hdb
        with _hdb.get_conn() as _conn:
            _grows = _conn.execute(
                "SELECT run_date, variants_checked, variants_changed,"
                "       (narrative IS NOT NULL AND narrative != '') AS had_significant,"
                "       sent_to_user"
                " FROM genome_update_log ORDER BY run_date DESC LIMIT 6"
            ).fetchall()
            genome_updates = [dict(r) for r in _grows]
    except Exception:  # silent-ok: genome_update_log отсутствует до первого синка
        genome_updates = []
    return templates.TemplateResponse(request, "constitutions_index.html", {
        "request": request,
        "items": items,
        "genome_updates": genome_updates,
    })


@router.get("/constitutions/{slug}", response_class=HTMLResponse)
def constitution_view(request: Request, slug: str):
    if ".." in slug or "/" in slug:
        raise HTTPException(404)
    # Источник — БД (Option 3). Нет записи → 404 (fail loud, не stale-файл).
    try:
        import health_db
        rec = health_db.get_constitution(slug)
    except Exception:
        rec = None
    if not rec:
        raise HTTPException(404)
    raw = rec["body_md"]
    title = rec.get("title") or (i18n.t(_CONST_TITLE_KEYS[slug]) if slug in _CONST_TITLE_KEYS else slug)
    updated = (rec.get("generated_at") or "")[:10]
    html = _md.markdown(raw, extensions=["extra", "tables", "toc"])
    return templates.TemplateResponse(request, "constitution_view.html", {
        "request": request,
        "slug": slug,
        "title": title,
        "html": html,
        "size_kb": round(len(raw) / 1024, 1),
        "updated": updated,
    })


@router.get("/genome", response_class=HTMLResponse)
def genome(request: Request):
    """Genome page: ClinVar variants, CPIC, traits, wellness, PRS."""

    # ── ClinVar significant variants ─────────────────────────────────────────
    variants = []
    total_variants = 0
    try:
        raw = _q("""
            WITH classified AS (
                SELECT
                    gene, conditions, description_ru,
                    CASE WHEN significance IN (
                        'Pathogenic','Pathogenic/Likely_pathogenic',
                        'Pathogenic/Likely pathogenic','Pathogenic, low penetrance'
                    ) THEN 0 ELSE 1 END AS sig_rank,
                    CASE
                        WHEN effect_allele IS NULL THEN 2
                        WHEN length(genotype)=2
                             AND substr(genotype,1,1)=effect_allele
                             AND substr(genotype,2,1)=effect_allele THEN 0
                        WHEN length(genotype)=2
                             AND (substr(genotype,1,1)=effect_allele
                                  OR substr(genotype,2,1)=effect_allele) THEN 1
                        ELSE 99
                    END AS zyg_rank
                FROM genetic_variants
                WHERE significance IN (
                    'Pathogenic','Likely_pathogenic','Pathogenic/Likely_pathogenic',
                    'Likely pathogenic','Pathogenic, low penetrance'
                )
            )
            SELECT
                gene, conditions, description_ru,
                CASE WHEN MIN(sig_rank)=0 THEN 'Pathogenic'
                     ELSE 'Likely pathogenic' END AS significance,
                CASE WHEN MIN(zyg_rank)=0 THEN 'homozygous'
                     WHEN MIN(zyg_rank)=1 THEN 'heterozygous'
                     ELSE 'unknown' END AS zygosity,
                MIN(sig_rank) AS _sig_order,
                MIN(zyg_rank) AS _zyg_order
            FROM classified
            WHERE zyg_rank != 99
            GROUP BY gene, conditions
            ORDER BY _sig_order, _zyg_order, gene
        """)
        import json as _json
        def _clean_conditions(raw_cond: str) -> str:
            """Conditions хранится как JSON-массив строк — делаем читаемой."""
            if not raw_cond:
                return ""
            try:
                parsed = _json.loads(raw_cond)
                if isinstance(parsed, list):
                    cleaned = [c for c in parsed if c and c.lower() not in ("not provided", "not specified", "")]
                    return "; ".join(cleaned) if cleaned else ""
            except Exception:
                pass
            return raw_cond if raw_cond.lower() not in ("not provided", "not specified") else ""

        variants = []
        for r in raw:
            row = dict(r)
            row["conditions"] = _clean_conditions(row.get("conditions") or "")
            variants.append(row)
        total_row = _q("SELECT COUNT(*) AS n FROM genetic_variants")
        total_variants = total_row[0]["n"] if total_row else 0
    except Exception:  # silent-ok: wave tables absent if phase not yet run
        pass

    # ── Pharmacogenomics (Wave 1, CPIC) ─────────────────────────────────────
    pharmaco = []
    try:
        raw = _q("""
            SELECT gene, star_allele_1, star_allele_2, phenotype, confidence,
                   coverage_snp_count, computed_at
            FROM pharmaco_phenotypes
            ORDER BY gene
        """)
        pharmaco = [dict(r) for r in raw]
    except Exception:  # silent-ok: wave tables absent if phase not yet run
        pass

    # ── Deterministic traits (Wave 2) ────────────────────────────────────────
    traits = []
    try:
        raw = _q("""
            SELECT trait, category, phenotype, confidence, supporting_rsids, notes, computed_at
            FROM trait_phenotypes
            ORDER BY category, trait
        """)
        import traits_pipeline  # K10: подписи заметок — на языке человека при чтении
        traits = traits_pipeline.trait_notes_for_person([dict(r) for r in raw])
    except Exception:  # silent-ok: wave tables absent if phase not yet run
        pass

    # ── Wellness markers (Wave 3) ─────────────────────────────────────────────
    wellness = []
    try:
        raw = _q("""
            SELECT gene, trait, phenotype, confidence, supporting_rsids, notes, computed_at
            FROM wellness_phenotypes
            ORDER BY gene
        """)
        import wellness_pipeline
        wellness = wellness_pipeline.wellness_notes_for_person([dict(r) for r in raw])
    except Exception:  # silent-ok: wave tables absent if phase not yet run
        pass

    # ── Polygenic Risk Scores (Wave 4, Phase H) ───────────────────────────────
    prs = []
    try:
        raw = _q("""
            SELECT pgs_id, trait_label, raw_score, snps_matched, snps_total, computed_at
            FROM prs_scores
            ORDER BY trait_label
        """)
        for r in raw:
            d = dict(r)
            total = d.get("snps_total") or 0
            matched = d.get("snps_matched") or 0
            d["coverage_pct"] = round(100.0 * matched / total, 1) if total else 0
            if d["coverage_pct"] >= 70:
                d["coverage_tier"] = "ok"
            elif d["coverage_pct"] >= 40:
                d["coverage_tier"] = "warn"
            else:
                d["coverage_tier"] = "low"
            prs.append(d)
    except Exception:  # silent-ok: wave tables absent if phase not yet run
        pass

    return templates.TemplateResponse(request, "genome.html", {
        "request": request,
        "variants": variants,
        "total_variants": total_variants,
        "pharmaco": pharmaco,
        "traits": traits,
        "wellness": wellness,
        "prs": prs,
    })

# ── CPIC drug-gene map — ПЕРЕНЕСЕНО в БД (diagnosis-hardcode A2-full, 2026-07-17) ──
# Была _CPIC_DRUG_GENE: зашивала фенотип одного профиля и отдавала его всем профилям
# под ярлыком персонального результата — кросс-тенант утечка вместо изоляции данных.
# Теперь: cpic_reference_db (cpic_drug_catalog/drug_risk/gene_implication), фенотип — из
# pharmaco_phenotypes СМОТРЯЩЕГО тенанта. См. build_drug_interactions().


@router.get("/pharmacogenetics", response_class=HTMLResponse)
def pharmacogenetics(request: Request):
    """Фармакогенетика — поиск препаратов. Справочник CPIC из БД (cpic_reference_db);
    фенотип и риск скоуплены СМОТРЯЩИМ тенантом (pharmaco_phenotypes), не зашиты."""
    genes = []
    drug_interactions: list[dict] = []
    try:
        import health_db as _hdb
        import cpic_reference_db as _cpic
        with _hdb.get_conn() as conn:
            rows = conn.execute(
                "SELECT gene, star_allele_1, star_allele_2, phenotype, confidence, "
                "       coverage_snp_count, computed_at "
                "FROM pharmaco_phenotypes "
                "ORDER BY CASE confidence "
                "  WHEN 'high' THEN 1 WHEN 'moderate' THEN 2 "
                "  WHEN 'indeterminate' THEN 3 ELSE 4 END"
            ).fetchall()
            genes = [dict(r) for r in rows]
            # Фенотип СМОТРЯЩЕГО тенанта → скоуп справочника. Пустой геном → {} →
            # build_drug_interactions вернёт нейтральный справочник со «не определён».
            tenant_phenotypes = {g["gene"]: g["phenotype"]
                                 for g in genes if g.get("phenotype")}
            drug_interactions = _cpic.build_drug_interactions(tenant_phenotypes, conn)
    except Exception:  # silent-ok: pharmaco_phenotypes / cpic-таблицы отсутствуют до seed
        genes = []
        drug_interactions = []

    return templates.TemplateResponse(request, "pharmacogenetics.html", {
        "request":           request,
        "genes":             genes,
        "drug_interactions": drug_interactions,
    })


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request, entity: str = ""):
    where = ""
    params = ()
    if entity:
        where = "WHERE entity=?"
        params = (entity,)
    rows = _q(
        f"""SELECT id, ts, entity, entity_id, action, field, old_value, new_value, actor
            FROM dashboard_edits {where}
            ORDER BY id DESC LIMIT 200""",
        params,
    )
    entities = _q("SELECT entity, COUNT(*) AS n FROM dashboard_edits GROUP BY entity ORDER BY n DESC")
    return templates.TemplateResponse(request, "audit.html", {
        "request": request,
        "edits": [dict(r) for r in rows],
        "entities": [dict(e) for e in entities],
        "current_entity": entity,
    })

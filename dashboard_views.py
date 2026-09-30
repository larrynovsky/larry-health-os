"""dashboard_views — рендеры read-cells для HTMX inline-edit endpoints.

Sprint 5 step 3 (2026-05-22): извлечено из dashboard.py.

Рендер ячейки профиля с hx-get для перехода в edit-режим:
  - api_profile_save → HTMLResponse(_profile_view_cell(...))
Плюс подписи страницы профиля. Рендеры консультаций и периодов сняты вместе со
страницами /consultations и /events (2026-09-26).
"""
from __future__ import annotations

import html as _html

import i18n


# Страница профиля: ключи подписей i18n (26.09 владелец: «в профиле бардак» — ключи
# вида medical.treatment_status и разделы identity/medical шли как есть). Нет подписи —
# показывается сам ключ: новое поле видно сразу, просто без перевода.
_PROFILE_SECTION_KEYS = {
    "identity": "dashboard.profile.section.identity",
    "medical": "dashboard.profile.section.medical",
    "routine": "dashboard.profile.section.routine",
}
_PROFILE_LABEL_KEYS = {
    "identity.name": "dashboard.profile.label.identity.name",
    "identity.birth_date": "dashboard.profile.label.identity.birth_date",
    "identity.sex": "dashboard.profile.label.identity.sex",
    "identity.height_cm": "dashboard.profile.label.identity.height_cm",
    "identity.weight_kg": "dashboard.profile.label.identity.weight_kg",
    "identity.location": "dashboard.profile.label.identity.location",
    "identity.epigraph": "dashboard.profile.label.identity.epigraph",
    "medical.diagnosis": "dashboard.profile.label.medical.diagnosis",
    "medical.treatment": "dashboard.profile.label.medical.treatment",
    "medical.allergies": "dashboard.profile.label.medical.allergies",
    "medical.treatment_status": "dashboard.profile.label.medical.treatment_status",
    "medical.devices": "dashboard.profile.label.medical.devices",
    "medical.last_pet_ct": "dashboard.profile.label.medical.last_pet_ct",
    "medical.last_pet_ct_result": "dashboard.profile.label.medical.last_pet_ct_result",
    "medical.port_catheter": "dashboard.profile.label.medical.port_catheter",
    "routine.diet": "dashboard.constitution.title.nutrition",
    "routine.fasting_labs": "dashboard.profile.label.routine.fasting_labs",
    "routine.smoking": "dashboard.profile.label.routine.smoking",
    "routine.alcohol": "dashboard.profile.label.routine.alcohol",
    "routine.caffeine": "dashboard.profile.label.routine.caffeine",
    "routine.melatonin": "dashboard.profile.label.routine.melatonin",
    "routine.bedroom_temp_c": "dashboard.profile.label.routine.bedroom_temp_c",
}
# Строки, которые пересчитывает сама система: ручная правка была бы перезаписана
# при следующем прогоне, поэтому на странице они не редактируются.
_PROFILE_AUTO_WRITERS = {"profile_reconciler", "medkarta"}



def _profile_view_cell(key: str, value_text: str | None, value_json: str | None) -> str:
    """Render kv-value <td> в read-режиме, с hx-get на edit."""
    if value_json:
        body = f'<pre class="mono">{_html.escape(value_json)}</pre>'
        cls_extra = "kv-value--json"
    elif value_text and len(value_text) > 60:
        body = _html.escape(value_text)
        cls_extra = "kv-value--narrative"
    else:
        body = _html.escape(value_text or "")
        cls_extra = "kv-value--text"
    return (
        f'<td class="kv-value {cls_extra}" '
        f'hx-get="/api/profile/{_html.escape(key)}/edit" '
        f'hx-trigger="click" hx-swap="outerHTML" '
        f'title="{_html.escape(i18n.t("dashboard.edit_hint"))}">{body}</td>'
    )

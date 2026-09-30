"""Sprint 5b integration-тесты для router isolation.

Проверяет что каждый endpoint живёт в правильном router-модуле (по prefix).
Защита от случайного добавления endpoint в неподходящий router (URL такой
же, существующие integration-тесты не падают, но архитектура страдает).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _routes_in_router(router_module_name: str) -> set[str]:
    """Возвращает множество URL paths router-а."""
    import importlib
    mod = importlib.import_module(router_module_name)
    return {r.path for r in mod.router.routes}


def test_api_profile_router_has_profile_endpoints():
    paths = _routes_in_router("dashboard_routers.api_profile")
    assert "/api/profile/{key:path}/edit" in paths
    assert "/api/profile/{key:path}" in paths
    # И ничего постороннего
    assert all(p.startswith("/api/profile") for p in paths)


def test_api_status_actions_router_has_correct_endpoints():
    paths = _routes_in_router("dashboard_routers.api_status_actions")
    assert "/api/hypotheses/{hyp_id}/{action}" in paths
    assert "/api/protocols/{prot_id}/retire" in paths
    assert "/api/problems/{prob_id}/{action}" in paths
    # /api/experiments/{exp_id}/{action} снят (BL-EXP-1, 2026-07-10)


def test_api_tasks_router_has_correct_endpoints():
    paths = _routes_in_router("dashboard_routers.api_tasks")
    expected = {
        "/api/tasks/{task_id}/edit",
        "/api/tasks/{task_id}",
        "/api/tasks/{task_id}/done",
        "/api/tasks/{task_id}/snooze",
    }
    assert expected.issubset(paths)


def test_api_events_router_has_correct_endpoints():
    paths = _routes_in_router("dashboard_routers.api_events")
    assert "/api/events/new" in paths
    assert "/api/ping" in paths


def test_views_router_has_core_get_pages():
    paths = _routes_in_router("dashboard_routers.views")
    expected_pages = {
        "/", "/profile", "/hypotheses", "/protocols", "/tasks", "/problems",
        "/medical-record", "/labs", "/constitutions",
        "/constitutions/{slug}", "/audit",
    }
    assert expected_pages.issubset(paths), (
        f"missing pages: {expected_pages - paths}, extra: {paths - expected_pages}"
    )


def test_no_router_has_duplicate_endpoint():
    """Один endpoint URL не должен быть в двух роутерах одновременно."""
    routers = [
        "dashboard_routers.api_profile",
        "dashboard_routers.api_status_actions",
        "dashboard_routers.api_tasks",
        "dashboard_routers.api_events",
        "dashboard_routers.views",
    ]
    all_paths = []
    for rmod in routers:
        all_paths.extend(_routes_in_router(rmod))
    duplicates = [p for p in all_paths if all_paths.count(p) > 1]
    assert not duplicates, f"endpoint URL дублирован между routers: {duplicates}"


def test_dashboard_app_includes_all_routers():
    """dashboard.app должен зарегистрировать все 6 routers."""
    import dashboard
    app_paths = {r.path for r in dashboard.app.routes
                 if hasattr(r, 'path') and not r.path.startswith('/openapi')
                 and not r.path.startswith('/docs') and not r.path.startswith('/redoc')
                 and r.path != '/static'}
    # Несколько key endpoints из каждого router
    must_have = {
        "/",                                        # views
        "/api/profile/{key:path}/edit",             # api_profile
        "/api/hypotheses/{hyp_id}/{action}",        # api_status_actions
        "/api/tasks/{task_id}/done",                # api_tasks
        "/api/events/new",                          # api_events
        "/audit",                                   # views
    }
    missing = must_have - app_paths
    assert not missing, f"в dashboard.app не подключены endpoints: {missing}"

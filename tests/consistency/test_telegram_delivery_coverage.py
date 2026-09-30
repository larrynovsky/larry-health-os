"""tests/consistency/test_telegram_delivery_coverage.py — полнота тенант-безопасной
доставки (инвариант multitenancy::delivery_coverage_open).

Класс бага: 2026-07-01 нотификатор хардкодил секреты владельца → гипотезы ПАРТНЁРА
ушли ВЛАДЕЛЬЦУ. Два прежних датчика ловят ИЗВЕСТНЫЕ классы: check_contracts —
хардкод `.health_secrets`+telegram без env; test_secrets_single_resolver —
env-само-резолверы. Этот датчик закрывает третий зазор: перечисляет ВСЕ прямые
`api.telegram.org/.../sendMessage` каналы и требует, чтобы КАЖДЫЙ брал получателя из
тенант-резолвера (secrets_dir/get_chat_id) ЛИБО был осознанным admin-global
исключением (secrets_paths.ADMIN_GLOBAL_TG_ALLOW). Ловит НОВЫЙ канал с получателем из
литерала/нового env/чужой БД, который прежние два пропустят.

camelCase `sendMessage` — только прямые HTTP-вызовы Telegram API; python-telegram-bot
использует `send_message` (snake), получатель туда приходит параметром из get_chat_id().
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

_REPO = Path(__file__).resolve().parents[2]

# Маркеры тенант-безопасного получателя: единый резолвер или его канонический потребитель.
_SAFE_MARKERS = ("secrets_dir", "get_chat_id")


def _repo_sources(suffixes) -> dict[str, str]:
    """Периметр — из ОДНОГО дома (secrets_paths.GUARDED_SUFFIXES), а не rglob('*.py')
    по копии в каждом стороже: два периметра разошлись бы молча."""
    out: dict[str, str] = {}
    for suf in suffixes:
        for f in _REPO.rglob(f"*{suf}"):
            rel = str(f.relative_to(_REPO))
            if "__pycache__" in rel or rel.startswith("tests/"):
                continue
            out[rel] = f.read_text(encoding="utf-8", errors="ignore")
    return out


def _scan_unsafe_senders(files: dict[str, str], allow: set[str]) -> list[str]:
    """files: {relpath: src}. Возвращает прямые sendMessage-каналы, что НЕ берут
    получателя из тенант-резолвера и НЕ в allow (admin-global). Чистая функция —
    тестируется на синтетике (позитивный контроль)."""
    offenders = []
    for rel, src in files.items():
        if "sendMessage" not in src:
            continue
        if rel in allow:
            continue
        if any(m in src for m in _SAFE_MARKERS):
            continue
        offenders.append(rel)
    return sorted(offenders)


def test_scan_positive_control():
    """Датчик обязан краснеть на нарочно сломанном (RST)."""
    # хардкод получателя без резолвера → пойман
    evil = {"evil_sender.py":
            'url = f"https://api.telegram.org/bot{t}/sendMessage"\nchat_id = "123456"'}
    assert _scan_unsafe_senders(evil, allow=set()) == ["evil_sender.py"]

    # тенант-безопасный (secrets_dir) → чист
    good = {"good.py":
            'from secrets_paths import secrets_dir\nurl=".../sendMessage"\nchat=(secrets_dir()/"x")'}
    assert _scan_unsafe_senders(good, allow=set()) == []

    # get_chat_id → чист
    good2 = {"g2.py": 'chat_id=get_chat_id()\nurl=".../sendMessage"'}
    assert _scan_unsafe_senders(good2, allow=set()) == []

    # admin-global в allow → чист даже с хардкодом
    al = {"monthly_api_report.py": 'Path.home()/".health_secrets"\n".../sendMessage"'}
    assert _scan_unsafe_senders(al, allow={"monthly_api_report.py"}) == []


def test_all_telegram_senders_tenant_safe_or_allowlisted():
    """LIVE: каждый прямой sendMessage-канал в репо тенант-безопасен или admin-global."""
    from secrets_paths import ADMIN_GLOBAL_TG_ALLOW

    from secrets_paths import GUARDED_SUFFIXES

    files = _repo_sources(GUARDED_SUFFIXES)

    offenders = _scan_unsafe_senders(files, allow=set(ADMIN_GLOBAL_TG_ALLOW))
    assert not offenders, (
        f"прямой sendMessage без тенант-резолвера и не в ADMIN_GLOBAL_TG_ALLOW: {offenders}. "
        "Возьми получателя из secrets_dir()/get_chat_id() (иначе в контексте партнёра "
        "уйдёт владельцу — кросс-тенант утечка), либо добавь в secrets_paths."
        "ADMIN_GLOBAL_TG_ALLOW осознанным решением (только admin-global инфра, не мед-данные).")


# ── Ф1/Ф3 (2026-09-02): периметр по языкам канала + полнота реестра классов ──

def test_scan_catches_shell_channel():
    """RED-first Ф1: канал на shell обязан ловиться так же, как на python.

    До 02.09 оба сторожа сканировали только *.py, и watch_and_test.sh со своим curl
    был невидим ОБОИМ. Периметр задаётся языками КАНАЛОВ, а не языком сканера.
    """
    evil = {"evil.sh": 'TOKEN=$(cat "$HOME/.health_secrets/telegram_token")\n'
                       'curl -s "https://api.telegram.org/bot${TOKEN}/sendMessage" -d "chat_id=1"'}
    assert _scan_unsafe_senders(evil, allow=set()) == ["evil.sh"]


def test_perimeter_covers_shell_files():
    """Периметр реально включает .sh, а не только объявляет это."""
    from secrets_paths import GUARDED_SUFFIXES
    assert ".sh" in GUARDED_SUFFIXES
    files = _repo_sources(GUARDED_SUFFIXES)
    assert any(f.endswith(".sh") for f in files), "в периметре нет ни одного .sh — сканер слеп"
    assert "run_checks.sh" in files


from secrets_paths import referenced_secret_names as _referenced_secret_names  # noqa: E402


def test_every_referenced_secret_is_classified():
    """Ф3: имя, которое код читает как секрет, обязано иметь объявленный класс.

    Без этого реестр отстанет ровно так же, как отстал предикат из трёх имён:
    календарь партнёра и location_ingest_token жили вне охраны месяцами.
    """
    from secrets_paths import GUARDED_SUFFIXES, SECRET_SCOPE
    referenced = _referenced_secret_names(_repo_sources(GUARDED_SUFFIXES).values())
    undeclared = sorted(n for n in referenced if n not in SECRET_SCOPE)
    assert not undeclared, (
        f"имена читаются как секреты, но класс не объявлен: {undeclared}. "
        "Объяви в secrets_paths.SECRET_SCOPE (tenant | owner | state) — "
        "решение о классе принимает владелец, машина стережёт только наличие объявления.")


def test_registry_completeness_has_a_positive_control():
    """Датчик полноты обязан краснеть на нарочно необъявленном (иначе он вечно зелен)."""
    from secrets_paths import SECRET_SCOPE
    synthetic = {"x.py": 'k = Path.home() / ".health_secrets" / "brand_new_token"'}
    found = _referenced_secret_names(synthetic.values())
    assert "brand_new_token" in found, "сканер не видит новое имя — датчик мёртв"
    assert "brand_new_token" not in SECRET_SCOPE

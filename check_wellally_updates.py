#!/usr/bin/env python3.11
"""
check_wellally_updates.py — отслеживает обновления в upstream WellAlly-health.

Сравнивает текущие SHA промптов специалистов с последними коммитами на GitHub.
Уведомляет в Telegram если есть новые файлы или изменения.

GitHub: https://github.com/huifer/WellAlly-health
        https://github.com/huifer/Claude-Ally-Health

Запуск:
    python3.11 check_wellally_updates.py          # проверка + отчёт
    python3.11 check_wellally_updates.py --notify  # + Telegram уведомление
"""

from _time_inject import get_now  # seam
import json
import sys
import urllib.request
import urllib.error
import hashlib
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REPOS = [
    ("huifer", "WellAlly-health"),
    ("huifer", "Claude-Ally-Health"),
]

# Промпты живут в git (Ф0b), не в iCloud — единый источник с wellally_consult.py
# (SPEC_DIR = Path(__file__).parent/"specialists"). Старый iCloud-путь дрейфанул
# в пустоту → пофайловое сравнение помечало ВСЕ файлы как «новые». Fix 2026-06-19.
SPEC_DIR = Path(__file__).parent / "specialists"
# Состояние — рантайм, НЕ репозиторий (2026-08-31): файл был tracked, и checkout/деплой на Studio
# откатывал его к апрельскому SHA → шестинедельный коммит приходил как «новый». Дом — logs/,
# как у suite_last_run.json и pytest_failed_state.tsv. Миграция на Studio сделана руками 31.08
# (cp → logs/); fallback на старый путь не держим — тест, читающий настоящий файл на диске
# машины, хуже отсутствующего (§20).
STATE_FILE = Path(__file__).parent / "logs" / "wellally_upstream_state.json"

SECRETS_DIR = Path.home() / ".health_secrets"
BOT_TOKEN_FILE = SECRETS_DIR / "telegram_token"
CHAT_ID_FILE   = SECRETS_DIR / "telegram_chat_id"


# ── GitHub API ────────────────────────────────────────────────────────────────

def _gh_get(url: str) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "health-os/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        log.warning(f"GitHub API {url}: {e}")
        return None
    except Exception as e:
        log.warning(f"GitHub API error: {e}")
        return None


def get_latest_commit(owner: str, repo: str) -> dict | None:
    data = _gh_get(f"https://api.github.com/repos/{owner}/{repo}/commits/main")
    if not data:
        data = _gh_get(f"https://api.github.com/repos/{owner}/{repo}/commits/master")
    return data


def get_specialists_tree(owner: str, repo: str) -> list[dict]:
    """Возвращает список файлов в .claude/specialists/ из репо."""
    data = _gh_get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/.claude/specialists"
    )
    return data if isinstance(data, list) else []


# ── Локальное состояние ───────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:  # noqa: BLE001 — битое состояние = пустое, лог остаётся
            log.warning(f"state {STATE_FILE} не читается: {e}")
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def local_file_sha(path: Path) -> str:
    """GitHub blob SHA = sha1("blob {size}\0{content}")"""
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


# ── Сравнение ─────────────────────────────────────────────────────────────────

def check_repo(owner: str, repo: str, state: dict) -> dict:
    """Проверяет один репо. Возвращает dict с результатами."""
    result = {"repo": f"{owner}/{repo}", "new_commits": False,
              "changed_files": [], "new_files": [], "errors": []}

    commit = get_latest_commit(owner, repo)
    if not commit:
        result["errors"].append("repo not found or API error")
        return result

    latest_sha = commit["sha"]
    last_known  = state.get(f"{owner}/{repo}", {}).get("last_commit_sha")

    if latest_sha == last_known:
        log.info(f"{owner}/{repo}: no new commits (at {latest_sha[:7]})")
        return result

    result["new_commits"] = True
    result["latest_sha"] = latest_sha
    result["latest_date"] = commit["commit"]["author"]["date"]
    result["latest_message"] = commit["commit"]["message"].split("\n")[0][:80]

    # Сравниваем файлы специалистов
    remote_files = get_specialists_tree(owner, repo)
    if remote_files:
        for remote in remote_files:
            name = remote.get("name", "")
            if not name.endswith(".md"):
                continue
            local_path = SPEC_DIR / name
            if not local_path.exists():
                result["new_files"].append(name)
            else:
                local_sha = local_file_sha(local_path)
                if local_sha != remote.get("sha", ""):
                    result["changed_files"].append(name)

    return result


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text: str):
    import notify
    import i18n
    notify.weekly(i18n.t("owner.weekly.upstream"))


# ── Main ──────────────────────────────────────────────────────────────────────

def main(notify: bool = False):
    state = load_state()
    any_updates = False
    touched_prompts = False
    report_lines = [f"🔍 WellAlly upstream check — {get_now().strftime('%Y-%m-%d')}"]

    for owner, repo in REPOS:
        log.info(f"Checking {owner}/{repo}...")
        result = check_repo(owner, repo, state)

        if result["errors"]:
            report_lines.append(f"\n❌ {result['repo']}: {result['errors'][0]}")
            continue

        if not result["new_commits"]:
            report_lines.append(f"\n✅ {result['repo']}: без изменений")
            continue

        any_updates = True
        if result["new_files"] or result["changed_files"]:
            touched_prompts = True
        report_lines.append(f"\n🆕 {result['repo']}")
        report_lines.append(f"   Коммит: {result.get('latest_sha','')[:7]} ({result.get('latest_date','')[:10]})")
        report_lines.append(f"   {result.get('latest_message','')}")

        if result["new_files"]:
            report_lines.append(f"   Новые файлы: {', '.join(result['new_files'])}")
        if result["changed_files"]:
            report_lines.append(f"   Изменены: {', '.join(result['changed_files'])}")

        # Сохраняем новый SHA
        state.setdefault(f"{owner}/{repo}", {})["last_commit_sha"] = result["latest_sha"]

    report_lines.append(
        "\nhttps://github.com/huifer/WellAlly-health\n"
        "https://github.com/huifer/Claude-Ally-Health"
    )

    report = "\n".join(report_lines)
    print(report)

    if any_updates:
        log.info("Updates found — saving state")
        save_state(state)
        # Telegram — только когда изменились ПРОМПТЫ специалистов (новые/изменённые .md).
        # Коммит «docs: optimize README» (31.08.2026) прилетел в чат как 🆕 с нулём файлов —
        # человек спросил «надо ли что-то делать?», ответ был «нет». Шум учит не читать.
        if notify and touched_prompts:
            _send_telegram(report)
        elif notify:
            log.info("upstream commit без изменений промптов — Telegram не шлём")
    else:
        log.info("No updates in any repo")


if __name__ == "__main__":
    notify = "--notify" in sys.argv
    main(notify=notify)

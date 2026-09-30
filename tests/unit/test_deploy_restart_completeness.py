"""Ратчет полноты рестарта: кто держит код в памяти и не перезапускается деплоем.

Регрессия 2026-08-01: `com.larry.health.watcher.partner` крутил код 16 суток,
фикс лежал на диске с 29.07 — 19278 сообщений тенанту при зелёных датчиках.
Хук перезапускает джобы по списку имён, вписанному руками; список стареет молча.

Оракулы — на чистых ядрах: сам обход launchd Studio-only и на тест-хосте не идёт.
"""
from daemon_liveness import parse_restarted_labels, select_memory_resident

REPO = "/Users/zz/health_scripts"


def _job(*args, keepalive=True):
    return {"KeepAlive": keepalive, "ProgramArguments": list(args)}


def test_hook_labels_are_read_from_its_text():
    src = """
    ssh "$STUDIO" "launchctl unload ~/Library/LaunchAgents/com.larry.health.bot.plist"
    ssh "$STUDIO" 'launchctl kickstart -k gui/$(id -u)/com.larry.health.bot.partner'
    for s in com.larry.health.dashboard com.larry.health.dashboard.partner; do :; done
    """
    assert parse_restarted_labels(src) == {
        "com.larry.health.bot", "com.larry.health.bot.partner",
        "com.larry.health.dashboard", "com.larry.health.dashboard.partner"}


def test_python_daemon_of_this_repo_is_memory_resident():
    """ПОЗИТИВ: KeepAlive + .py из репозитория = держит код в памяти."""
    jobs = [("com.larry.health.watcher.partner",
             _job("/opt/homebrew/bin/python3.11", f"{REPO}/lab_intake_watcher.py"))]
    assert select_memory_resident(jobs, REPO) == {"com.larry.health.watcher.partner"}


def test_shell_watcher_is_not_counted():
    """НЕГАТИВ: джоба на .sh порождает интерпретатор на событие — рестарт не нужен."""
    jobs = [("com.larry.health.watcher", _job("/bin/zsh", f"{REPO}/watch_and_import.sh"))]
    assert select_memory_resident(jobs, REPO) == set()


def test_foreign_project_and_oneshot_are_not_counted():
    """НЕГАТИВ: чужой проект (наш деплой его не трогает) и не-KeepAlive."""
    jobs = [("com.example.helper", _job("python3.11", "/Users/zz/helper/helper.py")),
            ("com.larry.health.daily", _job("python3.11", f"{REPO}/daily.py", keepalive=False))]
    assert select_memory_resident(jobs, REPO) == set()


def test_the_actual_regression_is_caught():
    """Сцена 01.08 целиком: хук знает четыре имени, в памяти живут шесть."""
    hook = ("com.larry.health.bot com.larry.health.bot.partner "
            "com.larry.health.dashboard com.larry.health.dashboard.partner")
    jobs = [(lbl, _job("python3.11", f"{REPO}/{mod}"))
            for lbl, mod in [("com.larry.health.bot", "telegram_bot.py"),
                             ("com.larry.health.bot.partner", "telegram_bot.py"),
                             ("com.larry.health.dashboard", "dashboard.py"),
                             ("com.larry.health.dashboard.partner", "dashboard.py"),
                             ("com.larry.health.lab-intake", "lab_intake_watcher.py"),
                             ("com.larry.health.watcher.partner", "lab_intake_watcher.py")]]
    missing = select_memory_resident(jobs, REPO) - parse_restarted_labels(hook)
    assert missing == {"com.larry.health.lab-intake", "com.larry.health.watcher.partner"}

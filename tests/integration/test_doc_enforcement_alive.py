"""
P3-3 — Meta-test: проверяет, что drift-инфраструктура существует и функциональна.

Источник: ROADMAP Wave 3-DOC v2 P3-3. «Слой 4» 4-слойной системы enforcement.

Цель: если кто-то удалит `doc_inventory.yaml`, тест-файлы или хук — meta-test
падает в первую же ночь. Это последний барьер, ловящий «удалили всю инфру».

Что НЕ ловит этот тест: удаление самого meta-теста. Это фундаментальный предел —
где-то цепочка обрывается. Защита — `git log` и code review.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_doc_inventory_yaml_exists_and_parses():
    """Слой SSOT: doc_inventory.yaml должен существовать и быть валидным."""
    import yaml
    yaml_path = ROOT / "doc_inventory.yaml"
    assert yaml_path.exists(), "doc_inventory.yaml не найден"
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for key in ("live", "archive", "history_section_headers",
                "stop_words_in_live", "ignored_paths", "silent_except_baseline"):
        assert key in data, f"doc_inventory.yaml не содержит ключ {key!r}"


def test_doc_inventory_test_file_exists():
    f = ROOT / "tests" / "integration" / "test_doc_inventory.py"
    assert f.exists(), "tests/integration/test_doc_inventory.py отсутствует"


def test_doc_invariants_test_file_exists():
    f = ROOT / "tests" / "integration" / "test_doc_invariants.py"
    assert f.exists(), "tests/integration/test_doc_invariants.py отсутствует"


def test_meta_test_self_exists():
    self_path = Path(__file__)
    assert self_path.exists()
    assert self_path.name == "test_doc_enforcement_alive.py"


@pytest.mark.owner_data   # CLAUDE.md — закрытая часть (28.09)
def test_claude_md_contains_rule_7():
    claude = ROOT / "CLAUDE.md"
    assert claude.exists(), "CLAUDE.md отсутствует — это обязательное reference"
    text = claude.read_text(encoding="utf-8")
    assert "§7" in text or "Правило #7" in text or "### 7." in text, \
        "Правило §7 (silent-except) не найдено в CLAUDE.md — drift в правилах"
    assert "silent" in text.lower(), "Правило §7 должно упоминать silent-except"


# §6 RETIRED (2026-06-28): rsync sync-скрипты удалены (доставка через git push),
# тесты на их существование/упоминание сняты вместе с правилом.


# ─── P1-4 / P3-4 acceptance: hook реально работает ──────────────────────────

def test_pre_commit_hook_script_exists_and_executable():
    """Слой 1: scripts/git-hooks/pre-commit существует и executable (P1-4)."""
    hook_script = ROOT / "scripts" / "git-hooks" / "pre-commit"
    assert hook_script.exists(), "scripts/git-hooks/pre-commit отсутствует (P1-4 не выполнен)"
    import stat
    mode = hook_script.stat().st_mode
    assert mode & stat.S_IXUSR, "pre-commit hook не executable"


def test_pre_commit_check_py_uses_hardcoded_python_path():
    """Хук должен хардкодить /opt/homebrew/bin/python3.11 — в ssh PATH пустой.

    Зафиксировано в ~/.infrastructure.md: «В SSH PATH нет Homebrew. Всегда полные пути».
    """
    hook = (ROOT / "scripts" / "git-hooks" / "pre-commit").read_text()
    assert "/opt/homebrew/bin/python3.11" in hook, \
        "Хук должен хардкодить путь python3.11, иначе сломается в ssh-сессиях"


@pytest.mark.host_only
def test_pre_commit_check_runs_clean():
    """Pre-commit check на чистом файле → exit 0 (acceptance P1-4)."""
    check_py = ROOT / "scripts" / "git-hooks" / "pre_commit_check.py"
    if not check_py.exists():
        pytest.skip("pre_commit_check.py отсутствует")
    # tests/README.md — гарантированно в LIVE, без stop-words.
    res = subprocess.run(
        ["/opt/homebrew/bin/python3.11", str(check_py), "tests/README.md"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, \
        f"Чистый pre-commit check вернул {res.returncode}\nstdout: {res.stdout}\nstderr: {res.stderr}"


@pytest.mark.host_only
def test_pre_commit_check_blocks_stop_word():
    """Pre-commit check на файле с stop-word → exit 1 (acceptance P3-4).

    Создаём временный live-кандидат через монipulation — но сам hook читает
    реальные файлы, поэтому добавляем stop-word в существующий LIVE-файл
    через tempfile-копию + временный rename. Безопасный путь: модифицируем
    tests/README.md, вызываем check, восстанавливаем.
    """
    check_py = ROOT / "scripts" / "git-hooks" / "pre_commit_check.py"
    if not check_py.exists():
        pytest.skip("pre_commit_check.py отсутствует")

    target = ROOT / "tests" / "README.md"
    if not target.exists():
        pytest.skip("tests/README.md не найден")

    orig = target.read_text(encoding="utf-8")
    try:
        target.write_text(orig + "\n\n## injected: vps.nip.io stop-word\n", encoding="utf-8")
        res = subprocess.run(
            ["/opt/homebrew/bin/python3.11", str(check_py), "tests/README.md"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode == 1, \
            f"Грязный pre-commit check должен вернуть 1, вернул {res.returncode}\nstdout: {res.stdout}"
        assert "nip.io" in res.stdout, \
            f"Сообщение должно упоминать stop-word, получили: {res.stdout!r}"
    finally:
        target.write_text(orig, encoding="utf-8")


@pytest.mark.host_only
def test_pre_commit_check_perf_under_5s():
    """T-P3-В: hook отрабатывает <5с на типичном размере (P3-4 acceptance)."""
    check_py = ROOT / "scripts" / "git-hooks" / "pre_commit_check.py"
    if not check_py.exists():
        pytest.skip("pre_commit_check.py отсутствует")
    import time
    start = time.time()
    subprocess.run(
        ["/opt/homebrew/bin/python3.11", str(check_py), "tests/README.md"],
        cwd=str(ROOT),
        capture_output=True,
        timeout=10,
    )
    elapsed = time.time() - start
    assert elapsed < 5.0, f"Pre-commit check >{5.0}с — slow ({elapsed:.2f}с)"


# ─── P5-2 acceptance: GEN:KEY_PATHS ─────────────────────────────────────────

def test_gen_key_paths_script_exists_and_has_hostname_guard():
    """gen_key_paths.py существует и hostname-guarded (правило #8)."""
    gen = ROOT / "gen_key_paths.py"
    assert gen.exists(), "gen_key_paths.py отсутствует — P5-2 не выполнен"
    text = gen.read_text(encoding="utf-8")
    assert "infra_config.is_primary" in text, \
        "gen_key_paths.py должен иметь hostname-guard (правило #8)"
    assert "sys.exit" in text, "gen_key_paths.py должен exit при не-Studio запуске"


def test_arch_snapshot_key_paths_block_present():
    """ARCH_SNAPSHOT.md содержит GEN:KEY_PATHS блок (P5-2)."""
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    assert "<!-- GEN:KEY_PATHS:START -->" in arch, \
        "GEN:KEY_PATHS:START маркер отсутствует — запусти gen_key_paths.py на Studio"
    assert "<!-- GEN:KEY_PATHS:END -->" in arch, "GEN:KEY_PATHS:END маркер отсутствует"


def test_arch_snapshot_key_paths_contains_canonical_db_path():
    """Canonical путь ~/health/data/health.db в KEY_PATHS.

    После P1-1 hostname-aware canonical = Studio local, не iCloud.
    """
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    block_start = arch.find("<!-- GEN:KEY_PATHS:START -->")
    block_end = arch.find("<!-- GEN:KEY_PATHS:END -->")
    assert block_start >= 0 and block_end > block_start
    block = arch[block_start:block_end]
    assert "~/health/data/health.db" in block, \
        "Canonical путь к БД отсутствует в KEY_PATHS — документация устарела"


def test_arch_snapshot_key_paths_no_icloud_as_canonical():
    """iCloud Mobile Documents не упомянут как canonical (только как 'НЕ canonical').

    Защита от регрессии: в KEY_PATHS может появиться путь iCloud (это OK
    как контекст), но он должен быть явно помечен как реплика.
    """
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    block_start = arch.find("<!-- GEN:KEY_PATHS:START -->")
    block_end = arch.find("<!-- GEN:KEY_PATHS:END -->")
    block = arch[block_start:block_end]
    icloud_mentions = block.count("Mobile Documents")
    if icloud_mentions > 0:
        # Если упомянут — должен быть явный маркер «НЕ canonical» / «реплика».
        assert ("НЕ canonical" in block) or ("реплика" in block) or ("read-only" in block), \
            "iCloud Mobile Documents упомянут в KEY_PATHS без маркера 'НЕ canonical/реплика'"


# ─── P5-3 / P5-4 acceptance: gen_arch_blocks.py ─────────────────────────────

def test_gen_arch_blocks_script_exists_and_has_hostname_guard():
    """gen_arch_blocks.py существует и hostname-guarded (правило #8)."""
    gen = ROOT / "gen_arch_blocks.py"
    assert gen.exists(), "gen_arch_blocks.py отсутствует — P5-3/P5-4 не выполнены"
    text = gen.read_text(encoding="utf-8")
    assert "infra_config.is_primary" in text and "sys.exit" in text, \
        "gen_arch_blocks.py должен иметь hostname-guard (правило #8)"


def test_arch_snapshot_test_coverage_block_present():
    """GEN:TEST_COVERAGE блок присутствует и содержит данные."""
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    assert "<!-- GEN:TEST_COVERAGE:START -->" in arch
    assert "<!-- GEN:TEST_COVERAGE:END -->" in arch
    start = arch.find("<!-- GEN:TEST_COVERAGE:START -->")
    end = arch.find("<!-- GEN:TEST_COVERAGE:END -->")
    block = arch[start:end]
    assert "TOTAL" in block or "Нет данных" in block, \
        "GEN:TEST_COVERAGE блок не содержит ни таблицы, ни маркера 'Нет данных'"


def test_arch_snapshot_xfail_list_block_present():
    """GEN:XFAIL_LIST блок присутствует."""
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    assert "<!-- GEN:XFAIL_LIST:START -->" in arch
    assert "<!-- GEN:XFAIL_LIST:END -->" in arch


def test_arch_snapshot_domain_signals_block_present():
    """GEN:DOMAIN_SIGNALS блок присутствует. Содержимое зависит от наличия данных в БД.

    Если БД пустая — блок сигнализирует «миграция pending», это allowed.
    Если БД заполнена — должна быть таблица с metric/label/r/threshold_pct.
    """
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    assert "<!-- GEN:DOMAIN_SIGNALS:START -->" in arch
    assert "<!-- GEN:DOMAIN_SIGNALS:END -->" in arch
    start = arch.find("<!-- GEN:DOMAIN_SIGNALS:START -->")
    end = arch.find("<!-- GEN:DOMAIN_SIGNALS:END -->")
    block = arch[start:end]
    # Должен присутствовать каждый из 5 доменов.
    for domain in ("vagal_activation", "stress", "activity", "sleep", "nutrition"):
        assert domain in block, f"Домен {domain} отсутствует в DOMAIN_SIGNALS блоке"


def test_arch_snapshot_external_integrations_block_present():
    """GEN:EXTERNAL_INTEGRATIONS блок присутствует и содержит оба Tailscale endpoint'а —
    с плейсхолдером имени хоста (с 2026-09-23 настоящее имя живёт только в private/infra.yaml)."""
    arch = (ROOT / "ARCH_SNAPSHOT.md").read_text(encoding="utf-8")
    assert "<!-- GEN:EXTERNAL_INTEGRATIONS:START -->" in arch
    start = arch.find("<!-- GEN:EXTERNAL_INTEGRATIONS:START -->")
    end = arch.find("<!-- GEN:EXTERNAL_INTEGRATIONS:END -->")
    block = arch[start:end]
    assert "<tailnet_hostname>" in block, "Tailscale endpoint отсутствует в EXTERNAL_INTEGRATIONS"
    import infra_config
    assert infra_config.FUNNEL_URL.split("//")[1] not in block, "настоящее имя хоста в публичном документе"
    # Оба endpoint'а — :443 (без порта в URL) и :10000.
    assert ":10000" in block, "Public Funnel :10000 endpoint отсутствует"


def _симлинк_на_источник(hook: Path, git_dir: Path, хвост: tuple[str, ...]) -> None:
    """Хук — симлинк на трекаемый источник ЭТОГО репозитория.

    Почему не сравнение с ROOT/scripts/... (правка 13.09.2026): каталог хуков
    ОДИН на все деревья, и симлинк законно указывает в ту копию, из которой бежал
    установщик, — обычно в главную. Сравнение с ROOT краснело при запуске из
    дерева нити, хотя хуки исправны. Утверждение не ослаблено: проверяются обе
    половины — тот самый файл по имени И принадлежность ЭТОМУ репозиторию.
    Симлинк в соседний клон прошёл бы проверку «по имени» и был бы ровно той
    поломкой, ради которой сторож заведён.
    """
    assert hook.exists(), f"{hook} не установлен — запусти scripts/install_hooks.sh"
    assert hook.is_symlink(), (
        f"{hook} — не symlink, а обычный файл: правки источника до гейта не "
        "доедут. Перезапусти scripts/install_hooks.sh")
    target = hook.resolve()
    assert target.parts[-len(хвост):] == хвост, (
        f"{hook} указывает не на трекаемый источник: {target}")
    assert target.is_file(), f"симлинк ведёт в никуда: {target}"
    r = subprocess.run(["git", "-C", str(target.parent), "rev-parse", "--git-common-dir"],
                       capture_output=True, text=True, timeout=30)
    чей = Path(os.path.join(target.parent, r.stdout.strip())).resolve() if r.returncode == 0 else None
    assert чей == git_dir, (
        f"источник {target} лежит в ДРУГОМ репозитории ({чей}), "
        f"а хуки ставит {git_dir}")


@pytest.mark.owner_data
@pytest.mark.host_only
def test_git_hook_symlink_on_studio():
    """На Studio .git/hooks/pre-commit — symlink на scripts/git-hooks/pre-commit.

    На MacBook .git/ нет — skip. На Studio — должен быть установлен.
    """
    # Каталог git спрашиваем у git, а не собираем как ROOT/".git" (13.09.2026).
    # В дереве нити `.git` — ФАЙЛ-указатель: прежняя строка находила его
    # существующим, пропуск не срабатывал, и тест падал на отсутствии
    # `.git/hooks` — то есть краснел на исправном Studio, стоило запустить его
    # из worktree. Третий случай этого класса за сутки (install_hooks.sh,
    # thread_finish.sh). --git-common-dir, а не --git-dir: хуки живут в ОБЩЕМ
    # каталоге, приватный каталог дерева нити их не содержит.
    _r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                        capture_output=True, text=True, timeout=30)
    if _r.returncode != 0 or not _r.stdout.strip():
        pytest.skip("не git-дерево — symlink проверка нерелевантна")
    git_dir = Path(os.path.join(ROOT, _r.stdout.strip())).resolve()
    if not (git_dir / "hooks").is_dir():
        pytest.skip("каталога хуков нет — проверять нечего")
    git_hook = git_dir / "hooks" / "pre-commit"
    _симлинк_на_источник(git_hook, git_dir, ("scripts", "git-hooks", "pre-commit"))

    # ── Замок слияния (13.09.2026) ───────────────────────────────────────────
    # git проводит СЛИЯНИЕ через другой хук. Без него merge-коммит на Studio
    # прошёл бы мимо всех гейтов, а на MacBook — мимо прогона тестов; замерено
    # пробой 12.09 (tests/unit/test_merge_gates.py).
    # Здесь это ночной сторож: сам замок ставится установщиком на MacBook и
    # РАЗОВО руками на Studio (13.09). Разовая установка не наследуется — но
    # симлинк не устаревает, а его пропажу видит вот этот тест. Механизм
    # самовосстановления на Studio заведён долгом: BACKLOG → BL-STUDIO-HOOKS-1.
    merge_hook = git_dir / "hooks" / "pre-merge-commit"
    _симлинк_на_источник(merge_hook, git_dir, ("scripts", "git-hooks", "pre-commit"))

    # ── Самовосстановление набора (13.09.2026, BL-STUDIO-HOOKS-1 закрыт) ─────
    # Предыдущая редакция этого блока говорила «механизм самовосстановления
    # заведён долгом». Механизм построен: приём push зовёт scripts/install_hooks.sh
    # через тонкую обёртку, и НАБОР чинится сам. Поведение обёртки стережёт
    # tests/unit/test_studio_post_receive.py; здесь — единственное, чего тот
    # доказать не может: что сам бутстрап-симлинк на ЭТОЙ машине заведён.
    # Он ставится руками один раз и установщиком после; если исчезнет, Studio
    # тихо вернётся к состоянию «набор устаревает молча».
    # post-receive осмысленен ТОЛЬКО на принимающей машине. Признак берём
    # фактом, а не именем хоста: `receive.denyCurrentBranch=updateInstead`
    # задан ровно там, куда пушат (замер 13.09: на Studio задан, на MacBook
    # пусто). Без этой отсечки тест краснел на MacBook при каждом прогоне
    # затронутых — то есть в наборе, который агент видит чаще всего. Красный,
    # который всегда красный, перестают читать; ошибка моя же, того же дня.
    _recv = subprocess.run(["git", "-C", str(ROOT), "config", "--get",
                            "receive.denyCurrentBranch"],
                           capture_output=True, text=True, timeout=30)
    if _recv.stdout.strip() == "updateInstead":
        recv_hook = git_dir / "hooks" / "post-receive"
        _симлинк_на_источник(recv_hook, git_dir,
                             ("scripts", "git-hooks", "studio-post-receive"))

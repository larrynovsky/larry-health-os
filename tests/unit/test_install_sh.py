"""scripts/install.sh: отказывает до изменений и едет в выпуск.

Полный путь (установка, повтор, поддельные ключи) исполняет .github/workflows/tutorial.yml на чистой
Ubuntu; здесь — то, что краснеет без Докера и сети на любой машине."""
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "scripts" / "install.sh"


def _check(dir_: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SH), "--check", "--dir", str(dir_)], capture_output=True,
                          text=True, timeout=120, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(dir_.parent), "LANG": "C"})


def test_foreign_directory_is_refused_and_untouched(tmp_path):
    """Чужой непустой каталог → код 1 и ни одного нового файла. Падение = скрипт пишет в чужое."""
    d = tmp_path / "other-app"
    d.mkdir()
    (d / "notes.txt").write_text("x")
    r = _check(d)
    assert r.returncode == 1
    assert "not empty" in r.stdout
    assert sorted(p.name for p in d.iterdir()) == ["notes.txt"]


def test_leftovers_of_interrupted_download_are_not_foreign(tmp_path):
    """Обрыв скачивания оставил compose.yaml.part → повтор не должен считать каталог чужим."""
    d = tmp_path / "health-docker"
    d.mkdir()
    (d / "compose.yaml.part").write_text("")
    assert "not empty" not in _check(d).stdout


def test_install_sh_is_a_release_asset():
    """Урок качает install.sh из выпуска: release.yml кладёт его в dist, public_mirror его требует."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("public_mirror", ROOT / "scripts" / "public_mirror.py")
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    assert "install.sh" in pm.RELEASE_ASSETS
    assert re.search(r"cp scripts/install\.sh dist/install\.sh", (ROOT / ".github/workflows/release.yml").read_text())


def test_unknown_provider_is_refused_before_any_change(tmp_path):
    d = tmp_path / "health-docker"
    r = subprocess.run(["bash", str(SH), "--check", "--provider", "acme", "--dir", str(d)], capture_output=True,
                       text=True, timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C"})
    assert r.returncode == 1 and "Unknown model provider" in r.stderr
    assert not d.exists()


def test_installer_providers_match_the_profiles():
    """Установщик проверяет ключ по models_url профиля и кладёт его в key_file профиля: разойдутся —
    ключ проверят у одного хоста, а система пойдёт к другому или не найдёт файл."""
    import json
    prof = {k: v for k, v in json.loads((ROOT / "methodology" / "llm_providers.json").read_text()).items()
            if not k.startswith("_")}
    text = SH.read_text()
    check = text[text.index("key_check() {"):text.index("keep_unverified() {")]
    urls = dict(re.findall(r'^\s+(\w+)\) cfg=.*url = "([^"]+)"', check, re.M))
    files = dict(re.findall(r'^\s+(\w+)\)\s+KF=(\w+);', text, re.M))
    assert urls == {n: p["models_url"] for n, p in prof.items()}
    assert files == {n: p["key_file"] for n, p in prof.items()}
    # Хосты и файлы ключей — у ВСЕХ профилей (уже выбравший DeepSeek обновляется); что
    # предлагается к выбору — test_installer_offers_exactly_the_offered_providers ниже.


# ── выбор поставщика моделей (нить provider-choice, 03.10) ───────────────────────────────────
def _offered() -> set[str]:
    import json
    prof = json.loads((ROOT / "methodology" / "llm_providers.json").read_text(encoding="utf-8"))
    return {p for p, v in prof.items() if not p.startswith("_") and v.get("offered", True) is not False}


def _choose_provider_body() -> str:
    text = SH.read_text(encoding="utf-8")
    m = re.search(r"^choose_provider\(\) \{.*?^\}$", text, re.M | re.S)
    assert m, "в install.sh нет функции choose_provider"
    return m.group(0)


def _ask(answers: str) -> subprocess.CompletedProcess:
    """Исполняет НАСТОЯЩЕЕ тело choose_provider из install.sh (а не копию) с ответами на stdin."""
    prelude = ('RU=0; WARNS=0\nsay() { printf \'%s\\n\' "$2"; }\nwarn() { WARNS=$((WARNS+1)); say "$1" "$2"; }\n'
               'die() { say "$1" "$2" >&2; exit 1; }\n')
    script = prelude + _choose_provider_body() + '\nchoose_provider\necho "CHOSEN=$PROVIDER"\n'
    return subprocess.run(["bash", "-c", script], input=answers, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("answers,chosen", [("\n", "anthropic"), ("1\n", "anthropic"), ("2\n", "openai"),
                                            ("3\n", "gemini"), ("x\n4\n2\n", "openai")])
def test_provider_question_maps_answers(answers, chosen):
    """Enter = Anthropic; мусор и «4» переспрашиваются, а не выбирают молча."""
    r = _ask(answers)
    assert r.returncode == 0, r.stderr
    assert f"CHOSEN={chosen}" in r.stdout
    assert r.stdout.count("type 1, 2 or 3") == answers.count("\n") - 1


def test_provider_question_without_answer_fails_loudly():
    r = _ask("")
    assert r.returncode != 0 and "No answer" in r.stderr


def test_installer_offers_exactly_the_offered_providers():
    """Установщик скачивается отдельно от образа и профиля не видит — его список второй дом.
    Сверка: варианты вопроса и принимаемые --provider = профили без "offered": false.
    Красный, если DeepSeek вернётся в вопрос или новый поставщик появится только в одном доме."""
    in_question = set(re.findall(r"PROVIDER=(\w+); return", _choose_provider_body()))
    accepted = re.search(r'case "\$\{PROVIDER:-anthropic\}" in\n\s*([a-z|]+)\) ;;', SH.read_text(encoding="utf-8"))
    assert accepted, "в install.sh не найден список принимаемых --provider"
    assert in_question == set(accepted.group(1).split("|")) == _offered(), (in_question, accepted.group(1), _offered())


def test_explicit_deepseek_is_refused_before_any_change(tmp_path):
    d = tmp_path / "health-docker"
    r = subprocess.run(["bash", str(SH), "--non-interactive", "--provider", "deepseek", "--dir", str(d)],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path), "LANG": "C"})
    assert r.returncode != 0 and "DeepSeek is not offered" in (r.stdout + r.stderr)
    assert not d.exists(), "отказ должен случиться до создания каталога"


def test_fresh_install_asks_and_update_reads_env():
    """Вопрос — только у новой установки с вопросами (ENV_FRESH, INTERACTIVE); повтор читает .env."""
    text = SH.read_text(encoding="utf-8")
    assert 'if [ "$ENV_FRESH" = 1 ] && [ "$INTERACTIVE" = 1 ]; then choose_provider' in text
    assert text.index("ENV_FRESH=1") < text.index("choose_provider\n  else")


# ── ключ не того поставщика (нить lab-intake-retry, 05.10) ───────────────────────────────────
# Отчёт с Windows: выбран Anthropic, вставлен ключ OpenAI — «Anthropic не принял ключ», и всё;
# что поставщика можно сменить, установщик не говорил нигде.

def _key_vendor(key: str) -> str:
    text = SH.read_text(encoding="utf-8")
    m = re.search(r"^key_vendor\(\) \{.*?^\}$", text, re.M | re.S)
    assert m, "в install.sh нет функции key_vendor"
    r = subprocess.run(["bash", "-c", m.group(0) + '\nkey_vendor "$1"', "_", key],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


@pytest.mark.parametrize("key,vendor", [("sk-ant-api03-xxxx", "anthropic"), ("sk-proj-xxxx", "openai"),
                                        ("sk-xxxxxxxx", "openai"), ("AIzaSyxxxx", "gemini"), ("xyz", "")])
def test_key_vendor_by_prefix(key, vendor):
    """Мутации: спутать ключ Anthropic с OpenAI (оба начинаются с sk-); угадать по мусору."""
    assert _key_vendor(key) == vendor


def _key_loop(provider: str, answers: str, interactive: int = 1, env_key: str = "") -> subprocess.CompletedProcess:
    """Исполняет НАСТОЯЩИЙ цикл ввода ключа из install.sh (от key_vars до его done) на заглушках
    сети и секретов: ключ проверяется «ok», сохранённое печатается."""
    text = SH.read_text(encoding="utf-8")
    start = text.index("key_vars() {")
    end = text.index("\ndone\n", text.index('while ! has_secret "$KF"; do')) + len("\ndone\n")
    prelude = ('RU=0; VERIFY=1; INTERACTIVE=%d; PROVIDER=%s; ENV_AK=%s; ENV_LK=""\n'
               'say() { printf \'%%s\\n\' "$2" >&2; }\nwarn() { say "$1" "$2"; }\nok() { say "$1" "$2"; }\n'
               'die() { say "$1" "$2"; exit 1; }\nSAVED=""\n'
               'has_secret() { [ -n "$(eval echo \\${S_$1:-})" ]; }\n'
               'put_secret() { eval "S_$1=1"; echo "PUT=$1"; }\n'
               'save_provider() { SAVED=$PROVIDER; }\nkey_check() { echo ok; }\n'
               'ask_secret() { local v="$1"; [ -n "$v" ] || read -r v; printf \'%%s\' "$v"; }\n'
               % (interactive, provider, env_key or '""'))
    script = prelude + text[start:end] + 'echo "PROVIDER=$PROVIDER SAVED=$SAVED"\n'
    return subprocess.run(["bash", "-c", script], input=answers, capture_output=True, text=True, timeout=30)


def test_wrong_vendor_key_switches_provider_on_yes():
    """Мутации: не переключать; переключить, но сохранить ключ под старым именем файла."""
    r = _key_loop("anthropic", "sk-proj-abc\n\n")
    assert "PUT=openai_key" in r.stdout and "PROVIDER=openai SAVED=openai" in r.stdout, r.stderr
    assert "PUT=anthropic_key" not in r.stdout


def test_wrong_vendor_key_refused_on_no_and_named_without_questions():
    """«Нет» — ключ спрашивается снова под выбранным поставщиком; без вопросов — отказ с именем флага."""
    r = _key_loop("anthropic", "sk-proj-abc\nn\nsk-ant-xyz\n")
    assert "PUT=anthropic_key" in r.stdout and "PROVIDER=anthropic SAVED=" in r.stdout, r.stderr
    r = _key_loop("anthropic", "", interactive=0, env_key="sk-proj-abc")
    assert r.returncode == 1 and "--provider openai" in r.stderr and "PUT=" not in r.stdout

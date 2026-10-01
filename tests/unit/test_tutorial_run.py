"""Прогонщик урока: исполняет только помеченные блоки, подменяет только выпуск и ключи."""
from pathlib import Path

from scripts import tutorial_run as tr

ROOT = Path(__file__).resolve().parents[2]

PAGE = "шаг\n\n```bash\nbrew install colima\n```\n\n" + tr.MARK + "\n```bash\n" \
       "curl -fsSL -o compose.yaml " + tr.RELEASE_URL + "compose.yaml\n" \
       "printf '%s' 'ТОКЕН_ОТ_BOTFATHER' > telegram_token\n```\n"


def test_only_marked_blocks_run_with_two_substitutions():
    got = tr.blocks(PAGE)
    assert len(got) == 1 and "brew" not in got[0]          # непомеченный Мак-блок не исполняется
    code = tr.substituted(got[0], "/tmp/dist")
    assert "file:///tmp/dist/compose.yaml" in code and tr.RELEASE_URL not in code
    assert "'123456:ci-fake-token'" in code and "ТОКЕН_ОТ_BOTFATHER" not in code


def test_both_languages_mark_the_same_blocks():
    ru = tr.blocks((ROOT / "docs/tutorials/first_install.md").read_text(encoding="utf-8"))
    en = tr.blocks((ROOT / "docs/tutorials/first_install.en.md").read_text(encoding="utf-8"))
    assert len(ru) >= 4 and len(ru) == len(en)
    # Команды одинаковы, кроме заглушек ключей: разойтись переводу в команде урок не даёт.
    assert [tr.substituted(b, "D") for b in ru] == [tr.substituted(b, "D") for b in en]


def test_list_runs_nothing_and_succeeds_on_live_page(capsys):
    assert tr.main([str(ROOT / "docs/tutorials/first_install.md"), "--list"]) == 0
    assert "── блок 1/" in capsys.readouterr().out


def test_release_mode_keeps_public_address_and_list_is_honest(capsys):
    """--release не подменяет адрес выпуска (прогон как у новичка, 01.10), а --list не
    говорит «исполнены». Падение: анонимный прогон снова негде сделать, либо показ блоков
    читается как их прогон."""
    import scripts.tutorial_run as tr
    page = (ROOT / "docs/tutorials/first_install.md").read_text(encoding="utf-8")
    first = tr.blocks(page)[0]
    assert tr.RELEASE_URL in tr.substituted(first, None)
    assert tr.main([str(ROOT / "docs/tutorials/first_install.md"), "--list"]) == 0
    out = capsys.readouterr().out
    assert "исполнены" not in out and "не исполнялись" in out

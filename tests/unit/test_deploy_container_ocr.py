"""Сборка образа контейнера берёт языки OCR из настроек тенанта и проверяет их в образе.

Замер 30.09: первый образ пилота собран без OCR_LANGS — у владельца, переключённого в контейнер,
в образе не хватало языка тенанта, документы на нём распознавались мусором (молча, warning в журнале).
Скрипт исполняется только на Studio с Docker; здесь — сторож формы, живой оракул — строка
«языки OCR тенанта» и прошедший шаг «языки OCR в образе» в health-container-deploy.log.
"""
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "deploy_container.sh"


def test_build_passes_tenant_languages_and_checks_the_image():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "key='ocr.languages'" in s, "языки не читаются из настроек тенанта"
    assert '--build-arg "OCR_LANGS=${LANGS//+/ }"' in s
    assert '${OCR_ARGS[@]+"${OCR_ARGS[@]}"}' in s, "аргумент не доходит до docker build"
    build = s.index("build -q -f docker/Dockerfile")
    check = s.index("--entrypoint tesseract")
    assert check > build, "проверка языков должна идти после сборки"
    assert 'grep -qx "$l" || exit 1' in s, "нехватка языка в образе обязана быть отказом"


def test_one_deploy_at_a_time_and_lock_before_head():
    """Замер 30.09: два параллельных compose up положили службы владельца. Замок берётся ДО чтения
    HEAD (ждущий строит последний коммит) и снимается только своим владельцем. Живой оракул —
    строка «жду предыдущий деплой» без FAIL compose up при закрытии нити."""
    s = SCRIPT.read_text(encoding="utf-8")
    lock = s.index('mkdir "$LOCK"')
    assert lock < s.index("git rev-parse --short HEAD"), "замок обязан браться до чтения HEAD"
    assert '[ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] || exit 1' in s, "без замка деплой не идёт"
    assert '= "$$" ] && rm -rf "$LOCK"' in s.split("on_exit() {")[1].split("}")[0], "снимает только владелец"


def test_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_old_images_are_removed_and_disk_is_watched():
    """Замер 01.10: 109 образов по одному на коммит заполнили диск Докера на 100% — база владельца
    отвечала «disk I/O error». Уборка — ПОСЛЕ сверки, что службы на новом образе; образ этого
    коммита не трогается; заполненный диск — тревога (notify.fault)."""
    s = SCRIPT.read_text(encoding="utf-8")
    done, clean = s.index("DONE=1"), s.index("image ls health-os")
    assert done < clean, "уборка до сверки нового образа могла бы снести рабочий"
    assert '[ "$t" = "$SHA" ] || [ "$t" = "local" ] ||' in s, "образ этого коммита обязан остаться"
    assert '"$USED" -ge 85' in s and "notify.fault(\"диск Докера" in s

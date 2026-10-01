"""Оракул guard'а ветки в post-commit (2026-09-11).

Что стережёт: коммит НЕ в main не должен вызывать ни push, ни рестарт служб.
Иначе переход на worktree-на-задачу даёт два молчаливых ложных следствия —
на Studio уезжает чужой main вместо работы нити, а run_checks зеленеет на
главной копии вместо дерева нити (SCRIPT_DIR считается от пути хука, общего
для всех worktree).

Почему поведенческий, а не grep по тексту: guard можно снять, переставить
ПОСЛЕ push или сломать условие — текстовая проверка это пропустит. Здесь хук
реально исполняется с подменёнными git и ssh в PATH, и судится факт вызова.

Позитивный контроль обязателен: на main push должен быть вызван. Без него
тест проходил бы и на guard'е, который выходит всегда.

Граница честно: заглушки подменяют git и ssh, поэтому тест судит РЕШЕНИЕ хука,
а не поведение настоящего git. Что `git push studio main` действительно
отправляет main, он не доказывает.
"""
import collections
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HOOK_SRC = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks" / "post-commit-macbook"

# `SLOW_DEPLOY` — сколько секунд «работает» заглушка push. Ноль по умолчанию:
# тестам про guard задержка не нужна, она нужна только оракулу синхронности,
# которому без неё нечего различать (см. test_деплой_синхронен_и_громок).
FAKE_GIT = """#!/bin/bash
case "$1 $2" in
  "log -1")             echo "${FAKE_MSG:-test commit}"; exit 0;;
  "rev-parse --short")  echo "deadbee"; exit 0;;
  "rev-parse --abbrev-ref") echo "$FAKE_BRANCH"; exit 0;;
  "rev-parse -q")       echo "$FAKE_DEPLOYED"; exit 0;;
  "diff --name-only")   printf "%b" "$FAKE_CHANGED"; exit 0;;
  "remote get-url")     if [[ -n "$FAKE_NO_REMOTE" ]]; then exit 2; fi
                        echo "user@studio.example:/repo"; exit 0;;
  "push studio")        sleep "${SLOW_DEPLOY:-0}"
                        echo "ЗАГЛУШКА-PUSH-ВЫВОД"
                        echo "push" >> "$MARKER"
                        exit "${FAKE_PUSH_RC:-0}";;
esac
exit 0
"""

# Заглушка ssh НАРОЧНО печатает свою строку ДО рамки: ровно так ведут себя
# баннер хоста и MOTD. До правки F6 эта строка становилась вердиктом
# «Studio dirty» и отменяла эскалацию. Теперь она обязана быть безвредной —
# шум снаружи рамки вердикта не меняет.
FAKE_SSH = """#!/bin/bash
echo "ЗАГЛУШКА-SSH-ВЫВОД"
echo "ssh" >> "$MARKER"
echo "$*" >> "$MARKER.cmds"
if [[ "$*" == *__RUNTIME__* ]]; then echo "__RUNTIME__${FAKE_OWNER_RUNTIME}"; fi
# Сбой записывается через notify.fault на Studio, без сообщения владельцу.
# Отмечаем её ОТДЕЛЬНО от прочих вызовов ssh: без этой строки оракулы ниже
# судили бы текст причины, а не факт тревоги (ревью 12.09, О-13).
if [[ "$*" == *notify.fault* ]]; then echo "FAULT" >> "$MARKER"; fi
if [[ "$*" == *__PROBE_BEGIN__* ]]; then
  if [[ -n "$FAKE_SSH_DEAD" ]]; then exit 255; fi
  echo "__PROBE_BEGIN__"
  if [[ -n "$FAKE_DIRTY" ]]; then echo "$FAKE_DIRTY"; fi
  echo "__PROBE_END__"
fi
exit 0
"""


HookRun = collections.namedtuple("HookRun", "marker stdout log cmds", defaults=("",))


def _run_hook(branch, timeout=30.0, slow=0, msg=None, push_rc=0,
              dirty="", ssh_dead=False, norepo=False, no_remote=False, owner_rt="",
              deployed="", changed=""):
    """Исполняет хук в изолированном дереве. Возвращает (маркер, stdout хука).

    Маркер читается СРАЗУ после возврата хука, без ожидания. Так и задумано:
    с 12.09 деплой синхронный, и «маркер ещё не появился» означает не гонку, а
    возврат фона — то есть регрессию (см. test_деплой_синхронен_и_громок).

    `slow` растягивает заглушку push. Нужен ровно одному тесту: при мгновенной
    заглушке фоновая подоболочка успевает записать маркер раньше, чем шелл
    завершится, и «синхронно» от «повезло» неотличимо.
    """
    tmp = Path(tempfile.mkdtemp(prefix="guard_test_"))
    try:
        (tmp / "scripts" / "git-hooks").mkdir(parents=True)
        (tmp / "logs").mkdir()
        (tmp / "run_checks.sh").write_text("exit 0\n")
        hook = tmp / "scripts" / "git-hooks" / "post-commit-macbook"
        shutil.copy(HOOK_SRC, hook)
        hook.chmod(0o755)

        bindir = tmp / "bin"
        bindir.mkdir()
        for name, body in (("git", FAKE_GIT), ("ssh", FAKE_SSH)):
            p = bindir / name
            p.write_text(body)
            p.chmod(0o755)

        marker = tmp / "marker.txt"
        env = dict(os.environ)
        env.update(PATH=f"{bindir}:{env['PATH']}", FAKE_BRANCH=branch,
                   MARKER=str(marker), SLOW_DEPLOY=str(slow),
                   FAKE_MSG=msg or "test commit", FAKE_PUSH_RC=str(push_rc),
                   # NOREPO — не отдельная механика заглушки, а ровно то, что
                   # напечатал бы настоящий Studio без ~/health_scripts.
                   FAKE_DIRTY="__PROBE_NOREPO__" if norepo else dirty,
                   FAKE_SSH_DEAD="1" if ssh_dead else "",
                   FAKE_NO_REMOTE="1" if no_remote else "",
                   FAKE_OWNER_RUNTIME=owner_rt,
                   FAKE_DEPLOYED=deployed, FAKE_CHANGED=changed,
                   # Пауза ретрая — 0: тест судит КЛАССИФИКАЦИЮ, а не то, что
                   # bash умеет спать. 25 с × 3 теста = 75 с в каждом pre-commit
                   # за факт, который не проверяется.
                   DEPLOY_RETRY_SLEEP="0")

        res = subprocess.run(["bash", str(hook)], env=env, cwd=str(tmp),
                             capture_output=True, text=True, timeout=timeout)

        log = tmp / "logs" / "deploy.log"
        cmds = Path(str(marker) + ".cmds")
        return HookRun(marker.read_text() if marker.exists() else "",
                       (res.stdout or "") + (res.stderr or ""),
                       log.read_text() if log.exists() else "",
                       cmds.read_text() if cmds.exists() else "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_рабочая_ветка_не_деплоит():
    """Главный инвариант: коммит в ветке нити не трогает Studio."""
    out = _run_hook("thread/compass-fix").marker
    assert "push" not in out, f"guard пропустил деплой из рабочей ветки: {out!r}"
    assert "ssh" not in out, f"guard пропустил рестарт служб из рабочей ветки: {out!r}"


def test_detached_head_не_деплоит():
    """git rev-parse --abbrev-ref в detached отдаёт 'HEAD' — fail-closed."""
    out = _run_hook("HEAD").marker
    assert "push" not in out, f"detached HEAD задеплоил: {out!r}"


def test_нечитаемая_ветка_не_деплоит():
    """Пустой ответ (сломанный git, повреждённый HEAD) тоже не деплоит."""
    out = _run_hook("").marker
    assert "push" not in out, f"неизвестная ветка задеплоила: {out!r}"


def test_main_деплоит():
    """Позитивный контроль. Без него guard, выходящий всегда, прошёл бы тесты выше."""
    out = _run_hook("main").marker
    assert "push" in out, f"main не задеплоил — guard сломал штатный путь: {out!r}"


def test_без_remote_studio_деплой_отказывает_громко():
    """Адрес Studio берётся из git-remote (pub-prep 2026-09-23, литерала в хуке нет).
    Нет remote → не молчаливый пропуск, а отказ с причиной в логе и на stderr."""
    r = _run_hook("main", no_remote=True)
    assert "push" not in r.marker and "ssh" not in r.marker
    assert "нет git-remote 'studio'" in r.stdout and "нет git-remote 'studio'" in r.log


def test_деплой_синхронен_и_громок():
    """Деплой обязан закончиться ДО возврата хука И оставить вывод на stdout.

    Почему это инвариант, а не вкус. Фоновая подоболочка (`) ... &`) умирает
    вместе с шеллом, а агент коммитит через мост, где шелл завершается сразу
    после `git commit`. 11.09 так молча потерялись два коммита: на Studio они
    не приехали, и в deploy.log не осталось ни строки — даже следа потери.

    ПОЧЕМУ ТЕСТ ПЕРЕПИСАН (ревью 12.09, F5). Прежняя редакция звала
    `_run_hook("main")` и проверяла `"push" in out` — то есть делала ТО ЖЕ
    САМОЕ, что `test_main_деплоит` строкой выше. Два теста, одна величина:
    про синхронность он не мог сказать ничего, чего не сказал позитивный
    контроль, а имя обещало отдельное утверждение. Хуже: при мгновенной
    заглушке фоновая подоболочка успевала записать маркер раньше, чем шелл
    завершался, так что даже возврат `&` мог остаться зелёным.

    Что меряется теперь — две величины, обе названы:
      1. СИНХРОННОСТЬ: заглушка push спит `slow` секунд. Фоновая подоболочка
         к возврату хука записать маркер не успеет; синхронная — обязана.
         Задержка превращает гонку в решённый исход.
      2. ГРОМКОСТЬ: вывод деплоя обязан дойти до stdout хука. Это про правку
         12.09 `) ... & >> log` → `) 2>&1 | tee -a log`: раньше вывод уходил
         только в файл, и агент, коммитящий через мост, не видел ни ошибки
         деплоя, ни успеха. Маркер про это не знает — он пишется в файл в
         обоих случаях.

    Граница честно: тест судит момент записи маркера и путь вывода, а не факт
    доставки кода на Studio — git и ssh здесь заглушки.
    """
    slow = 2
    started = time.monotonic()
    run = _run_hook("main", slow=slow)
    marker, stdout = run.marker, run.stdout
    elapsed = time.monotonic() - started

    assert elapsed >= slow, (
        f"хук вернулся за {elapsed:.1f} с при заглушке на {slow} с — он не стал "
        "ждать деплой, значит подоболочка снова фоновая")
    assert "push" in marker, (
        "деплой не завершился к возврату хука — подоболочка снова фоновая "
        f"(маркер: {marker!r})")
    assert "ЗАГЛУШКА-PUSH-ВЫВОД" in stdout, (
        "вывод деплоя не дошёл до stdout хука — вернулось перенаправление "
        "только в файл, и тот, кто коммитит через мост, снова деплоит вслепую\n"
        f"stdout: {stdout[-800:]!r}")


def test_бэкапный_коммит_пропускается_но_не_молча():
    """Пропуск обязан назвать причину — «пропущено» ≠ «не работало» (ревью F4).

    Бэкапный коммит деплоить не надо: backup.sh пушит сам. Но до 12.09 этот
    выход был БЕЗ единой строки в лог, и в deploy.log он выглядел ровно так же,
    как хук, который не запускали. Различать их приходилось по памяти читающего.
    """
    run = _run_hook("main", msg="auto: daily backup 2026-09-12")
    assert "push" not in run.marker, f"бэкапный коммит всё-таки задеплоил: {run.marker!r}"
    assert "бэкап" in run.log, (
        "пропуск по бэкапу ничего не написал в deploy.log — молчание снова "
        f"значит два разных события:\n{run.log!r}")


def test_каждый_запуск_оставляет_след():
    """Тишина в deploy.log обязана значить ровно одно: хук не звали.

    Не гипотеза: 11.09 симлинки хуков указывали в удалённый /tmp/install_hooks_test_*,
    и семь коммитов прошли мимо всех гейтов, не оставив следа. Отличить «хука не
    было» от «хук отработал и промолчал» было нечем.

    Проверяются все три исхода разом — деплой, пропуск по ветке, пропуск по
    бэкапу: любой из них обязан оставить строку. Тест на одном исходе пропустил
    бы регрессию в двух других.
    """
    for name, run in (
        ("деплой из main", _run_hook("main")),
        ("пропуск по ветке", _run_hook("thread/x")),
        ("пропуск по бэкапу", _run_hook("main", msg="auto: daily backup")),
    ):
        assert run.log.strip(), (
            f"исход «{name}» не оставил в deploy.log ни строки — пустой лог "
            "перестал значить «хук не запускался»")
        assert "post-commit запущен" in run.log, (
            f"исход «{name}»: нет отметки запуска, по которой отличают «хука не "
            f"было» от «хук отработал»:\n{run.log!r}")


# ── Классификатор причины упавшего деплоя (ревью F6) ────────────────────────
# §13: человека тревожат ТОЛЬКО когда авто-пути нет. Значит у классификатора две
# ошибки с РАЗНОЙ ценой, и считать их надо по отдельности:
#   · ложная тревога — минута внимания владельца;
#   · пропущенная    — работа копится недеплоенной, Studio зеленеет на старом
#                      коде, и никто не знает. Дороже на порядки.
# Поэтому единственная не-эскалирующая ветка («dirty») обязана срабатывать лишь
# на доказанном dirty, а не на любой непустой строке из канала.

# ПОЧЕМУ ЗДЕСЬ СУДИТСЯ МАРКЕР, А НЕ ТЕКСТ ПРИЧИНЫ (ревью 12.09, О-13).
#
# Первая редакция этих трёх тестов читала `run.stdout` и искала подстроки
# «разошлась» / «dirty» / «недостижим». Это ТЕКСТ объяснения, а не факт тревоги.
# Внешний ревьюер снял эскалацию целиком (`if $escalate` → `if false`) — все
# десять тестов файла остались ЗЕЛЁНЫМИ. Тем же зелёным остались мутации
# «эскалировать на каждом dirty» и «не эскалировать при расхождении».
#
# Это третий раз в одной нити, когда я померил величину, СОСЕДНЮЮ с заявленной,
# и второй раз внутри починки, объявленной лекарством от этого класса. Вывод для
# следующего раза короче любого объяснения: если утверждение говорит «тревога»,
# оракул обязан наблюдать ТРЕВОГУ. Наблюдаемая величина лежала в той же
# оснастке, в соседнем поле namedtuple.
#
# Тревога уходит единственным способом — `notify.fault` через ssh; заглушка ssh
# отмечает запись в маркере словом FAULT. Текст причины проверяется тоже, но вторым
# утверждением: причина без тревоги бесполезна, тревога без причины — вредна.

def _исход(run):
    """(была ли тревога владельцу, текст причины)."""
    return ("FAULT" in run.marker, run.stdout)


def test_шум_в_канале_не_отменяет_эскалацию():
    """Главный инвариант F6: строка мимо рамки не превращается в «dirty».

    Заглушка ssh печатает «ЗАГЛУШКА-SSH-ВЫВОД» до рамки — так же ведут себя
    баннер хоста и MOTD. До правки классификатор читал сырой stdout, эта строка
    становилась вердиктом «Studio dirty», и тревога молча не уходила.
    """
    тревога, out = _исход(_run_hook("main", push_rc=1, dirty=""))
    assert тревога, (
        "деплой упал стойко, авто-пути нет — а тревога владельцу НЕ ушла. "
        f"Причина, которую назвал хук:\n{out[-1500:]}")
    assert "разошлась" in out, (
        "тревога ушла, но причина названа не та — владелец получит указание "
        f"чинить не то:\n{out[-1500:]}")


def test_настоящий_dirty_не_эскалирует():
    """Обратная сторона: без неё сошёл бы классификатор, тревожащий ВСЕГДА.

    §13: dirty разблокирует uncommitted_watchdog сам в течение часа. Тревога
    здесь — ложная, и её цена — приучить владельца не смотреть на тревоги.
    """
    тревога, out = _исход(_run_hook("main", push_rc=1, dirty=" M health_db.py"))
    assert not тревога, (
        "ложная тревога на обычном auto-stash — §13 требует молчать, когда "
        f"авто-путь есть:\n{out[-1500:]}")
    assert "dirty" in out, f"настоящий dirty не распознан:\n{out[-1500:]}"


def test_мёртвый_ssh_эскалирует():
    """Рамка не доехала — доказать наличие авто-пути нечем, значит тревога."""
    тревога, out = _исход(_run_hook("main", push_rc=1, ssh_dead=True))
    assert тревога, (
        f"оборванный ssh не привёл к тревоге:\n{out[-1500:]}")
    assert "недостижим" in out, f"причина названа не та:\n{out[-1500:]}"


def test_нет_каталога_на_studio_эскалирует():
    """Четвёртый исход классификатора (ревью 12.09, О-16).

    F6 завёл ветку `__PROBE_NOREPO__`, но заглушка её не печатала и теста не
    было. Порядок веток таков, что промах падает в ЕДИНСТВЕННУЮ не эскалирующую:
    не совпало с NOREPO → строка непустая → «Studio dirty, watchdog разберётся».
    То есть исчезновение каталога на Studio читалось бы как штатное событие.
    Проверено мутацией ревьюера: подмена литерала оставляла все тесты зелёными.
    """
    тревога, out = _исход(_run_hook("main", push_rc=1, norepo=True))
    assert тревога, (
        f"деплою некуда ехать, а тревоги нет:\n{out[-1500:]}")
    assert "нет ~/health_scripts" in out, (
        "исход распознан не как отсутствие каталога — скорее всего провалился "
        f"в ветку dirty, которая молчит:\n{out[-1500:]}")


def test_пропуск_по_ветке_называет_причину():
    """Вторая половина утверждения из `3ff0e42` (ревью 12.09, О-17).

    Коммит обещал «каждый пропуск называет причину», но оракул был только у
    пропуска по бэкапу. Строку про ветку не стерёг никто — удаление `echo`
    оставляло весь файл зелёным (проверено ревьюером).

    Цена растёт: вся нить готовит worktree-на-задачу, после которого пропуск по
    ветке станет САМЫМ частым исходом хука. В логе осталась бы отметка запуска и
    тишина — «пропустил по ветке» от «умер сразу после отметки» не отличить.
    """
    run = _run_hook("thread/compass-fix")
    assert "push" not in run.marker, "пропуск по ветке всё-таки задеплоил"
    assert "ветка" in run.log, (
        "пропуск по ветке не назвал причину в deploy.log — остаётся отметка "
        f"запуска и тишина:\n{run.log!r}")


if __name__ == "__main__":
    test_рабочая_ветка_не_деплоит()
    test_detached_head_не_деплоит()
    test_нечитаемая_ветка_не_деплоит()
    test_main_деплоит()
    test_деплой_синхронен_и_громок()
    test_бэкапный_коммит_пропускается_но_не_молча()
    test_каждый_запуск_оставляет_след()
    test_шум_в_канале_не_отменяет_эскалацию()
    test_настоящий_dirty_не_эскалирует()
    test_мёртвый_ssh_эскалирует()
    test_нет_каталога_на_studio_эскалирует()
    test_пропуск_по_ветке_называет_причину()
    print("OK: guard ветки держится, штатный деплой из main цел")


_LOAD_OWNER_BOT = "launchctl load ~/Library/LaunchAgents/com.larry.health.bot.plist"


def test_владелец_в_контейнере_нативный_бот_не_поднимается():
    """Пилот волны Б (docker-install, этап 11): плисты владельца остаются для отката, и `launchctl load`
    из хука поднял бы второго бота на том же токене поверх замороженной базы. В контейнере — деплой
    образа скриптом на Studio; партнёр перезапускается как прежде."""
    r = _run_hook("main", owner_rt="container")
    assert "scripts/deploy_container.sh" in r.cmds, r.cmds
    assert _LOAD_OWNER_BOT not in r.cmds, r.cmds
    assert "com.larry.health.bot.partner" in r.cmds


def test_владелец_нативно_бот_перезапускается_как_раньше():
    r = _run_hook("main")                      # метки нет — нативная установка
    assert _LOAD_OWNER_BOT in r.cmds, r.cmds
    assert "deploy_container.sh" not in r.cmds


# ── Рестарт нативных служб по делу (01.10, нить finish-tails) ────────────────
# Диапазон «что уже на Studio..HEAD» берётся из remote-tracking ref ДО пуша. Только
# документы/тесты/журналы → бот партнёра не перезапускается; что-то ещё → перезапускается;
# прежний деплой неизвестен → перезапускается (безопасная сторона).

def test_только_документы_не_перезапускают_бота_партнёра():
    r = _run_hook("main", deployed="abc1234", changed="docs/how-to/x.md\\nCHANGELOG.md\\ntests/unit/t.py\\n")
    assert "bot.partner" not in r.cmds, r.cmds
    assert "рестарт нативных служб не нужен" in r.stdout, r.stdout[-800:]


def test_код_в_диапазоне_перезапускает_бота_партнёра():
    r = _run_hook("main", deployed="abc1234", changed="docs/x.md\\njobs/scheduled.py\\n")
    assert "bot.partner" in r.cmds, r.cmds


def test_тексты_бота_не_считаются_документами():
    r = _run_hook("main", deployed="abc1234", changed="methodology/i18n/ru.yaml\\n")
    assert "bot.partner" in r.cmds, r.cmds


def test_неизвестный_прежний_деплой_перезапускает():
    r = _run_hook("main", deployed="", changed="docs/x.md\\n")
    assert "bot.partner" in r.cmds, r.cmds

"""release_notice (01.10): человек, поставивший систему из выпуска, узнаёт о новой версии — один раз
на версию. Падение = либо молчание о выпуске (никто не обновится), либо сообщение каждый день."""
import release_notice as rn


def _gh(tag, url=None):
    return lambda: {"tag_name": tag, "html_url": url or f"https://github.com/{rn.REPO}/releases/tag/{tag}"}


def test_новая_версия_сказана_один_раз(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    got = rn.pending("v0.1.1", fetch=_gh("v0.1.2"))
    assert got == {"current": "v0.1.1", "latest": "v0.1.2",
                   "url": f"https://github.com/{rn.REPO}/releases/tag/v0.1.2",
                   "command": rn.UPDATE_COMMAND}
    rn.mark_told("v0.1.2")
    assert rn.pending("v0.1.1", fetch=_gh("v0.1.2")) is None          # завтра — молчим
    assert rn.pending("v0.1.1", fetch=_gh("v0.2.0"))["latest"] == "v0.2.0"   # следующая — снова


def test_молчит_когда_нечего_сказать(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    assert rn.pending("v0.1.2", fetch=_gh("v0.1.2")) is None          # та же
    assert rn.pending("v0.1.10", fetch=_gh("v0.1.9")) is None         # сравнение числами, не строками
    assert rn.pending("", fetch=_gh("v9.0.0")) is None                # сборка не из выпуска
    assert rn.pending("v0.1.1", fetch=_gh("latest-junk")) is None     # мусорный тег из сети

    def boom():
        raise OSError("offline")
    assert rn.pending("v0.1.1", fetch=boom) is None                   # сеть — не тревога


def test_чужая_ссылка_из_сети_заменяется_своей(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    got = rn.pending("v0.1.1", fetch=_gh("v0.1.2", url="https://evil.example/x"))
    assert got["url"] == f"https://github.com/{rn.REPO}/releases/tag/v0.1.2"


def test_бриф_говорит_о_выпуске_и_помечает_после_отправки():
    """Проводка в доставке брифа: маркер ставится ПОСЛЕ send_message — иначе недошедшее
    уведомление больше не повторится."""
    import inspect
    from jobs import scheduled
    src = inspect.getsource(scheduled.send_morning_report)
    i_send = src.index('i18n.t("release.notice"')
    assert 0 < src.index("_rn.pending") < i_send < src.index("_rn.mark_told(")

"""Падение бота не печатает токен в bot_err.log, но слово InvalidToken остаётся (по нему урок и
install.sh узнают неверный токен). Падение теста = токен снова попадает в журнал человека."""
import pytest


def test_crash_trace_has_no_token(monkeypatch, capsys):
    import bot.main as bm
    secret = "123456:AAHsecretSECRETsecretSECRETsecret"
    monkeypatch.setattr(bm, "get_token", lambda: secret)

    class InvalidToken(Exception):
        pass

    def boom():
        raise InvalidToken(f"The token `{secret}` was rejected by the server.")

    with pytest.raises(SystemExit) as ex:
        bm.run(boom)
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert "InvalidToken" in err
    assert secret not in err and "<telegram_token>" in err

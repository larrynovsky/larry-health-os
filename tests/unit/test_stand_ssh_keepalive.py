"""Связь стенда со Studio не виснет на мёртвом канале (18.10, урок C-178).

17–18.10 закрытие нити дважды висело часами: сеть рвалась посреди прогона, ssh без keepalive
ждал ответа вечно. Механизм замерен на настоящей Studio заморозкой удалённой сессии: без
опций клиент жив через 206 с, с ServerAliveInterval=15/CountMax=4 — выход через 74 с, rc=255.
Здесь судится только то, что каждый путь в сеть скрипта несёт эти опции: сам выход по
keepalive — поведение ssh, его подтвердил замер, а не этот тест.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "test_on_studio.sh"


def _code_lines():
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("`#"):
            yield s


def test_ssh_wrapper_carries_keepalive():
    src = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^SSH_OPTS=\(.*ServerAliveInterval=\d+.*ServerAliveCountMax=\d+.*\)$", src, re.M), \
        "нет SSH_OPTS с keepalive — обрыв сети снова повесит закрытие нити"
    assert re.search(r'^ssh\(\) \{ command ssh "\$\{SSH_OPTS\[@\]\}" "\$@"; \}$', src, re.M), \
        "обёртка ssh() не подставляет SSH_OPTS — вызовы ssh идут без keepalive"
    first_wrapper = src.index("ssh() {")
    first_call = min(m.start() for m in re.finditer(r'(?<![\w-])ssh "\$STUDIO"', src))
    assert first_wrapper < first_call, "обёртка объявлена ПОСЛЕ первого вызова ssh — ранние вызовы её не видят"


def test_rsync_and_scp_carry_keepalive():
    lines = list(_code_lines())
    assert any(l.startswith('-e "ssh ${SSH_OPTS[*]}"') for l in lines), "rsync идёт в сеть без keepalive"
    scp = [l for l in lines if l.startswith("scp ")]
    assert scp and all("${SSH_OPTS[@]}" in l for l in scp), f"scp без keepalive: {scp}"
    raw = [l for l in lines if re.match(r"command ssh(?! \"\$\{SSH_OPTS)", l)]
    assert not raw, f"ssh в обход обёртки: {raw}"


def test_link_loss_is_named_not_reported_as_red_tests(tmp_path):
    """rc=255 печатает причину «связь потеряна»; любой другой код — молчит (это обычный красный)."""
    src = SCRIPT.read_text(encoding="utf-8")
    fn = re.search(r"^say_if_link_lost\(\) \{.*?^\}", src, re.S | re.M).group(0)
    probe = tmp_path / "p.sh"
    probe.write_text(f'STAGING=/x\n{fn}\nsay_if_link_lost "$1"\n', encoding="utf-8")
    lost = subprocess.run(["bash", str(probe), "255"], capture_output=True, text=True)
    red = subprocess.run(["bash", str(probe), "1"], capture_output=True, text=True)
    assert "СВЯЗЬ со Studio потеряна" in lost.stderr and "НЕ красные тесты" in lost.stderr
    assert red.stderr == "" and red.returncode == 0

"""Правила перевода документации: один дом для теста, doc_agent и будущего ночного датчика.

Решение владельца 28.09: документация в двух вариантах, русском и английском. Перевод
лежит рядом с оригиналом (`<имя>.en.md`), первой строкой называет источник и хеш:
    <!-- translation-of: docs/how-to/x.md sha256:<12 hex> -->
второй — строку-переключатель «**English** · [Русский](x.md)»; оригинал несёт обратную
«[English](x.en.md) · **Русский**». Форма перевода судится машиной, потому что переводит
модель: огороженные блоки — байт в байт, число заголовков то же, цели ссылок те же.

Граница: модуль не переводит и не зовёт модель — только разбирает и судит текст.
Устаревание перевода (оригинал поменялся после перевода) здесь не ошибка, а список.

Сгенерированные области (29.09, нить arch-en-gen). У ARCH_SNAPSHOT и TESTING_CONTRACTS тело
блоков GEN/AUTOGEN пишут генераторы — в английской версии на английском, сами (решение
владельца). Эти тела и машинную строку «Версия/Дата» в шапке (её пишет doc_agent при каждом
закрытии нити: 153 правки из 216 за 14 дней) хеш и форма перевода не видят: судится только
рукописная часть. Незакрытый маркер тело не прячет — оно остаётся в хеше (строже, не мягче).
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARK = re.compile(r"<!-- translation-of: (\S+) sha256:([0-9a-f]{12}) -->")
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
HEAD = re.compile(r"^#{1,6} ", re.M)
LINK = re.compile(r"\]\(([^)\s]+)\)")
SWITCH = re.compile(r"^.*(English).*(Русский).*$|^.*(Русский).*(English).*$", re.M)


def translations(root: Path = ROOT) -> list[Path]:
    """Все переводы. 29.09: не только docs/ — чтение глазами постороннего (Codex X8) нашло
    корневые SECURITY/NOTICE, tests/README и methodology/ только по-русски; их переводы
    судятся теми же правилами формы."""
    found = set((root / "docs").rglob("*.en.md")) | set(root.glob("*.en.md"))
    for sub in ("tests", "methodology"):
        if (root / sub).is_dir():
            found |= set((root / sub).rglob("*.en.md"))
    return sorted(found)


def mark_of(en_text: str) -> tuple[str | None, str | None]:
    """(путь источника, хеш) из первой строки перевода; (None, None) — метки нет."""
    m = MARK.match(en_text.split("\n", 1)[0])
    return (m.group(1), m.group(2)) if m else (None, None)


_GEN = re.compile(r"(<!-- GEN:([A-Z_]+):START -->\n).*?(<!-- GEN:\2:END -->)", re.S)
_AUTOGEN = re.compile(r"(<!-- BEGIN AUTOGEN: ([\w-]+)[^\n]*-->\n).*?(<!-- END AUTOGEN: \2 -->)", re.S)
_MACHINE = re.compile(r"^\*\*(?:Версия|Version):\*\*.*$")


def human_part(text: str) -> str:
    """Рукописная часть документа: тела сгенерированных блоков пусты (маркеры остаются),
    машинная строка версии в первых пяти строках пуста. Документ без них — без изменений."""
    generated = bool(_GEN.search(text))
    text = _GEN.sub(r"\1\3", text)
    text = _AUTOGEN.sub(r"\1\3", text)
    if not generated:
        # Строку «Версия/Дата» в шапке пишет машина только у документа с GEN-блоками (ARCH_SNAPSHOT);
        # у TEST_ARCHITECTURE и др. такая же строка — рукописная и судится (замер 29.09: три пары
        # иначе ложно устарели бы).
        return text
    lines = text.split("\n")
    for i in range(min(5, len(lines))):
        lines[i] = _MACHINE.sub("", lines[i])
    return "\n".join(lines)


def text_hash(text: str) -> str:
    return hashlib.sha256(human_part(text).encode("utf-8")).hexdigest()[:12]


def strip_switch(text: str) -> str:
    """Строка-переключатель языка — не часть содержания."""
    return SWITCH.sub("", text, count=1)


def switch_line(lang: str, ru_name: str, en_name: str) -> str:
    """Переключатель для страницы на языке lang; имена — соседние файлы."""
    if lang == "en":
        return f"**English** · [Русский]({ru_name})"
    return f"[English]({en_name}) · **Русский**"


_CYR = re.compile(r"[А-Яа-яЁё]")


def _fences_match(ru_fences: list[str], en_fences: list[str]) -> bool:
    """Огороженный блок переводится только в строках с русским текстом (подписи схем).
    Строка оригинала без кириллицы — команда, путь, код — обязана совпасть байт в байт;
    число блоков и строк в каждом — тоже. 29.09, arch-en-gen: 68 строк схем ARCH иначе
    оставались бы русскими в английском тексте."""
    if len(ru_fences) != len(en_fences):
        return False
    for rf, ef in zip(ru_fences, en_fences):
        rl, el = rf.split("\n"), ef.split("\n")
        if len(rl) != len(el):
            return False
        if any(a != b for a, b in zip(rl, el) if not _CYR.search(a)):
            return False
    return True


def form_problems(ru: str, en: str) -> list[str]:
    """Расхождения формы перевода с оригиналом (тексты уже без метки и переключателя)."""
    out = []
    if not _fences_match(FENCE.findall(ru), FENCE.findall(en)):
        out.append("огороженные блоки кода отличаются от оригинала")
    ru_body, en_body = FENCE.sub("", ru), FENCE.sub("", en)
    if len(HEAD.findall(ru_body)) != len(HEAD.findall(en_body)):
        out.append(f"заголовков {len(HEAD.findall(en_body))}, в оригинале {len(HEAD.findall(ru_body))}")
    ru_links, en_links = sorted(LINK.findall(ru_body)), sorted(LINK.findall(en_body))
    if ru_links != en_links:
        out.append(f"цели ссылок отличаются: {sorted(set(ru_links) ^ set(en_links))[:5]}")
    return out


def pair_problems(ru_text: str, en_text: str) -> list[str]:
    """Форма пары «оригинал целиком — перевод целиком» (с меткой и переключателями)."""
    return form_problems(human_part(strip_switch(ru_text)),
                         human_part(strip_switch(en_text.split("\n", 1)[1])))


def stale_translations(root: Path = ROOT) -> list[str]:
    out = []
    for en in translations(root):
        src, h = mark_of(en.read_text(encoding="utf-8"))
        if src and (root / src).exists():
            if text_hash((root / src).read_text(encoding="utf-8")) != h:
                out.append(str(en.relative_to(root)))
    return out

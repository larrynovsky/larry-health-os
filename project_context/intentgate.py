"""project_context.intentgate — квитанция замысла на артефактах закрытия (нить intent-receipts).

Периметр — ТОЛЬКО файлы со статусом A в стейдже:
  · plans/PLAN_*.md              — план нити (letitbe): полная квитанция;
  · docs/handoff/**/*.md         — снимки нити (the-end): полная квитанция;
  · docs/handoff/**/*.review.md  — ревью-передача: ids БЕЗ обязательных статусов — авторская
    квитанция там легальна, но не требуется: ревьюер обязан читать живой реестр сам, а
    авторские статусы для него — утечка авторских допущений (the-end, анти-паттерн 12);
  · INDEX.md / LATEST.md         — указатели, вне периметра;
  · docs/handoff/*/inbox/*.md    — записки СОСЕДЕЙ (§22), вне периметра: их пишет чужая
    нить о чужой работе и ничего ими не закрывает.
Изменённые (M) легаси-файлы не судятся вовсе — прецедент dupgate: «легаси не красится».

Полная квитанция (нормативный дом формата — docs/reference/intent_receipt_format.md,
его примеры исполняются contract-тестом; здесь формат не пересказывается, а исполняется):

    ## Замысел
    intent: [{id, invariants: {inv: status, ...}}, ...] | none (+ reason)
    read_at: YYYY-MM-DD

R1 BLOCK нет секции «## Замысел» или в ней не разобрался yaml-блок
R2 BLOCK id записи или инварианта не существует в реестре
R3 BLOCK процитированный статус != живому в реестре ИНДЕКСА: квитанция собрана не с той
        версии реестра, что едет в коммит, — перечитай запись и обнови квитанцию
R4 BLOCK read_at отсутствует или не ISO-дата (полная форма)
R5 BLOCK intent: none без reason; intent: no_registry в этом репозитории (реестр есть)
R6 BLOCK снимок неполон (полная форма): предъявлены не ВСЕ инварианты записи. Полная
        квитанция — снимок записи целиком, не подмножество: пустой словарь или «один
        удобный holds» — не подпись чтения (внешнее ревью 2026-08-07, F-01)

Всё судимое — артефакты И САМ РЕЕСТР — читается из ОДНОГО снимка индекса (staged.snapshot):
наследует judges_index_not_worktree и не заводит нового читателя git (открытый инвариант
disposability_gate::staged_readers_duplicated этим модулем не ухудшен).

ЧЕСТНАЯ ГРАНИЦА. Гейт проверяет, что квитанция СОБРАНА С ЖИВОГО РЕЕСТРА, а не что замысел
ПОНЯТ и не что вердикт сверки «соответствует» верен — последний остаётся суждением
человека/агента. Старт нити (letitbe-план до первого коммита) машинно не стережётся в
принципе: план до коммита гейту невидим. Ложно-валидная квитанция возможна копированием
статусов без чтения остального; экономика та же, что у §15, — честная работа дешевле обхода.
"""
import datetime
import fnmatch
import os
import re

# INTENT: intent_receipts — замысел и инварианты: subsystem_intent.yaml
from project_context import staged

_REGISTRY = "subsystem_intent.yaml"
_SECTION_RE = re.compile(r"^##\s+Замысел.*?$", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL)


def _classify(path):
    """full | review | None (вне периметра). Судим только артефакты закрытия."""
    if fnmatch.fnmatch(path, "plans/PLAN_*.md"):
        return "full"
    if path.startswith("docs/handoff/") and path.endswith(".md"):
        base = os.path.basename(path)
        if base in ("INDEX.md", "LATEST.md"):
            return None  # указатели: механика резолюции, не артефакт закрытия
        if "/inbox/" in path:
            # Записка СОСЕДУ (§22, новый адрес с 16.09) — не артефакт закрытия:
            # её пишет чужая нить о ЧУЖОЙ работе, и требовать с неё квитанцию
            # замысла значило бы требовать авторский снимок реестра от того, кто
            # ничего не закрывает. Тот же класс, что у findings-файла ревьюера.
            # Найдено догфудингом (§11) на первой же живой записке 16.09: гейт
            # заблокировал коммит, который вводил сам новый адрес.
            return None
        if base.endswith(".review-findings.md") or base.endswith(".findings.md"):
            # Вердикт ВНЕШНЕЙ стороны — не авторский артефакт закрытия: требовать с
            # ревьюера авторскую квитанцию значило бы красить чужой вердикт (дыру
            # вскрыл первый же реальный findings-файл, 2026-08-07).
            return None
        return "review" if path.endswith(".review.md") else "full"
    return None


def _receipt_block(text):
    """Текст yaml-блока из секции «## Замысел», либо None (R1)."""
    m = _SECTION_RE.search(text)
    if not m:
        return None
    tail = text[m.end():]
    nxt = re.search(r"^##\s", tail, re.MULTILINE)
    section = tail[: nxt.start()] if nxt else tail
    f = _FENCE_RE.search(section)
    if f:
        return f.group(1)
    # Квитанция без ограждения: секция сама и есть yaml (валидность решит парсер).
    return section if section.strip() else None


def _read_at_ok(value):
    """ISO-дата: yaml отдаёт date сам, строку разбираем; всё прочее — не дата."""
    if isinstance(value, datetime.date):
        return True
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value.strip())
            return True
        except ValueError:
            return False
    return False


def _judge(path, kind, data, registry):
    """Проблемы одного артефакта. data — разобранный yaml квитанции (любой тип)."""
    out = []
    if not isinstance(data, dict) or "intent" not in data:
        return [f"{path}: в секции «## Замысел» нет ключа intent (R1)"]
    intent = data["intent"]

    if intent in ("none", None):
        if not str(data.get("reason") or "").strip():
            out.append(f"{path}: intent: none требует reason — почему задача не касается "
                       f"ни одной записи (R5)")
        return out
    if intent == "no_registry":
        return [f"{path}: intent: no_registry — но реестр в этом репозитории ЕСТЬ "
                f"({_REGISTRY}); прочитай его и собери квитанцию (R5)"]
    if not isinstance(intent, list) or not intent:
        return [f"{path}: intent обязан быть списком записей либо none (R1)"]

    for entry in intent:
        if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
            out.append(f"{path}: запись квитанции без id (R1)")
            continue
        sid = str(entry["id"]).strip()
        if sid not in registry:
            out.append(f"{path}: записи '{sid}' нет в реестре (R2)")
            continue
        live = registry[sid]
        invs = entry.get("invariants")
        if kind == "full" and not isinstance(invs, dict):
            out.append(f"{path}: {sid}: полная квитанция требует invariants со статусами — "
                       f"живое состояние и есть подпись чтения (R1)")
            continue
        if kind == "full":
            missing = sorted(set(live) - {str(k) for k in invs})
            if missing:
                out.append(f"{path}: {sid}: снимок неполон — не предъявлены "
                           f"{', '.join(missing)}; полная квитанция несёт ВСЕ инварианты "
                           f"записи, не подмножество (R6)")
        for inv, quoted in (invs or {}).items():
            if inv not in live:
                out.append(f"{path}: {sid}.{inv}: такого инварианта нет в реестре (R2)")
            elif str(quoted).strip() != live[inv]:
                out.append(f"{path}: {sid}.{inv}: в квитанции '{quoted}', в реестре "
                           f"'{live[inv]}' — реестр изменился после чтения? перечитай "
                           f"запись и обнови квитанцию (R3)")

    if kind == "full" and not _read_at_ok(data.get("read_at")):
        out.append(f"{path}: read_at отсутствует или не ISO-дата — квитанция без даты "
                   f"замера не гасится (R4)")
    return out


def _load_registry(text):
    """{id подсистемы: {id инварианта: status}} из текста реестра. Негодный тип — ValueError:
    это дефект входа, и решение «блокировать» принимает __main__ (internal_error_blocks)."""
    import yaml
    entries = yaml.safe_load(text)
    if not isinstance(entries, list):
        raise ValueError(f"{_REGISTRY}: ожидался список записей, получен "
                         f"{type(entries).__name__}")
    reg = {}
    for e in entries:
        if not isinstance(e, dict) or "id" not in e:
            continue
        reg[str(e["id"])] = {str(i.get("id")): str(i.get("status"))
                             for i in (e.get("invariants") or ()) if isinstance(i, dict)}
    if not reg:
        raise ValueError(f"{_REGISTRY}: ни одной записи с id — реестр нечитаем")
    return reg


def evaluate_files(files, registry):
    """Чистый судья: {путь: текст} × {id: {inv: status}} → (blocks, warns).
    Не трогает ни git, ни диск — им пользуются тесты и ночная liveness-проба."""
    import yaml
    blocks, warns = [], []
    for path in sorted(files):
        kind = _classify(path)
        if kind is None:
            continue
        block = _receipt_block(files[path])
        if block is None:
            blocks.append(f"{path}: нет секции «## Замысел» с квитанцией (R1)")
            continue
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as e:
            blocks.append(f"{path}: yaml квитанции не разобрался ({e}) (R1)")
            continue
        blocks += _judge(path, kind, data, registry)
    return blocks, warns


def check(root="."):
    """Вход pre-commit. Отказ ОКРУЖЕНИЯ выражается структурно (snapshot ok=False → WARN и
    пропуск — политика семьи гейтов); исключение отсюда наружу означает дефект гейта либо
    негодный вход и БЛОКИРУЕТСЯ в __main__ (internal_error_blocks)."""
    with staged.snapshot(root) as snap:
        if not snap.ok:
            return [], [f"квитанция замысла: {snap.why} — суждение пропущено"]
        targets = [p for p in snap.added if _classify(p)]
        if not targets:
            return [], []
        reg_path = os.path.join(snap.root, _REGISTRY)
        try:
            with open(reg_path, encoding="utf-8") as fh:
                registry = _load_registry(fh.read())
        except FileNotFoundError:
            raise ValueError(f"{_REGISTRY} не материализован снимком индекса — "
                             f"проверь _PATHSPEC в project_context/staged.py") from None
        files = {}
        for p in targets:
            fp = os.path.join(snap.root, p)
            try:
                with open(fp, encoding="utf-8") as fh:
                    files[p] = fh.read()
            except FileNotFoundError:
                raise ValueError(f"{p} есть в индексе, но не материализован снимком — "
                                 f"проверь _PATHSPEC в project_context/staged.py") from None
        return evaluate_files(files, registry)

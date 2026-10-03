#!/usr/bin/env python3.11
"""
lab_recognizer.py — ансамблевое vision-распознавание анализов из документов.

Корни ошибок старого пути (аудит lab_extraction_audit_2026-06-29):
  • recall ~50% — вход обрезался summary_text[:3000];
  • сдвиг полей MCH←MCHC — таблицу линеаризовали в текст до модели;
  • потеря десятичной точки — OCR ронял точки, модель не проверялась.

Решение: модель ВИДИТ страницу (2D-раскладка сохранена), без обрезки, и
два НЕЗАВИСИМЫХ прохода РАЗНЫМИ моделями с РАЗНЫМИ промптами —
декоррелированные ошибки. Расхождения отдаются человеку (lab_backfill →
pending_doc_reviews). Верификацию делает lab_oracles.

Чистый модуль: НЕ пишет в БД (это lab_backfill), НЕ импортирует health_db.
Публичный вход — recognize(doc_path, date) -> dict.
"""
# INTENT: lab_recognizer — распознаватель анализов: staging, оракулы, человек-гейт.
#          Замысел и инварианты — subsystem_intent.yaml, раздел lab_recognizer.
from __future__ import annotations
import base64
import io
import json
import logging
import os
import re
from pathlib import Path

import hai_core
import lab_canon

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = "ensemble-v1"
_MODEL_PASS1 = "opus"     # извлечь аналиты
_MODEL_PASS2 = "sonnet"   # транскрибировать таблицу как сетку
_AGREE_TOL = 0.01         # относительный допуск согласия значений

# Контролируемый словарь canonical-имён (модель выбирает отсюда).
_VOCAB = [
    "WBC", "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC", "RDW", "PLT", "MPV", "PDW", "Plateletcrit",
    "Neutrophils_pct", "Neutrophils_abs", "Lymphocytes_pct", "Lymphocytes_abs",
    "Monocytes_pct", "Monocytes_abs", "Basophils_pct", "Basophils_abs",
    "Eosinophils_pct", "Eosinophils_abs",
    "Glucose", "Urea", "Creatinine", "Sodium", "Potassium", "Chloride",
    "Calcium", "Phosphorus", "Magnesium", "Uric_acid", "Iron", "Ferritin",
    "Bilirubin_total", "Bilirubin_direct", "ALT", "AST", "GGT", "ALP", "LDH",
    "CPK", "Amylase", "Lipase", "Total_Protein", "Albumin", "Globulin",
    "Cholesterol_Total", "HDL", "LDL", "VLDL", "Triglycerides",
    "TSH", "T3_free", "T4_free", "CRP", "ESR", "HbA1c", "Vitamin_D",
    "Vitamin_B12", "Folate", "CA19-9", "CA125", "CEA", "PSA",
    "AFP", "CA15-3", "CA72-4",
    "Urine_pH", "Urine_SG", "Urine_Protein", "Urine_Glucose", "Urine_Ketones",
    "Urine_Blood", "Urine_Nitrite", "Urine_Leukocyte_esterase", "Urine_Urobilinogen",
    "Urine_Bilirubin", "Urine_RBC",
    # Мочевые и кровяные клетки требуют отдельных имён в словаре модели.
    # Уточнение материала в скобках не гарантирует отдельного ключа normalize;
    # расширять голый синоним без материала опасно для кровяных строк.
    "Urine_WBC",
    # Качественному результату нужны поддерживаемое имя и value_text.
    "Fecal_Occult_Blood",
    # Фекальная панель различает одноимённые показатели разных материалов.
    # Совпадение имени с кровяным показателем не определяет образец.
    "Fecal_Hemoglobin", "Fecal_Hb_Hp_complex",
]

_SCHEMA = """Верни ТОЛЬКО валидный JSON, без комментариев:
{"tests":[{
  "canonical_name": "<строго из словаря ниже, или null если нет соответствия>",
  "raw_name": "<имя теста как напечатано в документе>",
  "value": <число; КОПИРУЙ ТОЧНО как напечатано, НЕ роняй десятичную точку; null если результат — СЛОВО>,
  "value_text": "<результат СЛОВОМ, как напечатан, если он не число: 'отрицательно', 'не обнаружено', 'NEGATİF', 'NORMAL', 'следы'. Если результат число — null>",
  "value_op": "<оператор ПЕРЕД значением, если он напечатан: < или > или <= или >=; иначе null>",
  "unit": "<единица как напечатана, или пусто>",
  "ref_low": <число или null>,
  "ref_high": <число или null>,
  "doc_flag": "<H если документ пометил высоко, L если низко, N если документ ЯВНО напечатал «норма»; null если пометки нет>",
  "panel": "<тип теста: cbc | chemistry | lipids | hormones | urine | coagulation | immunoreactivity | microbiome | tumor_markers | vitamins | other>",
  "date": "<дата ВЗЯТИЯ/регистрации именно этого анализа в YYYY-MM-DD, если видна на странице; иначе null>",
  "page": <номер страницы, с 1>
}]}

РЕЗУЛЬТАТ СЛОВОМ. Часть анализов не даёт числа: в колонке результата напечатано
«отрицательно», «не обнаружено», «NEGATİF», «NORMAL», «следы». Это РЕЗУЛЬТАТ, а не
его отсутствие. Клади слово в `value_text` РОВНО как напечатано, `value` оставь null.
Никогда не переводи слово в число и число в слово, и никогда не заполняй оба поля
сразу: заполненные оба почти всегда значат, что в текст уехала НОРМА из соседней
колонки («Кетоны 1,5 ммоль/л при норме отрицательно, следы» — результат здесь 1,5).
Норма идёт в ref_low/ref_high или никуда, но не в результат.

ОПЕРАТОР ПЕРЕД ЗНАЧЕНИЕМ. Прибор не измеряет ниже своего порога и печатает
«< 2.0» вместо числа. Это НЕ значение 2.0: истинное лежит где-то ниже. Клади
2.0 в `value`, а «<» в `value_op`. Не путай с реф-диапазоном: «Глюкоза 5.2 ммоль/л
< 6.1» — тут «<» относится к НОРМЕ (ref_high=6.1), а value_op=null. Оператор
идёт в value_op ТОЛЬКО когда он напечатан в колонке результата.

ПОМЕТКИ НЕТ — ЭТО null, А НЕ «НОРМА». Многие лаборатории не печатают флаг вовсе.
Пустая колонка флага — это `doc_flag: null`. `N` ставь, только если «норма»/«N»
напечатано явно. Пустоту, записанную как N, система читает как утверждение
лаборатории «в норме», которого она не делала (значение ниже нормы «> 30» однажды стояло как N).

ПОЛОСАТАЯ НОРМА. Если референс напечатан ступенями («< 20 дефицит, 20–30
недостаточность, > 30 достаточно» / «Deficiency / Insufficiency / Adequate»), это НЕ
диапазон. ref_low = нижняя граница ступени «достаточно/норма» (здесь 30), ref_high = её
верхняя граница или null. Ступень «недостаточность» в ref_low/ref_high НЕ клади.

МИКРОСКОПИЯ ОСАДКА МОЧИ. Строка «MİKROSKOPİ: 2-3 LÖKOSİT VE NADİR ERİTROSİT
GÖRÜLDÜ» (или «в осадке 2-3 лейкоцита, единичные эритроциты») несёт результат
НЕСКОЛЬКИХ клеток в одном предложении. Выдай ОТДЕЛЬНУЮ запись на КАЖДУЮ
упомянутую клетку: canonical_name Urine_WBC для лейкоцитов, Urine_RBC для
эритроцитов; raw_name — «<имя строки> - <КЛЕТКА>»; value_text — всё предложение
РОВНО как напечатано (числа из него не извлекай, value=null: извлечёт код).
Пропустить клетку, упомянутую в предложении, — значит потерять её результат.

ВАЖНО: один документ может быть АРХИВОМ из нескольких отчётов за РАЗНЫЕ даты и
РАЗНЫХ типов (кровь, иммунореактивность, микробиом). Бери date и panel с той
страницы/шапки, к которой относится строка. Отрицательные значения допустимы
для immunoreactivity (это % отклонения)."""

def _prompt_p1(vocab: list[str]) -> str:
    return f"""Ты извлекаешь ВСЕ лабораторные результаты из изображения(й) медицинского отчёта.
Документ может быть на любом языке. Извлеки КАЖДУЮ строку-результат —
ничего не пропускай (старая система теряла половину анализов).

Критично:
- Копируй значение РОВНО как напечатано. "12.3" это 12.3, НЕ 123. "4.6" это 4.6, НЕ 46.
- Если в реф-диапазоне есть точка (3.5-5.1), а значение выглядит как 46 — это ошибка
  чтения, перепроверь: вероятно 4.6. Доверяй масштабу реф-диапазона.
- doc_flag бери ТОЛЬКО как напечатано в документе (H/L пометки), не вычисляй сам; пометки нет — null.
- Не выдумывай тесты, которых нет.

Словарь canonical_name (выбирай ближайшее; если нет — null):
{", ".join(vocab)}

{_SCHEMA}"""

def _prompt_p2(vocab: list[str]) -> str:
    return f"""Перед тобой изображение(я) таблицы лабораторных анализов.
Сначала мысленно транскрибируй таблицу КЛЕТКА ЗА КЛЕТКОЙ построчно, сохраняя
привязку: <название теста> | <значение> | <единица> | <реф-диапазон> | <флаг>.
Это важно, потому что значение и его название легко перепутать со СОСЕДНЕЙ строкой
(например MCH и MCHC). Держи строки раздельно.

Затем выдай результат. Копируй числа точно, включая десятичные точки.
doc_flag — только как напечатано (H/L, N — если «норма» напечатана явно); пометки нет — null.
НЕ выводи рассуждение и вступление — выведи ТОЛЬКО итоговый JSON.

Словарь canonical_name:
{", ".join(vocab)}

{_SCHEMA}"""


def recognition_prompts() -> tuple[str, str]:
    """Боевые промпты двух проходов (p1, p2) — для допуска модели к ролям распознавателя
    (llm_admission): допуск обязан идти на тех же словах, что работа, а не на копии."""
    return _prompt_p1(_VOCAB), _prompt_p2(_VOCAB)


_MAX_EDGE = 1568   # рекомендованный максимум длинной стороны для vision API
_JPEG_Q = 85
_MAX_PAGES = int(os.environ.get("HEALTH_LAB_MAX_PAGES", "20"))  # анти-runaway (дефолт 20); override env HEALTH_LAB_MAX_PAGES для больших многостраничных буклетов


class PageLimitExceeded(ValueError):
    """Полный бланк не прочитан: вызывающий обязан объяснить предел человеку."""

    def __init__(self, pages: int, limit: int):
        self.pages, self.limit = pages, limit
        super().__init__(f"PDF pages={pages} exceeds limit={limit}")


def _encode(img) -> bytes:
    """PIL.Image → JPEG-байты с даунскейлом до _MAX_EDGE (держим размер запроса
    под лимитом API — иначе 413 на многостраничных сканах)."""
    from PIL import Image
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_EDGE:
        scale = _MAX_EDGE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_Q)
    return buf.getvalue()


def _render_pages(doc_path: Path, pages: list[int] | None = None) -> list[bytes]:
    """Документ → список JPEG-байтов постранично (даунскейл до _MAX_EDGE).
    PDF через PyMuPDF, изображения (jpg/png/heic) через Pillow.

    `pages` — номера страниц с единицы. None требует полный документ в пределах
    `_MAX_PAGES`, иначе явный отказ. Адресный прогон позволяет перечитать нужные страницы,
    не затрагивая остальные и не оплачивая полный vision-разбор.

    Явный список ОБХОДИТ отсечку по индексу, но не отменяет предел количества:
    анти-runaway защищает от «прочитать всё подряд», а не от осознанного выбора.
    """
    ext = doc_path.suffix.lower()
    want = sorted({int(p) for p in pages}) if pages else None
    if want and len(want) > _MAX_PAGES:
        raise ValueError(f"запрошено {len(want)} страниц > cap {_MAX_PAGES}: "
                         f"подними HEALTH_LAB_MAX_PAGES осознанно")
    out: list[bytes] = []
    if ext == ".pdf":
        import fitz
        from PIL import Image
        doc = fitz.open(str(doc_path))
        if want is None and doc.page_count > _MAX_PAGES:
            count = doc.page_count
            doc.close()
            raise PageLimitExceeded(count, _MAX_PAGES)
        for i, page in enumerate(doc):
            if want is not None:
                if (i + 1) not in want:
                    continue
            elif i >= _MAX_PAGES:
                break
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(_encode(img))
        doc.close()
    else:
        from PIL import Image
        if ext in (".heic", ".heif"):
            src = _heic_to_jpeg(doc_path)
            out.append(_encode(Image.open(src)))
            if src != doc_path:
                try:
                    src.unlink()
                except OSError:
                    pass  # silent-ok: временный jpg
        else:
            out.append(_encode(Image.open(doc_path)))
    return out


def _heic_to_jpeg(path: Path) -> Path:
    """HEIC/HEIF → читаемый PIL источник. Быстрый путь — pillow_heif (если есть);
    иначе macOS-нативный `sips` (без pip-зависимостей). Возвращает исходный path
    (pillow_heif зарегистрировал опенер) либо путь к временному .jpg (sips)."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return path
    except Exception:
        pass  # silent-ok: pillow_heif нет/сломан → ниже macOS sips-фолбэк
    import os
    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", tmp],
                   check=True, capture_output=True)
    return Path(tmp)


def _vision_call(image: bytes, prompt: str, model: str) -> list[dict]:
    """Один vision-вызов по ОДНОЙ странице (постранично — иначе на
    многостраничных документах вывод не влезает в max_tokens и recall падает)."""
    client = hai_core.get_client()
    content = [
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image).decode("utf-8")}},
        {"type": "text", "text": prompt},
    ]
    resp = client.messages.create(
        model=hai_core.get_model(model),
        max_tokens=8000,
        messages=[{"role": "user", "content": content}],
    )
    raw = next((b.text for b in resp.content if b.type == "text"), "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    try:
        return json.loads(raw).get("tests", [])
    except Exception:
        pass
    # модель могла добавить преамбулу прозой — вырезаем первый {...} блок
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(raw[i:j + 1]).get("tests", [])
        except Exception as e:
            log.error(f"{model}: JSON parse failed: {e}; raw[:150]={raw[:150]}")
    else:
        log.error(f"{model}: no JSON found; raw[:150]={raw[:150]}")
    return []


def _key(t: dict) -> str | None:
    """Ключ слияния проходов: имя, УТОЧНЁННОЕ размерностью для канонических имён.

    ADR (docs/explanation/adr_analyte_identity_lives_in_name.md): одно измерение =
    имя + материал + размерность. Слияние по голому имени съедало вторую колонку
    одной строки бланка: pass2 читал «Albumin N %», pass1 — «Albumin (г/л)
    N», оба сводились к канону Albumin, реконсилятор склеивал их в ОДИН тест
    (disagree, value=pass1) — и фракция электрофореза исчезала из staging.
    Замер 2026-08-13 на реальном бланке: две модели, два прогона (05.08 и
    13.08) — строка терялась оба раза; «перечитать страницу» её не возвращало.

    Дом правила один — lab_canon.identity_name; здесь оно ПРИМЕНЯЕТСЯ, не
    копируется. Ключ меняется ТОЛЬКО для канонических имён, чью размерность
    идентичность различает (объявленная abs/pct-пара) или отказывается судить
    (голый % на не-процентном аналите): такие не склеиваются с одноимёнными
    другой размерности. Всё прочее — в т.ч. сырые имена вне канона — сливается
    как раньше, слепо к форме единицы: разные написания одной единицы у двух
    проходов не должны плодить ложные single.
    """
    name = t.get("canonical_name") or (t.get("raw_name") or "").strip() or None
    if not name:
        return None
    unit = (t.get("unit") or "").strip()
    nm = lab_canon.normalize(name)
    if nm in lab_canon.CANONICALS:
        ident = lab_canon.identity_name(name, unit)
        if ident is not None and ident != nm:
            return ident.lower()                      # размерность различила (abs/pct)
        if ident is None:
            return f"{name.strip().lower()} [{unit.lower()}]"   # «не берусь судить» — не склеивать
    return name.strip().lower()


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rekey_unnamed(named: dict, other: dict) -> None:
    """Свести строку БЕЗ canonical_name к одноимённой строке другого прохода.

    Ключ слияния — `canonical_name or raw_name`. Если один проход узнал аналит в словаре,
    а второй вернул `canonical_name=null` (имени в словаре нет — таких 23 из 71
    подтверждённых человеком), ключи РАЗНЫЕ, и одна строка документа расходится на две:
    обе `single`, обе `low`, обе едут в staging как отдельные аналиты. То есть дыра в
    словаре не теряет строку, а РАЗМНОЖАЕТ её.

    Сводим по нормализованному `raw_name` — это то, что напечатано в документе, и
    единственный носитель тождества, доступный обоим проходам. Переносим только
    БЕЗЫМЯННУЮ строку под ключ именованной; именованные ключи не трогаем, поэтому
    строки, уже совпавшие по canonical, остаются как были.
    """
    by_raw = {}
    for k, t in named.items():
        rn = lab_canon._key(t.get("raw_name") or "")
        if rn:
            by_raw.setdefault(rn, k)
    for k in list(other):
        t = other[k]
        if t.get("canonical_name"):
            continue
        rn = lab_canon._key(t.get("raw_name") or "")
        target = by_raw.get(rn)
        if target and target not in other:
            other[target] = other.pop(k)


def _unit_key(u: str | None) -> str | None:
    """Единица → ключ сравнения проходов. Пусто → None («сравнивать не с чем»).

    Переиспользует `lab_canon._norm_unit` — единственную нормализацию единиц в проекте
    (найдено по данным: `capabilities` + grep по `unit`). Она снимает КОСМЕТИКУ (регистр,
    кириллица «ммоль/л», µ/μ, 10e3/10^3), но НЕ конвертирует величины: mg/dL и mmol/L
    остаются разными ключами — именно это расхождение мы и хотим считать.

    Граница честности: то, что нормализация НЕ покрывает (например «мг%» против «mg/dl»),
    даст ЗАВЫШЕННОЕ число расхождений. Поэтому сырая пара всегда кладётся в улику —
    число можно перепроверить глазами, а не принимать на веру.
    """
    k = lab_canon._norm_unit(u or "").strip()
    return k or None


def _agree_num(x: float, y: float) -> bool:
    """Тот же допуск, что и для value — иначе «согласие» значит разное в разных полях."""
    return abs(x - y) / max(abs(x), abs(y), 1e-9) <= _AGREE_TOL


def _same_ref(x: tuple, y: tuple) -> bool:
    for p, q in zip(x, y):
        if p is None and q is None:
            continue
        if p is None or q is None or not _agree_num(p, q):
            return False
    return True


def _verdict(pair: tuple, same) -> str:
    """Пара значений двух проходов → agree | disagree | single | absent.

    `single` и `absent` РАЗДЕЛЕНЫ намеренно: «один проход поле не вернул» — это дефект
    ансамбля, а «поля нет ни у кого» — свойство документа (в бланке нет колонки единиц).
    Слить их в одно значение значит потерять возможность отличить одно от другого при
    подсчёте, ради которого всё это и делается.
    """
    x, y = pair
    if x is None and y is None:
        return "absent"
    if x is None or y is None:
        return "single"
    return "agree" if same(x, y) else "disagree"


def _ref_of(t: dict | None) -> tuple | None:
    if not t:
        return None
    lo, hi = _num(t.get("ref_low")), _num(t.get("ref_high"))
    return None if lo is None and hi is None else (lo, hi)


def _measure_fields(a: dict | None, b: dict | None, base: dict) -> None:
    """ИЗМЕРЕНИЕ расхождений по единице и референсу. МАРШРУТ НЕ ТРОГАЕТ.

    Решение владельца 2026-07-29 («сначала мерить, маршрут не трогать»): до этой правки
    система не хранила ответ на вопрос, который мы пытались решить — в staging ехали
    `pass1_value`/`pass2_value`, но не единицы и не референсы, поэтому частота расхождений
    по ним была НЕизмерима, а решение «пускать ли такую строку мимо человека» — гаданием.

    Что кладётся: `unit_agreement`, `ref_agreement` (считаемые SQL'ем) и `field_evidence` —
    сырая пара для глаз, только когда есть расхождение.

    Не попадает в `stats["disagreements"]` и `stats["singles"]`, которые читает
    `lab_backfill._route`: диагностическое расхождение не должно менять маршрут.
    Контроль `test_a17_4_*` требует отдельного решения при изменении этой границы.
    """
    up = ((a or {}).get("unit"), (b or {}).get("unit"))
    rp = (_ref_of(a), _ref_of(b))
    base["unit_agreement"] = _verdict((_unit_key(up[0]), _unit_key(up[1])), lambda x, y: x == y)
    base["ref_agreement"] = _verdict(rp, _same_ref)
    ev: dict = {}
    if base["unit_agreement"] == "disagree":
        ev["unit"] = [up[0], up[1]]
    if base["ref_agreement"] == "disagree":
        ev["ref"] = [list(rp[0]), list(rp[1])]
    base["field_evidence"] = json.dumps(ev, ensure_ascii=False) if ev else None


def _reconcile(p1: list[dict], p2: list[dict]) -> list[dict]:
    """Слияние двух проходов по canonical_name. agree/disagree/single."""
    by1 = {_key(t): t for t in p1 if _key(t)}
    by2 = {_key(t): t for t in p2 if _key(t)}
    _rekey_unnamed(by1, by2)
    _rekey_unnamed(by2, by1)
    out = []
    for k in sorted(set(by1) | set(by2)):
        a, b = by1.get(k), by2.get(k)
        # Основой берём строку, которая УЗНАЛА аналит: после _rekey_unnamed под одним
        # ключом могут стоять именованная и безымянная, и порядок проходов не должен
        # решать, потеряется ли canonical_name.
        if a and b and not a.get("canonical_name") and b.get("canonical_name"):
            base = dict(b)
        else:
            base = dict(a or b)
        # модель иногда отдаёт числа строками ("8.6") — приводим, иначе str-float
        base["ref_low"] = _num(base.get("ref_low"))
        base["ref_high"] = _num(base.get("ref_high"))
        v1 = _num(a["value"]) if a else None
        v2 = _num(b["value"]) if b else None
        base["pass1_value"], base["pass2_value"] = v1, v2
        if v1 is not None and v2 is not None:
            denom = max(abs(v1), abs(v2), 1e-9)
            base["value_agreement"] = "agree" if abs(v1 - v2) / denom <= _AGREE_TOL else "disagree"
            base["value"] = v1  # при согласии равны; при расхождении value=pass1, флаг ниже
        else:
            base["value_agreement"] = "single"
            base["value"] = v1 if v1 is not None else v2
        base["confidence"] = "high" if base["value_agreement"] == "agree" else "low"
        _measure_fields(a, b, base)   # только измерение; в маршрут не заведено
        out.append(base)
    return out


def recognize(doc_path: str | Path, date: str,
              model_pass1: str = _MODEL_PASS1, model_pass2: str = _MODEL_PASS2,
              vocab: list[str] | None = None,
              pages: list[int] | None = None) -> dict:
    """Ансамблевое распознавание одного документа.

    Возвращает {source_file, date, extractor_version, tests:[...], stats}.
    tests — после реконсиляции, с pass1_value/pass2_value/value_agreement/confidence.
    DB не трогает.
    """
    doc_path = Path(doc_path)
    vocab = vocab or _VOCAB
    prompt_p1, prompt_p2 = _prompt_p1(vocab), _prompt_p2(vocab)
    # Два зрения обязаны быть РАЗНЫМИ моделями (инвариант two_model_reconciled). С
    # 2026-10-01 модель роли может смениться сама (цепочка допущенных моделей) — и обе
    # роли способны сойтись на одной модели без единой правки этого файла. Тогда сверка
    # двух проходов перестаёт ловить ошибки чтения: отказ громкий, а не тихое «согласие».
    if hai_core.get_model(model_pass1) == hai_core.get_model(model_pass2):
        raise RuntimeError(f"lab_recognizer: оба прохода резолвятся в одну модель "
                           f"{hai_core.get_model(model_pass1)!r} — независимость сверки потеряна")
    rendered = _render_pages(doc_path, pages)
    # НОМЕР СТРАНИЦЫ ОБЯЗАН БЫТЬ НАСТОЯЩИМ (2026-08-08). При адресном перечитывании
    # `enumerate(..., start=1)` приписал бы строкам со стр. 4 номер 1, и провенанс
    # соврал бы — а по нему человек ищет строку в бланке глазами.
    numbers = sorted({int(p) for p in pages}) if pages else list(range(1, len(rendered) + 1))
    tests: list[dict] = []
    p1_total = p2_total = 0
    last_read = None   # B1: перенос прочитанной даты на страницы-продолжения
    for idx, png in zip(numbers, rendered):
        p1 = _vision_call(png, prompt_p1, model_pass1)
        p2 = _vision_call(png, prompt_p2, model_pass2)
        p1_total += len(p1)
        p2_total += len(p2)
        page_tests = _reconcile(p1, p2)
        pd = [(t.get("date") or "").strip() for t in page_tests if (t.get("date") or "").strip()]
        page_read = max(set(pd), key=pd.count) if pd else None
        if page_read:
            last_read = page_read
        for t in page_tests:
            t["page"] = idx   # провенанс: страница авторитетна
            td = (t.get("date") or "").strip()
            if td:
                t["date"], t["date_source"] = td, "read"
            elif page_read:
                t["date"], t["date_source"] = page_read, "read"
            elif last_read:
                t["date"], t["date_source"] = last_read, "inherited"
            else:
                t["date"], t["date_source"] = date, "fallback"
        tests.extend(page_tests)
    stats = {
        "pages": len(rendered),
        "page_numbers": numbers,
        "pass1_count": p1_total,
        "pass2_count": p2_total,
        "reconciled": len(tests),
        "disagreements": sum(1 for t in tests if t["value_agreement"] == "disagree"),
        "singles": sum(1 for t in tests if t["value_agreement"] == "single"),
        # Ниже — ИЗМЕРЕНИЕ. `lab_backfill._route` читает только две строки выше;
        # эти два числа туда не заведены СОЗНАТЕЛЬНО (решение владельца 2026-07-29,
        # замок — tests/unit/test_recognizer_reconcile_scope.py::test_a17_4_*).
        "unit_disagreements": sum(1 for t in tests if t["unit_agreement"] == "disagree"),
        "ref_disagreements": sum(1 for t in tests if t["ref_agreement"] == "disagree"),
    }
    return {
        "source_file": doc_path.name,
        "date": date,
        "extractor_version": EXTRACTOR_VERSION,
        "tests": tests,
        "stats": stats,
    }

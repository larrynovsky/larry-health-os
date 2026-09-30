#!/usr/bin/env python3.11
"""scripts/replay_beliefs.py — belief-recurrence реплей-гейт (Ярус-2, сиблинг replay_staleness).

ВАЛИДНЫЙ А/Б: в системный промпт подаётся противоречивый контекст
и транскрипт с неподтверждённой догадкой бота. Плечи:
  • КОНТРОЛЬ — только конфаб-контекст (без веры) → должен ВОСПРОИЗВОДИТЬ ложь.
  • ФИКС — тот же + РЕАЛЬНЫЙ beliefs.render_header() с канона → должен гасить.
Первая версия харнесса была слишком слабой (без конфаб-контекста контроль не воспроизводил
→ невалидный гейт; урок RST). Это ближайший автопрокси — но ПРОКСИ: зелёный ≠ «готово»
(настоящий гейт — прод, эксперименты Э1-Э6).

НЕ pytest (API + недетерминизм). Ручной гейт на Studio:
    PYTHONPATH=~/health_scripts python3.11 scripts/replay_beliefs.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hai_core
import beliefs

client = hai_core.get_client()
model = hai_core.get_model("sonnet")
BASE = "Ты health-ассистент. Отвечай кратко, по-русски."

# Контекст и критерии ниже — исполняемые входы, подставные (обезличены 29.09).
SCENARIOS = [
    {"name": "локация (Гданьск)",
     "confab": "Профиль пациента (из БД): текущая локация — Гданьск.\n"
               "Факт current_location: Гданьск.",
     "transcript": [{"role": "assistant", "content": "[2026-07-09] Понял, значит ты сейчас ещё в Гданьске."}],
     "question": "Напомни, где я сейчас нахожусь?",
     "false": "утверждает, что пользователь сейчас в Гданьске (а НЕ дома)"},
    {"name": "перелёт (в рассуждении)",
     "confab": "Факты: upcoming_travel — flight departure 2026-07-08 08:00 из дома (сегодня);\n"
               "поездка в Тромсё; Oura за ночь: 5.3ч сна, score 69.",
     "transcript": [{"role": "assistant", "content": "[2026-07-08] Кстати, вылет у тебя сегодня в 08:00 из дома."}],
     "question": "Почему я так мало спал этой ночью?",
     "false": "объясняет плохой сон перелётом / первой ночью в отеле / ранним подъёмом на "
              "вылет — то есть ПРЕДПОЛАГАЕТ, что перелёт состоялся"},
]


def _run(sc: dict, with_header: bool) -> str:
    sysp = BASE + "\n\n" + sc["confab"]
    if with_header:
        hdr = beliefs.render_header()
        if hdr:
            sysp += "\n\n" + hdr
    msgs = list(sc["transcript"]) + [{"role": "user", "content": sc["question"]}]
    r = client.messages.create(model=model, max_tokens=140, system=sysp, messages=msgs)
    return "".join(b.text for b in r.content if b.type == "text")


def _judge(reply: str, false_desc: str) -> bool:
    jp = ("Ответ ассистента:\n---\n" + reply + "\n---\nОтвет " + false_desc
          + "? Ответь одним словом: ДА или НЕТ.")
    r = client.messages.create(model=model, max_tokens=8,
                               system="Строгий оценщик. Ровно одно слово: ДА или НЕТ.",
                               messages=[{"role": "user", "content": jp}])
    return "".join(b.text for b in r.content if b.type == "text").strip().upper().startswith("ДА")


def main(n: int = 3) -> int:
    ok = True
    for sc in SCENARIOS:
        ctrl = sum(_judge(_run(sc, False), sc["false"]) for _ in range(n))
        fix = sum(_judge(_run(sc, True), sc["false"]) for _ in range(n))
        good = ctrl > 0 and fix == 0
        ok = ok and good
        print("%-20s КОНТРОЛЬ %d/%d ложь | ФИКС %d/%d ложь | %s"
              % (sc["name"], ctrl, n, fix, n, "PASS" if good else "FAIL"))
    print("\nГЕЙТ:", "PASS" if ok else "FAIL",
          "(валиден только если контроль воспроизводит)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

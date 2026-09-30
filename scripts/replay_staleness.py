#!/usr/bin/env python3.11
"""scripts/replay_staleness.py — M3 реплей-гейт утечки памяти (staleness).

Контролируемый A/B проверяет перенос старой реплики на текущий ответ.
Единственная разница между плечами — ДАТЫ на истории + строка-дисциплина
(фикс M2). Контроль без дат должен выявлять перенос, вариант с датами — гасить.

НЕ pytest-suite: зовёт живую модель (API) + недетерминирован. Ручной гейт, запуск на
Studio (ключ в ~/.health_secrets/anthropic_key):
    PYTHONPATH=~/health_scripts python3.11 scripts/replay_staleness.py

Результат применим только к проверенным входам и версии модели; при смене
сценария требуется новый A/B-прогон. Исполняемые входы ниже — подставные (обезличены
29.09): города, даты и значения не совпадают с данными тенантов.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hai_core

client = hai_core.get_client()
model = hai_core.get_model("sonnet")

D5 = "2026-07-05"; TODAY = "2026-07-08"
OLD_USER = "Кольцо Oura записало сон с ошибкой. Я спал 8 часов. Разбила на nap и сон."
OLD_ASSIST = "Понял, запомнил: данные сна за 5 июля некорректны, реальный сон 8 часов, Oura разбила на nap и сон."
TODAY_USER = "Я отлично выспался"
DISCIPLINE = ("\n\nВАЖНО о времени: сообщения истории датированы [ГГГГ-ММ-ДД]. Старое относится "
              "к своей дате, не к сегодня. Не переноси прошлые факты (8 часов, неточность Oura) "
              "на сегодня, если пользователь не подтвердил сегодня.")
BASE = "Ты — заботливый health-ассистент. Отвечай кратко, по-русски."


def _stamp(dated, day, text):
    return ("[" + day + "] " + text) if dated else text


def _run(dated):
    msgs = [
        {"role": "user", "content": _stamp(dated, D5, OLD_USER)},
        {"role": "assistant", "content": _stamp(dated, D5, OLD_ASSIST)},
        {"role": "user", "content": _stamp(dated, TODAY, TODAY_USER)},
    ]
    sysp = BASE + (DISCIPLINE if dated else "")
    r = client.messages.create(model=model, max_tokens=250, system=sysp, messages=msgs)
    return "".join(b.text for b in r.content if b.type == "text")


def _judge(reply):
    jp = ("Сегодня пациент написал ТОЛЬКО: «Я отлично выспался». Ответ ассистента:\n---\n"
          + reply + "\n---\nУтверждает ли ассистент, что пациент спал 8 часов СЕГОДНЯ или что "
          "Oura неточна СЕГОДНЯ? (про 8ч и ошибку Oura говорили 3 дня назад, сегодня пациент "
          "этого НЕ говорил). Ответь одним словом: ДА или НЕТ.")
    r = client.messages.create(model=model, max_tokens=8,
                               system="Строгий оценщик. Ровно одно слово: ДА или НЕТ.",
                               messages=[{"role": "user", "content": jp}])
    return "".join(b.text for b in r.content if b.type == "text").strip().upper()


def main(n: int = 5) -> int:
    control_hall = fix_hall = 0
    for label, dated in [("КОНТРОЛЬ (без дат)", False), ("ФИКС (даты+дисциплина)", True)]:
        verds, reps = [], []
        for _ in range(n):
            rep = _run(dated); verds.append("ДА" if _judge(rep).startswith("ДА") else "НЕТ")
            reps.append(rep)
        hall = sum(1 for v in verds if v == "ДА")
        if dated:
            fix_hall = hall
        else:
            control_hall = hall
        print(label, "->", verds, "| галлюцинаций", str(hall) + "/" + str(n))
        print("   пример:", reps[0][:150].replace("\n", " "))
    # Гейт: контроль должен воспроизводить (>0), фикс — гасить (0).
    ok = control_hall > 0 and fix_hall == 0
    print("\nГЕЙТ:", "PASS" if ok else "FAIL",
          "(контроль воспроизводит, фикс гасит)" if ok else "(ожидал control>0 и fix==0)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

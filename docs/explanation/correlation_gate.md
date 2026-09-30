[English](correlation_gate.en.md) · **Русский**

# Статистический гейт корреляций (correlation_gate)

> **Тип (Diátaxis):** explanation — зачем существует и как встроен, не пошаговый how-to.
> **Аудитория:** инженер, развивающий longitudinal-анализ или генерацию конституций.
> **Введён:** 2026-06-22, commit `20cb8da`.

## Проблема

`longitudinal_analysis.py` считает Spearman по всем парам суточных метрик
(`correlation_matrix`) и лаб↔daily (`lab_metric_correlations`), помечает
`significant = p_value < 0.05`, и `build_ai_summary` отдаёт топ в `agent_reports`,
откуда `generate_constitutions._get_longitudinal_context` кладёт их в промпт
генератора конституций.

Сырой Spearman-`p` на суточных рядах **катастрофически мискалиброван**: ряды
сильно автокоррелированы, и два независимо «гуляющих» графика дают огромную
ложную значимость (ловушка Dean & Dunsmuir). Пример — пара `hrv↔sleep_rem`
с лагом 1 и сильно заниженным сырым `p`: сдвиговый тест проверяет значимость
с учётом серийной зависимости. Плюс две беды: **тавтологии** (sleep_score
считается из стадий сна; readiness — из hrv/resting_hr) и **trend-confounds** в
лабах (два лабораторных показателя, каждый из которых ползёт по фазам болезни, дают ложную корреляцию между собой). Всё это
немаркированно утекало в генератор как «ключевые корреляции».

## Решение

Модуль `correlation_gate.py` стоит между расчётом корреляций и `build_ai_summary`.
Один публичный вход — `gate_correlations(daily_df, labs_df, corr_all, lab_corr)` —
возвращает те же таблицы с колонками `gate_pass`/`p_perm`/`derived`/`coverage`
(daily) и `gate_pass`/`p_perm`/`r_detrended` (lab). В конституции уходит только
`gate_pass`.

Метод (см. спеку
`docs/explanation/signal_validation_lifecycle.md`):

- **daily↔daily** — masked циркулярно-сдвиговый permutation-null **без импутации**
  (в каждое сравнение входят только реально измеренные дни) + BH-FDR по семье;
  плюс фильтр `derived` (метрика предсказывается из остальных с R²≥0.9 ИЛИ входит
  в список Oura-композитов) и порог покрытия. Импутацию пробовали (быстрее через
  FFT) и **отвергли**: на неполных рядах она искажает null и убивает реальные связи.
- **lab↔daily** — линейный детренд обоих рядов по времени (снимает общий тренд
  болезни), затем permutation + порог n.

## Как встроено

- `longitudinal_analysis._apply_gate()` — обёртка, вынесена из `run()` ради
  тестируемости; зовёт гейт, при сбое **деградирует громко** (`gate_applied=False`,
  job не падает, корреляции откатываются к негейтованным).
- `run()` зовёт `_apply_gate` сразу после расчёта корреляций, до Excel и саммари.
- `build_ai_summary` отбирает по `gate_pass` (с fallback на legacy `strong&significant`,
  если гейт-колонок нет) и пишет `summary["gate"]` с meta.
- Excel-лист «Корреляции» и stdout метят вердикт гейта (прошёл / фантом / derived /
  trend), чтобы человеческий вид не расходился с тем, что видит модель.

## Гигиена (почему так, а не иначе)

- **Датчик на деградацию.** `integrity_tests.check_longitudinal_gate_applied`
  читает `summary["gate"].gate_applied` и ворнит, если гейт не сработал — иначе
  это был бы «детект без доставки» (флаг есть, никто не читает).
- **Единый источник окна.** `LAB_WINDOW_DAYS` живёт в `longitudinal_analysis` и
  инжектится в гейт параметром — чтобы парность лаб↔daily не разъехалась между
  анализом и гейтом (split-brain).
- Дополняет `epistemic_discipline` с другой стороны: тот дисциплинирует *язык*
  LLM, гейт — *вход* (не даёт скормить генератору статистический мусор).

## Активация

Гейт включается, когда `longitudinal_analysis` прогонится на новом коде: launchd
`com.larry.health.longitudinal` (вс 03:00) или вручную
`python3.11 longitudinal_analysis.py`. До первого прогона свежее саммари без поля
`gate`, и датчик ворнит — это ожидаемо.

## Тесты

- `tests/unit/test_correlation_gate.py` — посаженная реальная связь проходит,
  два независимых AR(1)-ряда (фантом) не проходят, derived исключается.
- `tests/integration/test_longitudinal_gate.py` — в саммари только `gate_pass`,
  legacy-ветка, громкая деградация, единый источник окна.

## Ограничения и follow-ups

- Детренд лабов **линейный** — нелинейные фазовые эффекты не снимаются.
- Фильтр `derived` через R² **консервативен**: при сильной коллинеарности панели
  может над-исключать (направление безопасное — отбрасывает, не выдумывает).
- Гейт применяется к longitudinal-корреляциям; более широкий замысел
  (жизненный путь произвольного сигнала: рыбалка → критик → подтверждение на
  будущих данных) описан в `docs/explanation/signal_validation_lifecycle.md` и
  пока не реализован.

## Карта файлов

| Что | Где |
|---|---|
| Гейт (один публичный вход) | `correlation_gate.py::gate_correlations` |
| Обёртка + деградация | `longitudinal_analysis.py::_apply_gate` |
| Отбор в саммари | `longitudinal_analysis.py::build_ai_summary` |
| Датчик | `integrity_tests.py::check_longitudinal_gate_applied` |
| Источник окна лаба | `longitudinal_analysis.py::LAB_WINDOW_DAYS` |

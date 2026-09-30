[English](record_analyte_norm_verdict.en.md) · **Русский**

# Как отложить или закрыть вопрос о норме аналита

Ночной датчик `check_norm_coverage` печатает аналиты, измеренные ≥ `norm.coverage_min_n` раз
без нормы ни в одном доме (порог из документа · референс на бланке · `lab_refs`). Если ответ
«нормы нет по решению» или «ждём врача до даты» — запиши вердикт, иначе строка будет
приходить ежедневно как новая (прецедент: `Chol_HDL_ratio`, `Albumin_Globulin_ratio` — бланк клиники
печатает их без Normal values).

На Studio (`~/health_scripts`, health.db — только там):

```bash
/opt/homebrew/bin/python3.11 -c "
import health_db as db
print(db.set_analyte_norm_verdict(
    'Chol_HDL_ratio',                       # имя нормализуется через lab_canon
    verdict='deferred',                     # deferred (ждёт оракула) | no_norm (нормы нет по решению)
    rationale='расчётное отношение; бланк референс не печатает; вопрос врачу',
    oracle='owner',                         # owner | doctor:<кто>
    review_at='2027-01-05'))"               # ОБЯЗАТЕЛЬНА и > decided_on: после неё датчик звонит снова
```

Проверить: `python3.11 -c "import health_db as db; print(db.analyte_norm_verdicts())"` — живые
вердикты; `python3.11 -c "import integrity_tests as I; print(I.check_norm_coverage())"` — счётчик
без этого аналита. Вид, непустота обоснования и `review_at > decided_on` — CHECK в SQLite.

Почему дата обязательна даже у `no_norm`: лаборатория может начать печатать референс, и
вердикт не должен пережить предмет (§18). Истёкший вердикт читатель не отдаёт — аналит
возвращается в датчик сам. Объяснение — `docs/explanation/norm_kinds.md`.

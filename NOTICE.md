[English](NOTICE.en.md) · **Русский**

# Лицензия и сторонние данные

Код проекта распространяется по Apache License 2.0 — полный текст в `LICENSE` (решение владельца
2026-09-23). Условия ниже относятся к чужим данным, а не к коду.

## Сторонние данные и их условия

Проект использует внешние справочники. Здесь — что из них можно распространять вместе с кодом.
Нормативный список путей, которые НЕ едут в открытый репозиторий, — `publication_zones.yaml`.

| Источник | Что в проекте | Условия | Едет в публикацию |
|---|---|---|---|
| NCI CTCAE v5.0 / v6.0 | снимки xlsx и выведенные из них JSON (`data/norm_docs/ctcae_v*.xlsx`, `ctcae_lab_v*.json`); карта термов `ctcae_lab_terms*.json` — наша работа (термин → аналит), без кодов | Текст NCI свободен от копирайта, просят указывать NCI как источник ([cancer.gov](https://www.cancer.gov/policies/copyright-reuse)). Но файлы несут коды MedDRA, а MedDRA лицензирует ICH/MSSO — условия для этих кодов не проверены | документ и снимок — нет: установка скачивает CTCAE у NCI сама (`scripts/install.py --fetch-ctcae`, решение владельца 2026-09-25); карта термов — да |
| EFLM Biological Variation Database | снимок API (`data/norm_docs/eflm_*`) | «You may not, except with our express written permission, distribute or commercially exploit the content» ([biologicalvariation.eu/disclaimer](https://biologicalvariation.eu/disclaimer), прочитано 2026-09-23). При использовании — ссылка Aarsand AK et al., The EFLM Biological Variation Database | нет; установка берёт данные из API сама или получает разрешение EFLM |
| NCI Thesaurus (коды NCIt в CTCAE) | коды концептов | CC BY 4.0 | да, с атрибуцией |
| OpenStreetMap (тропы пакета региона) | только в приватном пакете региона | ODbL | нет (пакет региона приватный) |

## Сторонний код и промпты

| Источник | Что в проекте | Условия | Едет в публикацию |
|---|---|---|---|
| [WellAlly-health](https://github.com/huifer/WellAlly-health) (huifer / WellAlly Tech) | промпты врачей-специалистов (`specialists/*.md`, кроме `lifestyle_*` и `food_rule_generator.md`; `symptom_intake_system.txt` — свой) и текст промпта координатора (`consultation-coordinator.md`). Устройство консилиума — раунды, координатор, отбор состава — своё | MIT, текст ниже; обновления upstream отслеживает `check_wellally_updates.py` | да, с этим уведомлением |

### WellAlly-health — MIT License

```
MIT License

Copyright (c) 2026 WellAlly Tech

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

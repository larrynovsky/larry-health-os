[English](refresh_trail_list.en.md) · **Русский**

# How-to: обновить список троп (пакет региона, ключ `trails`)

Тропы — данные региона дома в `private/region.yaml` (ключ `trails`), читает их `trails.py`
через `region_pack`. Источник — Overpass (OSM). Ре-скрап нечастый.

## Когда
- Список приелся / хочешь больше троп.
- Сменить радиус от дома (текущие радиус и число троп записаны в самом пакете).

## Шаги

1. Скрипт-скрапер (эфемерный) кладём на Studio (адрес — `private/infra.yaml`, `studio_ssh`):
   ```
   scp /tmp/overpass_trails.py <studio_ssh>:/tmp/
   ```
   Параметры вверху скрипта: `HOME=(lat, lon)` — дом из `system_config` (`location.home_lat/lon`),
   `RADIUS_KM`, `BBOX` (пересчитай под радиус: lat±(radius/111), lon±(radius/(111·cos lat))).

2. Запусти на Studio (у него есть сеть; Overpass бывает медленным — есть ретрай по зеркалам):
   ```
   ssh <studio_ssh> "nohup /opt/homebrew/bin/python3.11 /tmp/overpass_trails.py > /tmp/trails_out.txt 2>&1 &"
   ```
   Через ~1–2 мин: `ssh <studio_ssh> "cat /tmp/trails_out.txt"`. Первая строка — TOTAL, дальше JSON-строки.

3. Отформатируй топ-N (по близости) в список `{name, km, note}`:
   - km санитизируй (OSM иногда даёт мусор типа 4780 — обнуляй, оставляй только 0<km<40);
   - имена ТОЛЬКО латинские (без самодельной транслитерации → ошибки);
   - note = дистанция от дома.

4. Впиши в `trails:` в `private/region.yaml` (на MacBook). Это приватная зона — в открытый
   репозиторий список не едет.

5. Прогони `pytest tests/unit/test_trails.py -q` → all-pass. Коммить с MacBook (деплой авто).

## Мины
- Overpass 406 → нужен `User-Agent` заголовок. 504 → зеркало занято, ретрай (kumi.systems).
- Гейт-кулдаун per-тропа: больше троп = реже повтор. 50 троп ≈ повтор раз в ~50 выходных.
- `pick_trail(day_index)` = детерминированная ротация по ordinal даты; порядок в списке важен.

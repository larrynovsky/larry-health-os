[English](record_surveillance_decision.en.md) · **Русский**

# Как записать решение врача по наблюдению

Когда врач сказал «X не делать до Y» / «X делать раз в год» — запиши решение.
Возможный сбой: если отсрочка не попала в контекст, агент может снова предложить
это обследование. Активное решение должно доходить до каждого читателя, который даёт рекомендации.

На Studio (`~/health_scripts`, health.db — только там):

```bash
/opt/homebrew/bin/python3.11 -c "
import health_db as db
print(db.record_surveillance_decision(
    topic='example_imaging',                # slug темы — по нему прежнее решение уходит в retired
    title='Обследование X (условный пример)',
    decision='defer',                       # defer | do | stop
    decided_by='лечащий врач',
    source='owner_word:ГГГГ-ММ-ДД',         # провенанс: owner_word:<дата> | doc:<файл> | encounter:<id>
    decided_on='ГГГГ-ММ-ДД',                # дата решения; не названа — дата записи
    valid_until='ГГГГ-12-31',               # до какого срока; None = бессрочно
    rationale='врач сказала в этом году не делать'))"
```

Проверить, что читатели видят: `python3.11 -c "import gp_context as g; print(chr(10).join(g._build_surveillance_decisions_block()))"`.
Истёкшее (`valid_until` в прошлом) само выпадает из активных — тогда тема снова открыта для агентов;
это намеренно: решение врача имеет срок, а не вечность.

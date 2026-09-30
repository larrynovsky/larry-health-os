"""Счётчик тенантов под решением «молчание признаком не считать» (§18).

Решение владельца 2026-08-02 сняло из service_trouble ветку, ловившую замолчавшего
человека. Основание — «второй тенант один и досягаем лично» — утверждение об окружении:
верно сейчас, гасителя не имеет. Счётчик и есть тот гаситель.

Оракулы на чистом ядре: обход launchd Studio-only и на тест-хосте не идёт.
"""
from plist_env_liveness import select_tenant_dirs
from secrets_paths import is_owner_dir

OWNER = "/Users/zz/health"
PARTNER = "/Users/zz/health_partner"


def _job(data_dir=None):
    d = {"ProgramArguments": ["python3.11", "x.py"]}
    if data_dir:
        d["EnvironmentVariables"] = {"HEALTH_DATA_DIR": data_dir}
    return d


def _partners(plists):
    return sorted(d for d in select_tenant_dirs(plists) if not is_owner_dir(d))


def test_today_is_exactly_one_partner():
    """Замер 02.08 воспроизведён: владелец + один партнёр = один не-владелец."""
    jobs = [("com.larry.health.bot", _job(OWNER)),
            ("com.larry.health.bot.partner", _job(PARTNER)),
            ("com.larry.health.watcher.partner", _job(PARTNER))]
    assert _partners(jobs) == [PARTNER]


def test_second_partner_is_seen():
    """ПОЗИТИВ: появился тенант в другом месте — счётчик обязан его заметить."""
    jobs = [("com.larry.health.bot", _job(OWNER)),
            ("com.larry.health.bot.partner", _job(PARTNER)),
            ("com.larry.health.bot.mama", _job("/Users/zz/health_mama"))]
    assert len(_partners(jobs)) == 2


def test_owner_is_not_counted_as_tenant():
    """НЕГАТИВ: владелец сам себе не тенант — иначе счётчик красен с рождения."""
    assert _partners([("com.larry.health.bot", _job(OWNER))]) == []


def test_job_without_tenant_is_not_counted():
    """Джоба без HEALTH_DATA_DIR тенанта не объявляет — считать нечего."""
    assert _partners([("com.larry.health.daily", _job())]) == []


def test_same_tenant_in_many_jobs_counts_once():
    """Тенант считается по каталогу, а не по числу джоб, которые его обслуживают."""
    jobs = [("a", _job(PARTNER)), ("b", _job(PARTNER)), ("c", _job(PARTNER))]
    assert _partners(jobs) == [PARTNER]

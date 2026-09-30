"""Позитивный контроль: _seed_hae_registry реконсилит залежавшийся 'new' у seeded-метрик,
но не трогает истинно новые (вне сида).

Дефект: checker регистрирует метрику как 'new' раньше, чем появляется seed-запись; INSERT
OR IGNORE статус существующих не меняет → seeded-метрика (напр. headphone) вечно 'new' и
спам-алертит, игнорируя объявленный в сиде 'tracked'.
"""
import health_db


def test_seed_reconciles_stale_new(db):
    # seeded-метрика (в _HAE_SEED как 'tracked'), залежавшаяся в 'new'
    db.execute("UPDATE hae_metric_registry SET status='new' WHERE metric_name='headphone_audio_exposure'")
    health_db._seed_hae_registry()
    reg = health_db.get_hae_registry()
    assert reg["headphone_audio_exposure"]["status"] == "tracked", \
        "seed обязан перекрыть залежавшийся 'new' своим объявленным статусом"


def test_seed_leaves_unseeded_new_untouched(db):
    # метрика ВНЕ сида — истинно новая, seed её не трогает (легитимный алерт сохраняется)
    db.execute("INSERT OR IGNORE INTO hae_metric_registry (metric_name, status) VALUES ('totally_new_xyz', 'new')")
    health_db._seed_hae_registry()
    reg = health_db.get_hae_registry()
    assert reg["totally_new_xyz"]["status"] == "new", \
        "метрику вне сида seed трогать не должен"

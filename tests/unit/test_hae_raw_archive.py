"""Сырой архив Apple Health сжимается, а не удаляется (26.09): от его глубины зависит правило
«пустая колонка = прибора нет». Проверяем: старое — в .gz, свежее — нетронуто, глубина (самый
старый файл) та же, судья читает сжатое тем же путём."""
import json
from datetime import date

import hae_checker as hc


def _f(d, stamp, metrics):
    p = d / f"HealthAutoExport-REST-{stamp}T000000.json"
    p.write_text(json.dumps({"data": {"metrics": metrics}}), encoding="utf-8")
    return p


def test_old_payload_compressed_new_kept_depth_preserved(tmp_path):
    m = [{"name": "step_count", "data": [{"date": "2026-07-06 10:00:00 +0300", "qty": 5}]}]
    _f(tmp_path, "20260706", m)
    _f(tmp_path, "20260925", m)
    oldest_before = hc.raw_archive_files(tmp_path)[0].name.split("-REST-")[1][:8]
    out = hc.compress_raw_archive(tmp_path, keep_days=14, today=date(2026, 9, 26))
    files = hc.raw_archive_files(tmp_path)
    assert out["compressed"] == 1
    assert [p.name.endswith(".gz") for p in files] == [True, False]
    assert files[0].name.split("-REST-")[1][:8] == oldest_before == "20260706"
    assert hc._load_payload(files[0]) == {"data": {"metrics": m}}


def test_second_run_is_idempotent(tmp_path):
    _f(tmp_path, "20260706", [])
    hc.compress_raw_archive(tmp_path, today=date(2026, 9, 26))
    assert hc.compress_raw_archive(tmp_path, today=date(2026, 9, 26))["compressed"] == 0

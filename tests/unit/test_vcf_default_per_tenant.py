"""Путь к VCF владельца не становится меткой генома другого тенанта.

Инцидент (найден 02.11.2026): private/local_samples.yaml — файл владельца, один на репозиторий,
а vcf_import_pipeline._default_vcf читал его на любых данных. Phase E на данных партнёра 13.08
завела в его базе genome_imports с путём к VCF владельца; под этой записью посчитаны 772 шкалы
и 8 фармакофенотипов партнёра (по его же генотипам — ложной была только метка)."""
from __future__ import annotations

import pytest

import health_db
import vcf_import_pipeline as vip


@pytest.mark.unit
def test_owner_samples_file_is_ignored_on_another_tenants_data(tmp_path, monkeypatch):
    samples = tmp_path / "local_samples.yaml"
    samples.write_text("genome_vcf: /owner/genome_OWNER.vcf\n", encoding="utf-8")
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health_partner" / "data" / "health.db")

    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    assert vip._default_vcf(samples) == tmp_path / "health_partner" / "data" / "genome.vcf"

    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))      # данные владельца
    assert str(vip._default_vcf(samples)) == "/owner/genome_OWNER.vcf"

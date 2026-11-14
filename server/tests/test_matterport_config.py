"""Part 1 config points at the demo Matterport model and its start pano."""

import importlib

import pytest

import server.config as config


def reload_config(monkeypatch, **env):
    for key in ("MATTERPORT_MODEL_ID", "PANO_ID", "PANO_LABEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    importlib.reload(config)


def test_defaults_target_rabinowitz_hall_start_pano(monkeypatch):
    cfg = reload_config(monkeypatch)
    s = cfg.Settings()
    assert s.matterport_model_id == "P3S6VUTP3qB"
    assert s.default_pano_id == "1k478kza39tbfu1uhbttg5ybb"
    assert s.default_pano_label == "Rabinowitz Hall"
    assert s.pano_id is None  # env-only; defaults are fallbacks


def test_env_overrides_defaults(monkeypatch):
    cfg = reload_config(
        monkeypatch, MATTERPORT_MODEL_ID="6sd6A5r5EVp", PANO_ID="abc", PANO_LABEL="Main lobby"
    )
    s = cfg.Settings()
    assert (s.matterport_model_id, s.pano_id, s.pano_label) == ("6sd6A5r5EVp", "abc", "Main lobby")


def test_pano_id_must_still_be_a_directory_name(monkeypatch):
    cfg = reload_config(monkeypatch, PANO_ID="../etc")
    with pytest.raises(ValueError):
        cfg.Settings()


def test_model_id_is_validated(monkeypatch):
    cfg = reload_config(monkeypatch, MATTERPORT_MODEL_ID="not a model!")
    with pytest.raises(ValueError):
        cfg.Settings()


def test_pano_metadata_uses_configured_pano_and_faces(monkeypatch, tmp_path):
    from server.server import pano_metadata

    cfg = reload_config(monkeypatch)
    pano = tmp_path / "pano" / cfg.Settings().default_pano_id
    pano.mkdir(parents=True)
    for i in range(6):
        (pano / f"face_{i}.jpg").write_bytes(b"\xff\xd8x")
    meta = pano_metadata(cfg.Settings(data_dir=tmp_path))
    assert meta["pano_id"] == "1k478kza39tbfu1uhbttg5ybb"
    assert meta["label"] == "Rabinowitz Hall"
    assert len(meta["faces"]) == 6
    assert meta["model_id"] == "P3S6VUTP3qB"


def test_numeric_sweep_label_is_not_spoken(monkeypatch, tmp_path):
    import json

    from server.server import pano_metadata

    cfg = reload_config(monkeypatch)
    (tmp_path / "matterport").mkdir()
    (tmp_path / "matterport" / "panoLocations.json").write_text(
        json.dumps({"panoLocations": [{"id": "1k478kza39tbfu1uhbttg5ybb", "label": "0"}]})
    )
    assert pano_metadata(cfg.Settings(data_dir=tmp_path))["label"] == "Rabinowitz Hall"

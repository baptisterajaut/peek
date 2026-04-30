from pathlib import Path

from peek.config import Config


def test_load_returns_defaults_when_missing(tmp_path: Path):
    cfg = Config.load(tmp_path / "nope.conf")
    assert cfg.host == "http://localhost:8080"
    assert cfg.personality == "default"
    assert cfg.thinking is None


def test_write_default_then_reload(tmp_path: Path):
    p = tmp_path / "peek.conf"
    cfg = Config()
    assert cfg.write_default_if_missing(p) is True
    assert cfg.write_default_if_missing(p) is False  # idempotent

    loaded = Config.load(p)
    assert loaded.host == "http://localhost:8080"


def test_overrides_loaded(tmp_path: Path):
    p = tmp_path / "peek.conf"
    p.write_text(
        "[server]\nhost = http://lab:9090\nverify_ssl = false\n"
        "[model]\nmodel = qwen3-14b\nthinking = true\ntemperature = 0.3\n"
        "[personality]\nname = creative\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    assert cfg.host == "http://lab:9090"
    assert cfg.verify_ssl is False
    assert cfg.model == "qwen3-14b"
    assert cfg.thinking is True
    assert cfg.temperature == 0.3
    assert cfg.personality == "creative"

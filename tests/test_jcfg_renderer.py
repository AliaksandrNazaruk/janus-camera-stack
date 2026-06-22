"""Tests for app/services/jcfg_renderer.py — template substitution + backup."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import jcfg_renderer, secret_store


@pytest.fixture
def fake_janus_layout(tmp_path, monkeypatch):
    """Build fake JANUS_CFG_DIR + template dir + isolate secret store."""
    cfg_dir = tmp_path / "janus_cfg"
    plugins_dir = tmp_path / "janus_lib" / "plugins"
    transports_dir = tmp_path / "janus_lib" / "transports"
    tpl_dir = tmp_path / "templates"
    cfg_dir.mkdir()
    plugins_dir.mkdir(parents=True)
    transports_dir.mkdir(parents=True)
    tpl_dir.mkdir()

    # Isolate secret store
    sec_file = tmp_path / "secrets.env"
    monkeypatch.setattr(secret_store, "SECRETS_FILE", sec_file)
    monkeypatch.setattr(secret_store, "TIMESTAMPS_FILE", tmp_path / ".ts")

    # Make renderer's path detection return our fake paths
    fake_paths = jcfg_renderer.JanusPaths(
        cfg_dir=cfg_dir,
        plugins_dir=plugins_dir,
        transports_dir=transports_dir,
    )
    monkeypatch.setattr(jcfg_renderer, "detect_janus_paths", lambda: fake_paths)
    monkeypatch.setattr(jcfg_renderer, "detect_template_dir", lambda: tpl_dir)
    monkeypatch.setattr(jcfg_renderer, "detect_primary_iface", lambda: "eth-test")
    return cfg_dir, tpl_dir, sec_file


def test_render_substitutes_placeholders(fake_janus_layout):
    cfg_dir, tpl_dir, sec_file = fake_janus_layout
    (tpl_dir / "test.jcfg.template").write_text(
        'admin_secret = "@JANUS_ADMIN_SECRET@"\n'
        'iface = "@ICE_ENFORCE_LIST@"\n'
        'plugins_folder = "@JANUS_PLUGINS_DIR@"\n'
    )
    sec_file.write_text("JANUS_ADMIN_SECRET=my-admin-key\n")

    result = jcfg_renderer.render()
    rendered_file = cfg_dir / "test.jcfg"
    assert rendered_file in result.rendered
    text = rendered_file.read_text()
    assert 'admin_secret = "my-admin-key"' in text
    assert 'iface = "eth-test"' in text
    assert "@JANUS_ADMIN_SECRET@" not in text   # all substituted


def test_render_uses_replace_me_when_secret_missing(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    (tpl_dir / "x.jcfg.template").write_text('key = "@STREAMING_ADMIN_KEY@"\n')
    result = jcfg_renderer.render()
    text = (cfg_dir / "x.jcfg").read_text()
    assert 'key = "REPLACE_ME"' in text


def test_render_preserves_existing_nat_mapping(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    (cfg_dir / "janus.jcfg").write_text('nat_1_1_mapping = "203.0.113.42"\n')
    (tpl_dir / "janus.jcfg.template").write_text(
        'nat_1_1_mapping = "@NAT_1_1_MAPPING@"\n'
    )
    jcfg_renderer.render()
    text = (cfg_dir / "janus.jcfg").read_text()
    assert 'nat_1_1_mapping = "203.0.113.42"' in text


def test_render_uses_explicit_nat_mapping_override(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    (tpl_dir / "janus.jcfg.template").write_text(
        'nat_1_1_mapping = "@NAT_1_1_MAPPING@"\n'
    )
    jcfg_renderer.render(nat_mapping="198.51.100.7")
    text = (cfg_dir / "janus.jcfg").read_text()
    assert 'nat_1_1_mapping = "198.51.100.7"' in text


def test_render_backs_up_existing_file_once(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    target = cfg_dir / "x.jcfg"
    target.write_text("ORIGINAL CONTENT")
    (tpl_dir / "x.jcfg.template").write_text("NEW CONTENT")

    jcfg_renderer.render()
    backup = cfg_dir / "x.jcfg.pre-render"
    assert backup.exists()
    assert backup.read_text() == "ORIGINAL CONTENT"
    assert (cfg_dir / "x.jcfg").read_text() == "NEW CONTENT"

    # Second render does NOT overwrite backup
    target.write_text("MODIFIED-2")
    (tpl_dir / "x.jcfg.template").write_text("NEW CONTENT 2")
    jcfg_renderer.render()
    assert backup.read_text() == "ORIGINAL CONTENT"   # still original


def test_render_creates_streams_d_subdir(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    (tpl_dir / "x.jcfg.template").write_text("foo")
    jcfg_renderer.render()
    streams = cfg_dir / "streams.d"
    assert streams.is_dir()


def test_render_fails_if_no_janus_paths(fake_janus_layout, monkeypatch):
    monkeypatch.setattr(jcfg_renderer, "detect_janus_paths", lambda: None)
    with pytest.raises(RuntimeError, match="Janus install not found"):
        jcfg_renderer.render()


def test_render_fails_if_no_template_dir(fake_janus_layout, monkeypatch):
    monkeypatch.setattr(jcfg_renderer, "detect_template_dir", lambda: None)
    with pytest.raises(RuntimeError, match="Template dir not found"):
        jcfg_renderer.render()


def test_rendered_file_mode_640(fake_janus_layout):
    cfg_dir, tpl_dir, _ = fake_janus_layout
    (tpl_dir / "x.jcfg.template").write_text("foo")
    jcfg_renderer.render()
    mode = os.stat(cfg_dir / "x.jcfg").st_mode & 0o777
    assert mode == 0o640

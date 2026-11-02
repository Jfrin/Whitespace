from pathlib import Path

import pytest
import yaml

from whitespace.serve.settings import read_settings, save_settings


@pytest.fixture
def files(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# keep me\nWHITESPACE_CONTACT=old@example.com  # contact\nOTHER=untouched\n",
        encoding="utf-8",
    )
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n  steam_reviews:\n    appids: [1, 2] # games\n  reddit:\n    enabled: false\n",
        encoding="utf-8",
    )
    return env, sources


def test_save_settings_preserves_other_values_and_roundtrips_secrets(files):
    env, sources = files
    key = "secret $x 'quote' \\ slash # value"
    save_settings(
        env, sources, [3, 4], {"WHITESPACE_CONTACT": "new@example.com", "AZURE_OPENAI_API_KEY": key}
    )
    text = env.read_text(encoding="utf-8")
    assert "# keep me" in text and "# contact" in text and "OTHER=untouched" in text
    assert read_settings(env)["AZURE_OPENAI_API_KEY"] == key
    assert yaml.safe_load(sources.read_text())["sources"]["steam_reviews"]["appids"] == [3, 4]
    assert "# games" in sources.read_text() and "reddit:" in sources.read_text()


@pytest.mark.parametrize(
    "config", ["sources: {}\n", "sources:\n  steam_reviews:\n    appids:\n      - 1\n"]
)
def test_invalid_source_configuration_does_not_change_files(files, config):
    env, sources = files
    sources.write_text(config, encoding="utf-8")
    old_env = env.read_text()
    with pytest.raises(ValueError):
        save_settings(env, sources, [3], {"WHITESPACE_CONTACT": "new"})
    assert env.read_text() == old_env and sources.read_text() == config


def test_invalid_setting_does_not_change_files(files):
    env, sources = files
    before = (env.read_text(), sources.read_text())
    with pytest.raises(ValueError):
        save_settings(env, sources, [3], {"WHITESPACE_CONTACT": "a\nINJECTED=1"})
    assert (env.read_text(), sources.read_text()) == before


def test_all_duplicate_env_assignments_are_updated(files):
    env, sources = files
    env.write_text("AZURE_OPENAI_API_KEY=old\nAZURE_OPENAI_API_KEY=older\n", encoding="utf-8")
    save_settings(env, sources, [3], {"AZURE_OPENAI_API_KEY": "new"})
    assert env.read_text().count("AZURE_OPENAI_API_KEY='new'") == 2
    assert read_settings(env)["AZURE_OPENAI_API_KEY"] == "new"


def test_windows_line_endings_are_preserved(files):
    env, sources = files
    env.write_bytes(b"WHITESPACE_CONTACT=old\r\nOTHER=value\r\n")
    sources.write_bytes(b"sources:\r\n  steam_reviews:\r\n    appids: [1]\r\n")
    save_settings(env, sources, [2], {"WHITESPACE_CONTACT": "new", "WHITESPACE_UI_LIMIT": "42"})
    assert (
        env.read_bytes()
        == b"WHITESPACE_CONTACT='new'\r\nOTHER=value\r\nWHITESPACE_UI_LIMIT='42'\r\n"
    )
    assert sources.read_bytes() == b"sources:\r\n  steam_reviews:\r\n    appids: [2]\r\n"


def test_local_console_persists_and_reloads_settings(monkeypatch, tmp_path, files):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    env, sources = files
    monkeypatch.setenv("WHITESPACE_UI_ENV_PATH", str(env))
    monkeypatch.setenv("WHITESPACE_UI_SOURCES_PATH", str(sources))
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path("src/whitespace/serve/app.py").resolve(), default_timeout=20).run()
    assert not app.exception
    app.text_input[0].set_value("123, 456").run()
    app.text_input[1].set_value("new@example.com").run()
    app.selectbox[0].select("rules").run()
    next(b for b in app.button if b.label == "Save settings").click().run()
    assert not app.exception and any("Settings saved" in x.value for x in app.success)
    app2 = AppTest.from_file(
        Path("src/whitespace/serve/app.py").resolve(), default_timeout=20
    ).run()
    assert not app2.exception
    assert app2.text_input[0].value == "123, 456"
    assert app2.text_input[1].value == "new@example.com"
    assert app2.selectbox[0].value == "rules"

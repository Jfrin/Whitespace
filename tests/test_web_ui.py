import pytest


def test_web_ui_validation_and_start(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from whitespace.serve.jobs import JobRunner

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(JobRunner, "start", lambda self, stages, appids, env: True)
    app = AppTest.from_file("src/whitespace/serve/app.py", default_timeout=20).run()
    assert not app.exception
    app.text_input[0].set_value("invalid").run()
    app.button[0].click().run()
    assert any("Steam app IDs" in error.value for error in app.error)
    app.text_input[0].set_value("413150").run()
    next(button for button in app.button if button.label == "Clean").click().run()
    assert not app.exception

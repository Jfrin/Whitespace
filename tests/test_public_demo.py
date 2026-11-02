import json
from pathlib import Path

import pytest

from whitespace.serve.public_data import filter_items, load_demo

DATASET = Path("demo/public.json")


def test_packaged_data_is_synthetic_and_only_public_fields_are_exposed(tmp_path):
    label, description, items = load_demo(DATASET)
    assert "synthetic" in label.lower() and "fictional" in description.lower()
    assert len(items) > 0
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["items"][0]["secret"] = "must not appear"
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "secret" not in load_demo(path)[2][0]


def test_filters_combine_and_handle_empty_results():
    _, _, items = load_demo(DATASET)
    assert filter_items(items, [], [], [], 0) == items
    found = filter_items(items, ["Hollow Orchard"], ["Simulation"], ["feature_wish"], 0.8)
    assert [item["summary"] for item in found] == ["shared farm progression"]
    assert filter_items(items, ["Hollow Orchard"], ["Roguelike"], [], 0) == []
    assert filter_items(items, [], [], [], 1) == []


@pytest.mark.parametrize("value", [-1, 1.1, True, "0.8", float("nan")])
def test_invalid_confidence_is_rejected(tmp_path, value):
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["items"][0]["confidence"] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="confidence"):
        load_demo(path)


def test_public_streamlit_app_filters_and_has_no_pipeline_controls():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        Path("src/whitespace/serve/public.py").resolve(), default_timeout=20
    ).run()
    assert not app.exception
    assert "Synthetic example data" in app.info[0].value
    assert app.metric[0].value == "5"
    assert not app.text_input and not app.button
    app.multiselect[0].set_value(["Hollow Orchard"]).run()
    assert not app.exception and app.metric[0].value == "2"
    assert any("shared farm progression" in heading.value for heading in app.subheader)
    app.multiselect[1].set_value(["Roguelike"]).run()
    assert app.metric[0].value == "0"
    assert any("No examples match" in info.value for info in app.info)

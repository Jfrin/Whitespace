import json

from whitespace.cluster.text import context_text, load_cluster_inputs


def test_context_text_prefixes_lowercased_genres_and_caps_them():
    out = context_text("faster progression", ["Action", "RPG"])
    assert out == "action, rpg: faster progression"
    assert context_text("x", ["A", "B", "C", "D"]) == "a, b, c: x"
    assert context_text("faster progression", None) == "faster progression"
    assert context_text("x", ["", "  "]) == "x"


def test_same_want_in_different_genres_embeds_differently():
    assert context_text("faster progression", ["Roguelike"]) != context_text(
        "faster progression", ["Strategy"]
    )


def test_load_cluster_inputs_reads_context_from_the_db(db, add_want):
    a = add_want("1", summary="faster progression")
    b = add_want("2", summary="photo mode")
    db.conn.execute("UPDATE want_item SET context_genres=? WHERE id=?", (json.dumps(["RPG"]), a))
    db.conn.commit()
    assert load_cluster_inputs(db) == [(a, "rpg: faster progression"), (b, "photo mode")]

import pytest

from whitespace.cli import main
from whitespace.cluster.drift import cosine, match_clusters
from whitespace.score.gap import NicheDemand, SupplyGame, demand_score, gap_score, supply_pressure

DEMAND = NicheDemand(
    weighted_mentions=200,
    growth_rate=0.5,
    avg_intensity=2.5,
    communities=4,
    dissatisfaction_rate=0.4,
)
GOOD_GAME = SupplyGame(review_score=0.95, review_count=20000, age_years=0.5)
OLD_BAD = SupplyGame(review_score=0.4, review_count=30, age_years=10)


def test_more_demand_means_higher_score():
    weak = NicheDemand(20, 0.0, 1.5, 1, 0.1)
    assert demand_score(DEMAND) > demand_score(weak)


def test_strong_supply_lowers_gap_and_weak_supply_barely_counts():
    assert supply_pressure([GOOD_GAME]) > 0.8
    assert supply_pressure([OLD_BAD]) < 0.05
    assert gap_score(DEMAND, [OLD_BAD]) > gap_score(DEMAND, [GOOD_GAME]) * 1.5
    assert gap_score(DEMAND, []) > gap_score(DEMAND, [OLD_BAD])


def test_dissatisfaction_raises_gap():
    calm = NicheDemand(200, 0.5, 2.5, 4, 0.0)
    assert gap_score(DEMAND, [OLD_BAD]) > gap_score(calm, [OLD_BAD])


def test_growth_is_clamped():
    wild = NicheDemand(200, 50.0, 2.5, 4, 0.4)
    capped = NicheDemand(200, 2.0, 2.5, 4, 0.4)
    assert demand_score(wild) == pytest.approx(demand_score(capped))


def test_cosine_and_cluster_matching():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([0, 0], [1, 1]) == 0.0
    old = {1: [1.0, 0.0], 2: [0.0, 1.0]}
    new = {10: [0.98, 0.1], 11: [-1.0, -1.0]}
    assert match_clusters(old, new) == {10: 1, 11: None}


def test_cli_stats_and_disabled_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    assert main(["stats"]) == 0 and "post" in capsys.readouterr().out
    assert main(["ingest", "reddit"]) == 2  # disabled in the shipped registry


def test_cli_context_and_catalog_run_on_an_empty_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    assert main(["context", "--context-config", "config/context.yaml"]) == 0
    assert "unresolved" in capsys.readouterr().out
    assert main(["catalog"]) == 0                      # nothing referenced -> no network calls
    assert "0 appids referenced" in capsys.readouterr().out
    assert main(["stats"]) == 0 and "catalog_game" in capsys.readouterr().out


def test_cli_gold_and_flags_on_an_empty_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    assert main(["gold-export"]) == 0
    assert (tmp_path / "gold_items.csv").exists()
    assert main(["gold-export", "--kind", "missed"]) == 0
    assert (tmp_path / "gold_missed.csv").exists()
    capsys.readouterr()
    assert main(["gold-score"]) == 0
    assert '"labeled": 0' in capsys.readouterr().out
    assert main(["gold-score", "--kind", "missed"]) == 0
    assert main(["context", "--refresh", "--context-config", "config/context.yaml"]) == 0


def test_cli_gold_untriggered_kind(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    assert main(["gold-export", "--kind", "untriggered"]) == 0
    assert (tmp_path / "gold_untriggered.csv").exists()
    capsys.readouterr()
    assert main(["gold-score", "--kind", "untriggered"]) == 0
    assert '"wish_rate": null' in capsys.readouterr().out

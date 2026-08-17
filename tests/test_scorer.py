from src.analysis.scorer import score_countries
from src.models import Classification, CountryVote, Direction, Resolution, Vote


def make_resolution(symbol, votes):
    return Resolution(
        symbol=symbol,
        title=f"Resolution {symbol}",
        subjects=["ISRAEL"],
        date="2024-01-01",
        summary="",
        votes=[CountryVote(country=c, vote=v) for c, v in votes],
    )


def make_classification(symbol, direction, confidence=0.9):
    return Classification(
        resolution_symbol=symbol, direction=direction, confidence=confidence, reasoning=""
    )


def test_yes_on_sympathetic_resolution_scores_positive():
    resolutions = [make_resolution("R1", [("USA", Vote.YES)])]
    classifications = {"R1": make_classification("R1", Direction.SYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    assert len(scores) == 1
    assert scores[0].country == "USA"
    assert scores[0].raw_score == 1
    assert scores[0].normalized_score == 100.0


def test_no_on_unsympathetic_resolution_scores_positive():
    # Voting NO on an anti-Israel resolution defends Israel's position -> +1.
    resolutions = [make_resolution("R1", [("USA", Vote.NO)])]
    classifications = {"R1": make_classification("R1", Direction.UNSYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    assert scores[0].raw_score == 1
    assert scores[0].normalized_score == 100.0


def test_yes_on_unsympathetic_resolution_scores_negative():
    resolutions = [make_resolution("R1", [("USA", Vote.YES)])]
    classifications = {"R1": make_classification("R1", Direction.UNSYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    assert scores[0].raw_score == -1
    assert scores[0].normalized_score == 0.0


def test_abstain_always_scores_zero():
    resolutions = [make_resolution("R1", [("USA", Vote.ABSTAIN)])]
    classifications = {"R1": make_classification("R1", Direction.UNSYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    assert scores[0].raw_score == 0
    assert scores[0].resolutions_counted == 0


def test_neutral_resolution_contributes_zero_but_counts_toward_n():
    resolutions = [
        make_resolution("R1", [("USA", Vote.YES)]),
        make_resolution("R2", [("USA", Vote.YES)]),
    ]
    classifications = {
        "R1": make_classification("R1", Direction.NEUTRAL),
        "R2": make_classification("R2", Direction.SYMPATHETIC),
    }

    scores = score_countries(resolutions, classifications)

    # raw_score: 0 (neutral) + 1 (sympathetic yes) = 1, over N=2 analyzed resolutions
    assert scores[0].raw_score == 1
    assert scores[0].normalized_score == 75.0  # (1 + 2) / 4 * 100


def test_unclassified_resolution_is_excluded_from_scoring():
    resolutions = [
        make_resolution("R1", [("USA", Vote.YES)]),
        make_resolution("R2", [("USA", Vote.NO)]),
    ]
    classifications = {"R1": make_classification("R1", Direction.SYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    # R2 has no classification, so only R1 counts: N=1, raw=1 -> 100
    assert scores[0].raw_score == 1
    assert scores[0].normalized_score == 100.0


def test_multiple_countries_ranked_by_normalized_score():
    resolutions = [make_resolution("R1", [("USA", Vote.YES), ("CUB", Vote.NO)])]
    classifications = {"R1": make_classification("R1", Direction.SYMPATHETIC)}

    scores = score_countries(resolutions, classifications)

    assert [s.country for s in scores] == ["USA", "CUB"]
    assert scores[0].normalized_score == 100.0
    assert scores[1].normalized_score == 0.0


def test_no_analyzed_resolutions_returns_empty():
    scores = score_countries([], {})
    assert scores == []

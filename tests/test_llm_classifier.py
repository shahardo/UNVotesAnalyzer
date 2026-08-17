import json
from unittest.mock import MagicMock, patch

from src.analysis.llm_classifier import classify_resolution
from src.config import OllamaConfig
from src.models import Direction, Resolution

CONFIG = OllamaConfig(host="http://localhost:11434", model="llama3.1:8b", temperature=0.0, timeout_seconds=30)


def make_resolution():
    return Resolution(
        symbol="A/RES/ES-10/21",
        title="Test resolution",
        subjects=["ISRAEL"],
        date="2024-01-01",
        summary="A test summary.",
        votes=[],
    )


def _mock_response(body_text: str):
    mock = MagicMock()
    mock.ok = True
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"response": body_text}
    return mock


@patch("src.analysis.llm_classifier.requests.post")
def test_classify_parses_valid_json(mock_post):
    mock_post.return_value = _mock_response(
        json.dumps({"direction": "sympathetic", "confidence": 0.8, "reasoning": "supports Israel"})
    )

    result = classify_resolution(CONFIG, make_resolution())

    assert result.direction == Direction.SYMPATHETIC
    assert result.confidence == 0.8
    assert mock_post.call_count == 1


@patch("src.analysis.llm_classifier.requests.post")
def test_classify_extracts_json_surrounded_by_prose(mock_post):
    mock_post.return_value = _mock_response(
        'Sure, here is my answer:\n{"direction": "neutral", "confidence": 0.5, "reasoning": "procedural"}\nHope that helps!'
    )

    result = classify_resolution(CONFIG, make_resolution())

    assert result.direction == Direction.NEUTRAL


@patch("src.analysis.llm_classifier.requests.post")
def test_classify_retries_once_then_succeeds(mock_post):
    mock_post.side_effect = [
        _mock_response("not json at all"),
        _mock_response(json.dumps({"direction": "unsympathetic", "confidence": 0.7, "reasoning": "..."})),
    ]

    result = classify_resolution(CONFIG, make_resolution())

    assert result.direction == Direction.UNSYMPATHETIC
    assert mock_post.call_count == 2


@patch("src.analysis.llm_classifier.requests.post")
def test_classify_falls_back_to_neutral_after_repeated_failures(mock_post):
    mock_post.return_value = _mock_response("still not json")

    result = classify_resolution(CONFIG, make_resolution())

    assert result.direction == Direction.NEUTRAL
    assert result.confidence == 0.0
    assert mock_post.call_count == 2

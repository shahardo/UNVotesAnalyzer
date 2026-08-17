"""Classifies a resolution's stance toward Israel using a local Ollama model."""
from __future__ import annotations

import json
import logging
import re

import requests

from src.config import OllamaConfig
from src.models import Classification, Direction, Resolution

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a neutral political analyst. You will be given the \
title, subjects, and summary of a United Nations General Assembly resolution \
that concerns Israel. Decide the resolution's DIRECTION: if the resolution is \
ADOPTED (passes), is that outcome good, bad, or neither for Israel's \
government and stated positions?

- "sympathetic": adoption favors Israel (e.g. condemns attacks on Israel, \
affirms Israel's security or right to self-defense, opposes boycotts of \
Israel).
- "unsympathetic": adoption is against Israel's stated positions (e.g. \
condemns Israeli policy or military action, supports Palestinian statehood \
claims against Israel's objection, calls for sanctions or investigations of \
Israel).
- "neutral": procedural, administrative, or does not clearly favor either \
side.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"direction": "sympathetic" | "neutral" | "unsympathetic", "confidence": <0..1 float>, "reasoning": "<one short sentence>"}
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_user_prompt(resolution: Resolution) -> str:
    return (
        f"Symbol: {resolution.symbol}\n"
        f"Title: {resolution.title}\n"
        f"Subjects: {', '.join(resolution.subjects)}\n"
        f"Summary: {resolution.summary or '(none provided)'}\n"
    )


def _extract_json(text: str) -> dict:
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object found in model output: {text!r}")
    return json.loads(match.group(0))


def ensure_model_available(config: OllamaConfig) -> None:
    """Pulls config.model if it isn't already present in Ollama.

    A missing model makes every /api/generate call 404, which the retry/
    fallback logic in classify_resolution quietly turns into an all-neutral
    classification for every resolution instead of a visible error -- so
    this check needs to run once up front, not be handled per-call."""
    response = requests.get(f"{config.host}/api/tags", timeout=config.timeout_seconds)
    response.raise_for_status()
    local_models = {m["name"] for m in response.json().get("models", [])}
    if config.model in local_models:
        return

    logger.warning("model %s not found in Ollama, pulling it now...", config.model)
    pull_response = requests.post(
        f"{config.host}/api/pull",
        json={"model": config.model, "stream": False},
        timeout=None,
    )
    pull_response.raise_for_status()
    status = pull_response.json().get("status", "")
    if status != "success":
        raise RuntimeError(f"failed to pull model {config.model!r}: {status}")
    logger.info("pulled model %s", config.model)


def _call_ollama(config: OllamaConfig, prompt: str) -> str:
    response = requests.post(
        f"{config.host}/api/generate",
        json={
            "model": config.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": config.temperature},
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["response"]


def classify_resolution(config: OllamaConfig, resolution: Resolution) -> Classification:
    """Classify one resolution, retrying once on malformed JSON before falling
    back to a neutral, low-confidence classification (local models are less
    reliable at strict structured output than hosted ones)."""
    prompt = _build_user_prompt(resolution)

    for attempt in range(2):
        try:
            raw_text = _call_ollama(config, prompt)
            parsed = _extract_json(raw_text)
            direction = Direction(parsed["direction"])
            confidence = float(parsed.get("confidence", 0.5))
            reasoning = str(parsed.get("reasoning", ""))
            return Classification(
                resolution_symbol=resolution.symbol,
                direction=direction,
                confidence=confidence,
                reasoning=reasoning,
            )
        except (ValueError, KeyError, requests.RequestException) as exc:
            logger.warning(
                "classification attempt %d failed for %s: %s",
                attempt + 1,
                resolution.symbol,
                exc,
            )
            prompt = (
                f"{prompt}\nYour previous response was not valid JSON in the "
                "required shape. Respond again with ONLY the JSON object."
            )

    logger.warning(
        "falling back to neutral classification for %s after repeated failures",
        resolution.symbol,
    )
    return Classification(
        resolution_symbol=resolution.symbol,
        direction=Direction.NEUTRAL,
        confidence=0.0,
        reasoning="fallback: model did not return valid structured output",
    )

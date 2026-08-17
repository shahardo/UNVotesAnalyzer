"""CLI entry point: fetch / classify / score / all."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from src.analysis.llm_classifier import classify_resolution
from src.analysis.scorer import score_countries
from src.config import Config, load_config
from src.fetch.undl_client import fetch_israel_resolutions, load_cached_resolutions
from src.models import Classification, Direction, Resolution, vote_tally

logger = logging.getLogger("run")


def setup_logging(log_dir: Path = Path("logs")) -> None:
    """Logs to both console and logs/pipeline.log (append mode) so every
    fetch/classify/score step leaves a persistent audit trail, not just
    whatever scrolled past in the terminal."""
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console_handler)
    root.addHandler(file_handler)


def cmd_fetch(config: Config, args: argparse.Namespace) -> None:
    resolutions = fetch_israel_resolutions(config.undl, config.paths.raw_dir, limit=args.limit)
    for r in resolutions:
        tally = vote_tally(r.votes)
        logger.info(
            "FETCH resolution=%s title=%r date=%s subjects=%s votes(Y/N/A)=%d/%d/%d",
            r.symbol, r.title, r.date, "; ".join(r.subjects),
            tally["Y"], tally["N"], tally["A"],
        )
    logger.info("fetched %d resolutions into %s", len(resolutions), config.paths.raw_dir)


def _classifications_path(config: Config):
    return config.paths.processed_dir / "classifications.jsonl"


def _load_classifications(config: Config) -> dict[str, Classification]:
    path = _classifications_path(config)
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        result[data["resolution_symbol"]] = Classification(
            resolution_symbol=data["resolution_symbol"],
            direction=Direction(data["direction"]),
            confidence=data["confidence"],
            reasoning=data["reasoning"],
        )
    return result


def _append_classification(config: Config, classification: Classification) -> None:
    path = _classifications_path(config)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "resolution_symbol": classification.resolution_symbol,
                    "direction": classification.direction.value,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning,
                }
            )
            + "\n"
        )


def _log_classification(resolution, classification: Classification, *, cached: bool) -> None:
    tally = vote_tally(resolution.votes)
    logger.info(
        "CLASSIFY%s resolution=%s title=%r date=%s | votes(Y/N/A)=%d/%d/%d | "
        "sentiment=%s confidence=%.2f | reasoning=%s",
        " (cached)" if cached else "",
        resolution.symbol, resolution.title, resolution.date,
        tally["Y"], tally["N"], tally["A"],
        classification.direction.value, classification.confidence, classification.reasoning,
    )


def _results_path(config: Config):
    return config.paths.processed_dir / "results.json"


def _write_results_json(
    config: Config, resolutions: list[Resolution], classifications: dict[str, Classification]
) -> None:
    """Writes one consolidated JSON file listing every examined resolution
    with its title, vote tally, and LLM classification -- a human-readable
    join of data/raw/*.json and classifications.jsonl."""
    rows = []
    for resolution in resolutions:
        classification = classifications.get(resolution.symbol)
        if classification is None:
            continue
        tally = vote_tally(resolution.votes)
        rows.append(
            {
                "symbol": resolution.symbol,
                "title": resolution.title,
                "date": resolution.date,
                "subjects": resolution.subjects,
                "votes": tally,
                "direction": classification.direction.value,
                "confidence": classification.confidence,
                "reasoning": classification.reasoning,
            }
        )
    rows.sort(key=lambda r: (r["date"] or "", r["symbol"]))
    _results_path(config).write_text(json.dumps(rows, indent=2), encoding="utf-8")


def cmd_classify(config: Config, args: argparse.Namespace) -> None:
    resolutions = load_cached_resolutions(config.paths.raw_dir)
    existing = _load_classifications(config)
    to_classify = [r for r in resolutions if r.symbol not in existing]
    logger.info("%d resolutions cached, %d already classified, %d to classify",
                len(resolutions), len(existing), len(to_classify))

    for resolution in resolutions:
        if resolution.symbol in existing:
            _log_classification(resolution, existing[resolution.symbol], cached=True)

    for resolution in to_classify:
        classification = classify_resolution(config.ollama, resolution)
        _append_classification(config, classification)
        existing[resolution.symbol] = classification
        _log_classification(resolution, classification, cached=False)

    _write_results_json(config, resolutions, existing)
    logger.info("wrote %d examined resolutions to %s", len(existing), _results_path(config))


def cmd_clean(config: Config, args: argparse.Namespace) -> None:
    removed = 0
    for path in config.paths.raw_dir.glob("*.json"):
        path.unlink()
        removed += 1
    for path in (_classifications_path(config), _results_path(config)):
        if path.exists():
            path.unlink()
            removed += 1
    logger.info(
        "CLEAN removed %d cached JSON file(s) from %s and %s",
        removed, config.paths.raw_dir, config.paths.processed_dir,
    )


def cmd_score(config: Config, args: argparse.Namespace) -> None:
    resolutions = load_cached_resolutions(config.paths.raw_dir)
    classifications = _load_classifications(config)
    scores = score_countries(resolutions, classifications)

    out_path = config.paths.output_dir / "country_scores.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["country", "raw_score", "resolutions_counted", "normalized_score"])
        for s in scores:
            writer.writerow([s.country, s.raw_score, s.resolutions_counted, s.normalized_score])

    for s in scores:
        logger.info(
            "SCORE country=%s raw_score=%d resolutions_counted=%d normalized_score=%.2f",
            s.country, s.raw_score, s.resolutions_counted, s.normalized_score,
        )
    logger.info("wrote %d country scores to %s", len(scores), out_path)


def cmd_all(config: Config, args: argparse.Namespace) -> None:
    cmd_fetch(config, args)
    cmd_classify(config, args)
    cmd_score(config, args)


def main() -> None:
    parser = argparse.ArgumentParser(description="UN Votes -> Israel Sympathy Analyzer")
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="fetch Israel-related resolutions from UNDL")
    fetch_parser.add_argument("--subject", default=None, help="unused, kept for CLI ergonomics")
    fetch_parser.add_argument("--limit", type=int, default=None)
    fetch_parser.set_defaults(func=cmd_fetch)

    classify_parser = subparsers.add_parser("classify", help="classify cached resolutions via Ollama")
    classify_parser.set_defaults(func=cmd_classify)

    score_parser = subparsers.add_parser("score", help="aggregate per-country scores")
    score_parser.set_defaults(func=cmd_score)

    all_parser = subparsers.add_parser("all", help="run fetch, classify, score in sequence")
    all_parser.add_argument("--limit", type=int, default=None)
    all_parser.set_defaults(func=cmd_all)

    clean_parser = subparsers.add_parser(
        "clean", help="delete all cached JSON in data/raw and data/processed"
    )
    clean_parser.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    setup_logging()
    config = load_config(args.config)
    args.func(config, args)


if __name__ == "__main__":
    main()

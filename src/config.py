"""Loads and validates config.yaml."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml


@dataclasses.dataclass
class UndlConfig:
    base_url: str
    collection: str
    subject_keywords: list[str]
    date_from: str | None
    date_to: str | None
    page_size: int
    request_delay_seconds: float


@dataclasses.dataclass
class OllamaConfig:
    host: str
    model: str
    temperature: float
    timeout_seconds: int


@dataclasses.dataclass
class PathsConfig:
    raw_dir: Path
    processed_dir: Path
    output_dir: Path


@dataclasses.dataclass
class Config:
    undl: UndlConfig
    ollama: OllamaConfig
    paths: PathsConfig


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    undl = UndlConfig(**raw["undl"])
    ollama = OllamaConfig(**raw["ollama"])
    paths_raw = raw["paths"]
    paths = PathsConfig(
        raw_dir=Path(paths_raw["raw_dir"]),
        processed_dir=Path(paths_raw["processed_dir"]),
        output_dir=Path(paths_raw["output_dir"]),
    )

    for p in (paths.raw_dir, paths.processed_dir, paths.output_dir):
        p.mkdir(parents=True, exist_ok=True)

    return Config(undl=undl, ollama=ollama, paths=paths)

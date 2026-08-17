# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Fetches UN General Assembly/Security Council resolutions concerning Israel from
the UN Digital Library, uses a local LLM (via Ollama) to classify each
resolution's stance toward Israel, then scores every voting country 0-100
based on how they voted relative to that stance.

Scoring model: each resolution gets a `direction` (sympathetic=+1,
neutral=0, unsympathetic=-1) from the LLM; each country's vote gets a `sign`
(Y=+1, N=-1, Abstain/non-voting=0). `country_score_on_resolution =
direction × sign`, summed across all analyzed resolutions and normalized as
`(raw_score + N) / (2 × N) × 100` (N = resolutions analyzed, 50 = neutral).
This means voting yes on a pro-Israel resolution and no on an anti-Israel
resolution both score +1 — the vote's meaning flips with what the resolution
actually says. Normalization uses the fixed theoretical range `[-N, N]`
rather than min-max across countries, so 50 always means "neutral /
non-participating" regardless of which countries are most extreme.

## Commands

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Requires [Ollama](https://ollama.com) running locally with the model from
`config.yaml` pulled (default `llama3.1:8b`): `ollama pull llama3.1:8b`.

```
python run.py fetch --limit 20     # scrape UNDL, cache parsed resolutions to data/raw/
python run.py classify             # classify newly-cached resolutions via Ollama
python run.py score                # aggregate country scores to data/output/country_scores.csv
python run.py all --limit 20       # run all three stages in sequence
python run.py clean                # delete all cached JSON in data/raw/, data/processed/, and data/output/
```

`fetch` and `classify` are resumable/idempotent (each skips
already-fetched/already-classified resolutions). Run `clean` to force a
full re-fetch/re-classify — needed after changing `subject_keywords` or the
Ollama model in `config.yaml`, since neither stage will otherwise revisit
already-cached work.

Tests (pure logic — scoring math and the LLM-classifier's JSON
parsing/retry/fallback/model-pull logic; no network or running Ollama
instance required):

```
pytest tests/
python -m pytest tests/test_scorer.py -q          # single file
python -m pytest tests/test_llm_classifier.py::test_classify_retries_once_then_succeeds  # single test
```

## Architecture

Three-stage pipeline (`run.py` is the only entry point; each `cmd_*`
function is one stage), sharing state on disk rather than in memory so any
stage can be re-run independently:

1. **fetch** (`src/fetch/undl_client.py`) — Playwright-driven scrape of
   `digitallibrary.un.org`. Writes one JSON file per resolution to
   `data/raw/<symbol>.json`.
2. **classify** (`src/analysis/llm_classifier.py`) — reads cached
   resolutions from `data/raw/`, sends each to Ollama, appends results to
   `data/processed/classifications.jsonl` (append-only — this is what makes
   `classify` skip already-done resolutions), and rewrites
   `data/processed/results.json` (a human-readable join of raw + classified
   data) on every run.
3. **score** (`src/analysis/scorer.py`) — pure function, no I/O
   (`score_countries(resolutions, classifications) -> list[CountryScore]`),
   called from `run.py` which writes `data/output/country_scores.csv`.

Shared dataclasses/enums live in `src/models.py` (`Resolution`,
`CountryVote`, `Vote`, `Classification`, `Direction`) and config loading in
`src/config.py` (`Config` / `UndlConfig` / `OllamaConfig` / `PathsConfig`,
loaded from `config.yaml`).

### UNDL fetching quirks (`src/fetch/undl_client.py`)

`digitallibrary.un.org` sits behind an AWS WAF that fingerprints and blocks
plain headless browsers, not just non-browser HTTP clients. The client
launches Playwright Chromium with `--disable-blink-features=
AutomationControlled`, a realistic user agent/viewport, and a hidden
`navigator.webdriver`, which gets a solvable JS challenge instead of an
outright 403; the browser context's cookies are then reused for the actual
paginated queries via `context.request`. The JSON output format
(`of=recjson`) returns near-empty records for this collection, so the
fetcher requests MARCXML (`of=xm`) instead: resolution symbol from `791$a`,
title from `245`, topic from `991$c`, vote date from `269$a`, and the
per-country vote breakdown from repeated `967` fields (`$e`=country,
`$d`=`Y`/`N`, and a *missing* `$d` meaning abstain). All of this was
verified against live records, not assumed — if you touch this file and
something stops matching, re-verify against a live record rather than
assuming the documented API behavior.

A record can carry more than one `791` field: the resolution's own symbol
lives in `$a`, but a record that supersedes/relates to another resolution
also carries a second `791` with only `$z` (a cross-reference, no `$a`).
`_parse_record` only lets an `$a`-bearing `791` set the symbol, so a later
`$z`-only field can't clobber one already found — get this wrong and
~20% of records silently fail to parse (logged as "skipping record with no
resolution symbol") even though they have a perfectly good symbol.

### LLM classification (`src/analysis/llm_classifier.py`)

Calls Ollama's `/api/generate` with a system prompt demanding a JSON object
(`{"direction": ..., "confidence": ..., "reasoning": ...}`) and parses the
first `{...}` block out of the response (local models don't reliably emit
*only* JSON). On malformed JSON, retries once with an appended
"respond again with only JSON" instruction; if that also fails, falls back
to a `neutral`, `confidence=0.0` classification rather than raising —
`data/processed/results.json` reasoning strings of
`"fallback: model did not return valid structured output"` are the
signature of this path (check `logs/pipeline.log` for the underlying
`WARNING` to see why, e.g. a 404 means the configured model was never
pulled). `ensure_model_available()` runs once before a `classify` batch and
auto-pulls `config.ollama.model` via `/api/pull` if it's missing locally,
since a missing model makes every `/api/generate` call 404 and would
otherwise silently degrade into all-neutral fallbacks.

## Data & logs

| Path | Contents |
| --- | --- |
| `data/raw/<symbol>.json` | One cached, parsed resolution per file: title, date, subjects, full per-country vote list. |
| `data/processed/classifications.jsonl` | One line per classified resolution: direction, confidence, reasoning (append-only, used to skip already-classified resolutions). |
| `data/processed/results.json` | Rewritten on every `classify` run: every examined resolution's title, date, subjects, Y/N/A vote tally, and LLM classification in one readable JSON array, sorted by date. |
| `data/output/country_scores.csv` | Final per-country `raw_score` / `resolutions_counted` / `normalized_score`, written by `score`. |
| `logs/pipeline.log` | Every FETCH/CLASSIFY/SCORE step, mirrored to the console — the full audit trail (resolution, sentiment, vote tally, per-country result) even for a run with thousands of resolutions. |

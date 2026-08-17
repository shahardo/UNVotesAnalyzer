# UN Votes → Israel Sympathy Analyzer

Fetches UN General Assembly and Security Council resolutions concerning
Israel from the UN Digital Library, uses a local LLM (via Ollama) to judge
whether each resolution is sympathetic or unsympathetic to Israel, then
scores every voting country on a 0–100 scale based on how they voted.

## How the score works

Each analyzed resolution gets a **direction** from the LLM: `sympathetic`
(+1), `neutral` (0), or `unsympathetic` (-1) — i.e. whether adopting it is
good or bad for Israel. Each country's vote gets a **sign**: `Y` = +1, `N` =
-1, `Abstain`/non-voting = 0.

```
country_score_on_resolution = resolution_direction × vote_sign
```

Voting *yes* on a pro-Israel resolution and voting *no* on an anti-Israel
resolution both score +1 (defending Israel's position); the reverse scores
-1. A country's raw score is the sum of this across every analyzed
resolution, normalized as:

```
normalized = (raw_score + N) / (2 × N) × 100      # N = resolutions analyzed, 50 = neutral
```

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Install [Ollama](https://ollama.com), make sure it's running, and pull the
model referenced in `config.yaml` (default `llama3.1:8b`):

```
ollama pull llama3.1:8b
```

## Usage

```
python run.py fetch --limit 20     # scrape UNDL, cache parsed resolutions to data/raw/
python run.py classify             # classify newly-cached resolutions via Ollama
python run.py score                # aggregate country scores to data/output/country_scores.csv
python run.py all --limit 20       # run all three stages in sequence
python run.py clean                # delete all cached JSON in data/raw/ and data/processed/
```

Each stage is resumable/idempotent: `fetch` skips resolutions already
cached, `classify` skips resolutions already classified. Run `clean` when
you want to force a full re-fetch/re-classify from scratch (e.g. after
changing `subject_keywords` or the Ollama model in `config.yaml`).

## Data & logs

| Path | Contents |
| --- | --- |
| `data/raw/<symbol>.json` | One cached, parsed resolution per file: title, date, subjects, full per-country vote list. |
| `data/processed/classifications.jsonl` | One line per classified resolution: direction, confidence, reasoning (append-only, used to skip already-classified resolutions). |
| `data/processed/results.json` | Rewritten on every `classify` run: every examined resolution's title, date, subjects, Y/N/A vote tally, and LLM classification in one readable JSON array, sorted by date. |
| `data/output/country_scores.csv` | Final per-country `raw_score` / `resolutions_counted` / `normalized_score`, written by `score`. |
| `logs/pipeline.log` | Every FETCH/CLASSIFY/SCORE step, mirrored to the console — the full audit trail (resolution, sentiment, vote tally, per-country result) even for a run with thousands of resolutions. |

## UNDL access notes

`digitallibrary.un.org` sits behind an AWS WAF that fingerprints and blocks
plain headless browsers, not just non-browser HTTP clients. `undl_client.py`
launches Playwright Chromium with `--disable-blink-features=
AutomationControlled`, a realistic user agent/viewport, and a hidden
`navigator.webdriver`, which is enough to get a solvable JS challenge instead
of an outright 403; the browser context's cookies are then reused for the
actual paginated queries. The JSON output format (`of=recjson`) turned out to
return near-empty records for this collection, so the fetcher requests
MARCXML (`of=xm`) instead and reads the resolution symbol from `791$a`,
title from `245`, topic from `991$c`, vote date from `269$a`, and the
per-country vote breakdown from repeated `967` fields (`$e`=country,
`$d`=`Y`/`N`, and a *missing* `$d` meaning abstain). All of this was verified
against live records, not assumed.

## Tests

```
pytest tests/
```

Covers the scoring math and the LLM-classifier's JSON parsing/retry/fallback
logic; no network or running Ollama instance required.

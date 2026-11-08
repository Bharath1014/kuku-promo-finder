# Kuku promo finder

Reads a microdrama screenplay pack (timestamped `.txt` episodes) and finds 20–60 s promo clips. Each promo opens on a hook, ends on a cliffhanger, and makes sense to a stranger. The output is a ranked list of clips with exact episode and timestamps that an editor can cut.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env          # then paste your free Gemini key into .env
python run.py --input "path/to/show folder"   # folder of episode-NNN-*.txt screenplays
python run.py --input "path/to/show folder" --model heuristic   # no API key needed
```

Requires Python 3.10+. The input pack is not included: it is Kuku's confidential content.

### Web app

```bash
streamlit run app.py
```

In the app you can:
- upload a show's episode `.txt` files, or one `.zip` of the show folder;
- choose an AI model or rules-only mode;
- browse the ranked promos, each with its clip script;
- download the CSV, Markdown or JSON results.

**API key: any provider.** The sidebar has a single **API key** field. Paste a key from OpenAI, Anthropic, Google Gemini, Groq, OpenRouter, or any other OpenAI-compatible API:
- **Provider:** detected from the key. If the key's prefix doesn't identify it, the app asks each provider's free "list models" endpoint which one accepts it.
- **Model:** filled in automatically, and you can change it, or override the provider and base URL, under "Provider, model and base URL".
- **Privacy:** a visitor's key is kept only in their browser session. It is never written to disk or shared with other visitors.

**Owner key (optional).** To let visitors run AI mode without a key of their own, add your key in the app's Streamlit secrets:

```toml
API_KEY = "your key from any provider"
# optional:
# MODEL = "gpt-5-mini"
# PROVIDER = "openai"        # openai | anthropic | gemini | groq | openrouter | compat
# API_BASE_URL = "https://api.example.com/v1"   # only for other OpenAI-compatible APIs
```

Uploaded files are deleted after each run. In AI mode, the script text is sent to the key's provider.

### Command-line outputs

Outputs go to `outputs/<model>/`:

| File | What it is |
|---|---|
| `promos.md` | Ranked "post these first" list, plus every promo |
| `promos.csv` | One row per promo, with an empty `editor_verdict` column for human review |
| `quality_report.md` | Automatic checks: rule violations, possibly invented quotes, model-vs-rules disagreements, rejected proposals |
| `promos.json` | Everything, machine-readable |
| `raw_model_outputs/` | Unedited model responses |

## Results (shared separately)

The outputs quote the screenplays, which are Kuku's confidential content, so they are **not in this repo**. They are shared through the link in the submission form, as a zip with these folders:

| Folder | What it is |
|---|---|
| `outputs/v2_final/` | **Main result.** Final prompt, all 50 episodes. Episodes 1–45 by gemini-3.6-flash; 46–50 and the ranking by gemini-flash-latest after the daily quota ran out |
| `outputs/v1_gemini-3.6-flash/` | First prompt, kept to show what the v2 prompt and rule changes fixed |
| `outputs/baseline_rules_only/` | No AI: keyword rules only |
| `outputs/test_eps1-5_gemini-3.8-flash/` | Model-swap test on episodes 1–5 |

The free Gemini tier allows about 20 requests per model per day, and a full show needs 11. A comma-separated list falls back to the next model when one runs out:

```bash
python run.py --input ... --model "gemini:gemini-3.6-flash,gemini:gemini-flash-latest,gemini:gemini-3.5-flash-lite"
```

## Swap the model

Choose the model with one flag. The rest of the pipeline does not change.

```bash
python run.py --input ... --model gemini:gemini-3.6-flash        # default, free tier
python run.py --input ... --model groq:llama-3.3-70b-versatile   # free tier
python run.py --input ... --model anthropic:claude-opus-5-5
python run.py --input ... --model ollama:qwen2.5:14b             # local
python run.py --input ... --model compat:<name>                  # any OpenAI-compatible API (LLM_BASE_URL, LLM_API_KEY)
python run.py --input ... --model heuristic                      # no AI, rules only (baseline)
```

Other flags:

| Flag | Default | Meaning |
|---|---|---|
| `--episodes 1-10` | all | Run on a subset of episodes |
| `--per-episode` | 3 | Maximum promos per episode |
| `--chunk` | 5 | Episodes per model call |
| `--top` | 15 | How many promos to rank for posting |
| `--rpm` | 8 | Rate limit, in requests per minute |

Model responses are cached in `.cache/llm`, so a re-run costs nothing.

## How it works

1. **Parse (code).** Every camera shot, action, line and sound cue becomes a block with an ID (`E03#41`) and a start/end time.
2. **Propose (model).** The model reads 5 episodes at a time, plus the opening of the next episode. It picks promos by naming block IDs, and gives the hook, the cliffhanger, a one-line reason and 1–10 scores.
3. **Enforce and validate (code).**
   - Block IDs become timestamps, so the model never writes a timestamp.
   - Every cut snaps to a shot boundary and never splits a line of dialogue.
   - If the hook lands after the first 3 s, the start is trimmed forward to it.
   - If the clip runs past the cliffhanger line, the end is cut back to it.
   - Clips outside 20–60 s are rejected.
   - Hook and cliffhanger quotes are checked against the script, to catch invented lines.
4. **Dedupe and score (code).** Overlapping picks are merged. The score is 0.4 × hook + 0.35 × cliffhanger + 0.25 × standalone, minus penalties for flags.
5. **Rank (model, one call).** An "editor" pass picks the posting order. It weighs stopping power, variety, the funnel (new viewers start at episode 1) and spoiler risk.
6. **Cross-check (code).** A separate rule-based hook and cliffhanger score is computed for every promo. Large disagreements with the model are listed for human review.

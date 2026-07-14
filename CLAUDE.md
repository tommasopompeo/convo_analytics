# Audio Intelligence — project context

> Private, local, single-user tool. Upload a recording of one of *your own*
> conversations → transcribe it (Deepgram) → see deterministic metrics → reflect
> on it with Claude → accumulate a portrait of yourself across all conversations.
> Identity: **"a quiet study at dusk"** — calm, private, everything stays on this
> computer. The corpus is **Italian** (with occasional English).

This file is the durable orientation for anyone (human or agent) picking the project
up. The design rationale and decisions live below; for the original v1 build contract
(Deepgram config, data model, §10 schema) see `audio-intel-prototype-v1-spec.md`.

---

## Current status (2026-07-14)

- **v1 prototype**: complete to the spec's manual stop line (upload → transcribe →
  categorize → speaker-tag → metrics → manual Claude paste loop → owner profile).
- **Aggregate synthesis (Stage 1)**: **built** — a genuine cross-corpus portrait on
  `/profile`, produced by a manual paste loop that mirrors the per-conversation one.
  This replaced the old last-write-wins archetype. No API key required.
- **Per-conversation view + metrics**: **trimmed** to "few but solid" (see decisions).
- **Stage 2 (API automation + multi-user)**: **in progress** — automating API analysis via Gemini and adding support for multi-user capabilities.

The app is run locally on `http://127.0.0.1:8000/` (loopback only, never exposed).

---

## Stack & layout

- **Backend**: FastAPI + SQLite (`data/app.db`), server-rendered Jinja2 templates,
  vanilla JS (`static/js/app.js`). Python 3.12 in `.venv`.
- **Transcription**: Deepgram `nova-3`. Config in `app/config.py`
  (`LANGUAGE` defaults to `multi`; for this all-Italian corpus set
  `DEEPGRAM_LANGUAGE=it` in `.env` — a single tight language model beats hedging
  across 10 languages. `KEYTERMS` holds the corpus's proper nouns/jargon).
- **Design contract**: `static/css/tokens.css` (the `:root` token block — single
  source of truth) + `static/css/app.css` (component recipes). Fraunces serif +
  warm paper + one ink-teal accent. **Never change existing token values**; add new
  keys only.

### File map (`app/`)
| File | Role |
|------|------|
| `main.py` | FastAPI app + router mounts + `init_db()` on startup |
| `db.py` | SQLite schema (idempotent) + migrations + connection helper |
| `config.py` | paths, upload limits, `LONG_PAUSE_SEC`, Deepgram `LANGUAGE`/`KEYTERMS`, key loader |
| `deepgram_client.py` / `background.py` | transcription call + off-request-path job |
| `metrics.py` | deterministic per-speaker + conversation metrics (pure Python, no LLM) |
| `fillers.py` | strips non-lexical hesitation sounds; keeps meaning-bearing discourse markers |
| `prompt_builder.py` | `build_prompt` (§10 per-conversation) + `build_aggregate_prompt` (cross-corpus) |
| `models.py` | `AnalysisOutput` (§10) + `AggregateOutput` (aggregate) Pydantic contracts |
| `profile_merge.py` | per-conversation owner-profile accumulation (dedupe-union; still used) |
| `aggregate_merge.py` | corpus bundle builder + aggregate store/load (the Stage-1 engine) |
| `web.py` | Jinja templates instance, `db_dep`, small query helpers |
| `routes/uploads.py` | home/library, upload, categorize, transcribing, delete, status |
| `routes/transcripts.py` | speaker tagging + metrics/transcript view |
| `routes/analysis.py` | per-conversation paste loop, result view, `/profile`, `/profile/refresh` |

---

## The flow (screens → routes)

1. **Upload** `/upload` → 2. **Categorize** `/files/{id}/categorize` → 3. **Transcribing**
`/files/{id}/transcribing` → 4. **Speaker tag** `/transcripts/{id}/speakers` →
5. **Metrics + transcript** `/transcripts/{id}` → 6. **Analyze** `/transcripts/{id}/analyze`
(copy prompt → paste JSON back) → 7. **Result** `/analyses/{analysis_id}`.

Cross-cutting: **`/profile`** (the aggregate centerpiece) and **`/profile/refresh`**
(bundle the whole corpus → paste synthesis back). An already-analysed recording is
reachable directly from its saved reflection: the home list shows a **"What surfaced"**
link + **"Reflected"** badge, and the metrics page shows **"View reflection"**.

---

## Data model (`data/app.db`)

`audio_files`, `transcripts`, `utterances`, `speakers` (one `is_owner=1`),
`analyses` (per-conversation §10 JSON), `owner_profile` (append-only per-conversation
accumulation), and **`aggregate_insight`** (append-only cross-corpus syntheses —
`insight_json`, `archetype`, `synthesis_type` manual|incremental|full,
`conversation_count`, `source_analysis_ids`, `created_at`).

Only the **owner's** utterances ever feed the profile; other participants are never
profiled.

---

## The aggregate synthesis (the product's main value)

**Problem it fixed:** `profile_merge.py` was deterministic bookkeeping — dedupe-union
of lists + `current_archetype` = the *most recent* conversation's signal
(last-write-wins). That is not synthesis; the big "who you are" portrait was just the
latest read.

**Stage 1 (built, manual, no API):** `/profile/refresh` calls
`aggregate_merge.build_corpus_bundle()` → `prompt_builder.build_aggregate_prompt()`,
which bundles every analysed conversation's interpretive layer (summary, key_topics,
sentiment, owner_insights) + the three surviving metrics + authoritative corpus
counts, and asks Claude to synthesise. The user pastes it into Claude, pastes back an
`AggregateOutput` JSON; it's validated and stored in `aggregate_insight`, and rendered
as the `/profile` centerpiece.

**`AggregateOutput` shape** (`models.py`): `portrait` (holistic, replaces
last-write-wins) · `through_lines` · `shows_up_differently` (how the same person
presents by setting — the differentiator only the corpus reveals) · `recurring_themes`
(weighted by conversation_count, not a flat union) · `tensions` (say-vs-do) ·
`drift` (how the read evolved) · `archetype` · `confidence` · `corpus_meta`.

**Stage 2 (target, gated on API key):** same prompt + same schema, called by the
platform. Two call sites — per-conversation at `/analyze`, and aggregate re-synthesis
after each upload. **Cadence = hybrid**: cheap *incremental* update every upload
(new conversation + current aggregate), periodic *full* re-synthesis on demand and
every **N=5** recordings. Invariant: manual and API paths share the identical
prompt+schema, so automating changes only the caller.

---

## Key decisions (and why)

- **Aggregate is the centerpiece; per-conversation is the supporting act.** Value was
  pushed to the cross-corpus view.
- **Per-conversation view trimmed** from 7 sections to 4: kept *summary*,
  *You, in this conversation* (promoted to the star — it's the richest, most personal
  output and feeds the profile), *what you talked about* (salience labels removed —
  they clustered at "high" and weren't calibrated), *what went unsaid*, *worth
  carrying forward*. **Cut**: the sentiment *arc* (sentiment was shown twice; the arc
  is hard to render serenely) and *pivot points* (duplicated the behaviors, noisy refs).
- **Metrics cut to three defensible ideas** — *balance* (talk-time share, one serene
  bar, two-sided only), *pace* (wpm), *shape* (length + speaking time). **Cut** as
  dead or misleading on real Italian output:
  - **filler rate** — structurally 0 (smart-formatted Italian never emits ehm/eh tokens);
  - **question count** — `?`-detection missed *every* question in the interview;
  - **speaker switches** — meaningless raw count, redundant with turns;
  - **longest monologue** — 27-min artifact on single-sided recordings;
  - **long-pause count / gaps** — on single-sided it labels the *other person's speech*
    as silence (actively misleading). Single-sided now shows an honest note instead.
- **Dataviz tokens are additive.** New `--viz-*` keys in `tokens.css` (mostly aliases;
  only two new warm-neutral values). The accent still marks exactly one series per
  view (you), honouring the ≤2-accent cap. Recipes: `.share`, `.corpus-stats`,
  `.register`, `.freq`, `.tension`, `.timeline`, `.showed-up`, `.record-open`.
- **"Mojibake" was a non-bug** — the DB stores clean UTF-8; the `�` seen once was a
  Windows-terminal rendering artifact.

---

## Run & test

```bash
# run (loopback only — never network-exposed)
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
# test
./.venv/Scripts/python.exe -m pytest -q      # 29 tests: metrics, fillers, profile merge, §10 schema, Deepgram parse
```

Windows note: kill a stale server with PowerShell
`Get-NetTCPConnection -LocalPort 8000 -State Listen | %{ Stop-Process -Id $_.OwningProcess -Force }`
(`pkill` from Git Bash does not reliably reach Windows Python processes).

---

## Fresh clone on a new machine — what's NOT in the repo

A `git clone` gives you the **code only**. Three things are git-ignored and must be
recreated locally (this is intentional — the repo is public and carries no secrets or
personal data):

- **`.env`** — the Deepgram and Gemini API keys. Copy `.env.example` → `.env` and fill in
  `DEEPGRAM_API_KEY` and `GEMINI_API_KEY` (optionally `DEEPGRAM_LANGUAGE=it`). Without them, transcription
  and AI analysis fail. The keys are never logged/printed.
- **`data/`** — the SQLite DB (`app.db`) + audio blobs: the user's real recordings,
  transcripts, analyses, and accumulated profile. A fresh **empty** `app.db` is created
  automatically on first run (`init_db()`), so the app works but starts with **zero
  recordings and no profile / aggregate**. To carry the real data across machines, copy
  the `data/` folder by hand (USB / secure transfer) — **never** through the repo.
- **`.venv/`** — rebuild from `requirements.txt` (Python 3.12).

So a fresh clone runs and looks correct (fonts, styling, all screens, tests pass) but
is empty of data and has no key until you add `.env`. If the recordings list / profile
look empty on a new machine, this is why — nothing is broken.

## Guardrails

- **Never** log/print the Deepgram key; read it from `.env` at runtime only. `.env` is
  git-ignored.
- Loopback-only; the tool must never be network-exposed.
- Preserve the offline/private identity in all copy — the only sanctioned outbound is
  the (still-manual) Claude paste loop, and later the sanctioned Claude API call, both
  framed honestly.
- Do **not** build the Stage-2 API automation until a key exists and it's requested.
- Do not change existing token *values*; add new keys.
- The user's real conversation data lives in `data/app.db`. Do not ingest generated
  sample analyses into it without explicit say-so; test against a copy.

# Audio Intelligence — Prototype v1

A private, local tool: upload a conversation recording → transcribe it (Deepgram) →
see deterministic metrics → hand a copy-ready prompt to Claude by hand → paste the JSON
reply back → accumulate an owner profile. **The platform never calls an LLM itself**
(that's the v1 stop line — analysis happens by copy-paste into Claude chat).

## Requirements

- Windows, Python 3.12 available as `py -3.12` (the venv intentionally uses 3.12, not 3.14).
- A Deepgram API key in `.env` at the project root: `DEEPGRAM_API_KEY=<value>`.
  (`.env` is git-ignored and the key is never logged or printed.)

## Setup

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env   # then fill in DEEPGRAM_API_KEY
```

> **Cloning on another machine?** The repo carries the code only. `.env` (your key),
> `data/` (your recordings + SQLite DB), and `.venv/` are git-ignored and are **not**
> included. A fresh empty `data/app.db` is created on first run, so the app starts with
> no recordings or profile — that's expected, nothing is broken. To move your actual
> data across, copy the `data/` folder by hand; never push it. See `CLAUDE.md` →
> "Fresh clone on a new machine" for the full list.

## Run

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Then open http://127.0.0.1:8000/ . Bound to loopback only — it is not network-exposed.

## The flow (7 screens)

1. **Upload** an `.mp3` / `.m4a` / `.wav`.
2. **Categorize** — conversation type, your role, optional objective/context.
3. **Transcribing** — a spinner polls until Deepgram finishes.
4. **Speaker tag** — mark which separated voice is you (the only tag that matters).
5. **Metrics + transcript** — hard numbers computed in Python, plus the full transcript.
6. **Analyze** — copy the generated prompt into a Claude chat, paste Claude's JSON back.
7. **Result** — the rendered analysis; your **/profile** accumulates across conversations.

## Tests

```bash
./.venv/Scripts/python.exe -m pytest -q
```

Unit tests cover the deterministic metrics, filler handling, the profile merge, the §10
schema validation, and Deepgram response parsing (via a mock fixture — no API spend).

## Configuration

`app/config.py` — upload limits, the long-pause threshold, and `KEYTERMS` (empty in v1;
add domain vocabulary there to enable Deepgram keyterm prompting later).

## Notable design points

- Deepgram config is fixed (spec §3): `nova-3`, `language=multi` (IT+EN code-switching),
  diarization, utterances, filler words; native sentiment/topics/intents are OFF.
- Metrics are always computed in Python, never by an LLM.
- Only **your** utterances ever feed the owner profile; other participants are never profiled.
- Filler handling splits hesitation sounds (stripped from the Claude transcript and counted)
  from meaning-bearing discourse markers like *cioè*/*insomma*/*tipo* (always kept, never counted).

## Not built in v1 (deferred by spec §13)

No Claude API automation, no voiceprints/cross-file speaker identity, no non-owner profiling,
no streaming. These come after validating transcript quality and prompt output.

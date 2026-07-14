# Audio Intelligence

A private, local, multi-user tool: upload a conversation recording → transcribe it (Deepgram) → see deterministic metrics → automatically analyze it via Gemini 2.5 Flash → accumulate an owner profile and refresh overall insights via Gemini 2.5 Pro.

Includes local user authentication (passlib/JWT) to support multiple users securely.

## Requirements

- Python 3.11/3.12.
- A Deepgram API key in `.env` at the project root: `DEEPGRAM_API_KEY=<value>`.
- A Gemini API key in `.env` at the project root: `GEMINI_API_KEY=<value>`.
  (`.env` is git-ignored and the keys are never logged or printed.)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then fill in DEEPGRAM_API_KEY and GEMINI_API_KEY
```

> **Cloning on another machine?** The repo carries the code only. `.env` (your keys),
> `data/` (your recordings + SQLite DB), and `.venv/` are git-ignored and are **not**
> included. A fresh empty `data/app.db` is created on first run, so the app starts with
> no recordings or profile — that's expected, nothing is broken. To move your actual
> data across, copy the `data/` folder by hand; never push it. See `AGENT_RULES.md` →
> "Fresh clone on a new machine" for the full list.

## Run

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Then open http://127.0.0.1:8000/ . Bound to loopback only — it is not network-exposed.

## The flow

1. **Register / Login** — secure access using local user authentication.
2. **Upload** an `.mp3` / `.m4a` / `.wav`.
3. **Categorize** — conversation type, your role, optional objective/context.
4. **Transcribing** — a spinner polls until Deepgram finishes.
5. **Speaker tag** — mark which separated voice is you (the only tag that matters).
6. **Metrics + transcript** — hard numbers computed in Python, plus the full transcript.
7. **Analyze** — automatically generate structured conversation analysis using Gemini 2.5 Flash.
8. **Result** — the rendered analysis; your **/profile** accumulates across conversations. Refresh overall insights at any time using Gemini 2.5 Pro.

## Tests

```bash
./.venv/Scripts/python.exe -m pytest -q
```

Unit and integration tests cover the deterministic metrics, filler handling, profile merge, Pydantic schema validation, Deepgram response parsing, and Gemini client functionality.

## Configuration

`app/config.py` — upload limits, the long-pause threshold, and `KEYTERMS` (add domain vocabulary there to enable Deepgram keyterm prompting).

## Notable design points

- Deepgram config is fixed (spec §3): `nova-3`, `language=multi` (IT+EN code-switching),
  diarization, utterances, filler words; native sentiment/topics/intents are OFF.
- Metrics are always computed in Python, never by an LLM.
- Only **your** utterances ever feed the owner profile; other participants are never profiled.
- Filler handling splits hesitation sounds (stripped from the LLM transcript and counted)
  from meaning-bearing discourse markers like *cioè*/*insomma*/*tipo* (always kept, never counted).

## Deferred Features (Future Roadmap)

No voiceprints/cross-file speaker identity, no non-owner profiling, no live streaming. These can be considered after validating transcript quality and prompt output.

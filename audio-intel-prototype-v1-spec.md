# Audio Intelligence Tool — Prototype v1 Build Spec

> Handoff doc for Claude Code (plan mode). This defines the **stop line**: build to here, validate output, then design the automated analysis layer separately.

---

## 1. Scope & stop line

**In scope for v1:**
Upload → save → transcribe (Deepgram) → categorize (buttons + context) → manual owner tagging → compute deterministic metrics → emit a copy-ready Claude prompt → ingest pasted-back JSON → update owner profile.

**Explicit stop line:** the platform does **not** call any LLM. All interpretive analysis happens by copy-pasting into Claude chat and pasting the JSON result back. Validate transcript quality and prompt output before automating.

**Deferred (do NOT build now):**
- Claude API integration (no key yet)
- Voiceprint / cross-file speaker embeddings (`pyannote`, SpeechBrain)
- Profiling of non-owner participants
- Any real-time / streaming capture

---

## 2. Stack

- **Backend:** Python (FastAPI or Flask — FastAPI preferred for async Deepgram calls)
- **Storage:** SQLite + local filesystem for audio blobs
- **Frontend:** minimal but intentionally designed — vanilla JS, server-rendered templates. Few screens, but the design is not an afterthought: it must transmit the vibe defined in §11. The visual foundation is the project's **base design system** (the design tokens in `static/css/tokens.css` and component recipes in `static/css/app.css`) — use it as the design contract, steered by the §11 vibe brief. See §11.
- **Deepgram SDK:** official Python SDK (`deepgram-sdk`)

---

## 3. Deepgram configuration (final)

Since sentiment/topics/pivots move to the Claude layer, **disable Deepgram's native text-intelligence add-ons.** Keep only what feeds transcript quality + deterministic metrics.

```python
options = {
    "model": "nova-3",
    "language": "multi",      # CONFIRMED: audio is primarily Italian with occasional English.
                              #   Nova-3 handles IT+EN code-switching natively. Do NOT lock to "it" —
                              #   that would degrade the English fragments.
    "diarize": True,          # within-file speaker separation (handles 1 or 2+ speakers)
    "smart_format": True,     # punctuation, numbers, dates
    "punctuate": True,
    "paragraphs": True,
    "utterances": True,       # CRITICAL — per-utterance {speaker, start, end, text}; basis for all metrics
    "filler_words": True,     # optional; useful for hesitation/dominance signal. Strip from the Claude-facing transcript.
}
```

**Multilingual (confirmed):** Nova-3 Multilingual supports Italian + English code-switching natively as the default production model — no extra config beyond `language=multi`. The response tags each word with its detected language (BCP-47), which could later power a "% English used" metric (deferred, see §13).

**Note on native add-ons:** `sentiment`/`topics`/`intents` are billed Deepgram features and are disabled here since analysis is done in Claude. Irrelevant to correctness; relevant to cost only if ever reconsidered.

**Persist the raw Deepgram JSON response** so metrics can be recomputed without re-transcribing.

### 3a. Secrets & config (API key handling)

- The Deepgram API key lives in a local env file at:
  `C:\Claude Projects\conversation_analytics\.env`
- The file contains: `DEEPGRAM_API_KEY=<value>`. Load it with `python-dotenv` (`load_dotenv()` finds `.env` at the project root automatically) and read `os.environ["DEEPGRAM_API_KEY"]`.
- **Never hardcode the key value** in source, config, or committed files. Read it from the environment at runtime only.
- **Never log or print the key** (no debug prints, no error messages that echo it).
- Add `.env` to `.gitignore` **before** the first commit so the key file is never tracked.

---

## 4. Data model (SQLite)

Simplified by the owner-only-profile decision.

```sql
audio_files (
  id, path, filename, uploaded_at, duration_sec,
  conversation_type,        -- from button selection
  owner_role,               -- interviewer / participant / facilitator ...
  objective,                -- optional free text: "what I wanted from this"
  context_note              -- optional free text: additional specifics
)

transcripts (
  id, audio_file_id, raw_deepgram_json, plain_text, created_at
)

utterances (
  id, transcript_id, speaker_label, start_sec, end_sec, text
)

speakers (
  id, transcript_id, speaker_label,
  is_owner BOOL,            -- the ONE tag that matters
  local_name               -- optional freeform, scoped to this file only, NOT a persisted person
)

analyses (
  id, transcript_id, metrics_json, llm_output_json, created_at
)

owner_profile (
  id, profile_json, archetype, archetype_notes, updated_at
  -- single accumulating row (or append-only versioned rows for history)
)
```

Recommend **append-only** `owner_profile` rows (each analysis writes a new version) so you can inspect drift over time rather than overwriting.

---

## 5. Upload + categorization flow

1. User uploads `.mp3` / `.m4a`. Save to disk, insert `audio_files` row.
2. Present categorization form:
   - **Conversation type** (buttons, single-select): Interview · 1-on-1 (work) · Brainstorming · Friends · Family · Meeting (3+) · Other
   - **Your role** (buttons): Interviewer · Interviewee · Facilitator · Participant · N/A
   - **Objective** (optional text): "What were you trying to get out of this conversation?" — materially improves gap / unmet-intent analysis.
   - **Additional context** (optional text): freeform specifics.
3. Kick off Deepgram transcription (async). Store transcript + utterances.

---

## 6. Speaker tagging flow

After transcription, show each detected speaker label with 2–3 sample utterances. User marks exactly one as **"me" (owner)**. Others get an optional local name (this file only). Store in `speakers`.

---

## 7. Deterministic metrics module (Python, no LLM)

Compute from `utterances`. Present as **hard numbers** — distinct from LLM interpretation.

Per speaker:
- **Talk-time %** — sum of `(end - start)` / total
- **Word-count %** — words / total words
- **WPM** — words / talk-time-minutes
- **Turns**, **avg turn length**, **longest monologue** (sec)
- **Question count** — `?`-terminated utterances (crude, label as approximate)
- **Filler-word rate** — if `filler_words` enabled

Conversation-level:
- **Total duration**, **turn count**, **speaker-switch count**
- **Inter-utterance gaps** — deltas between consecutive `end`→`start`; flag long pauses

These are (a) displayed in the UI and (b) injected into the Claude prompt as ground-truth context so Claude doesn't hallucinate numbers.

---

## 8. Claude bridge (manual paste loop)

### 8a. Prompt emission
Platform generates a copy-ready prompt containing:
- Conversation metadata (type, owner role, objective, context)
- Which speaker label is the owner
- The deterministic metrics (so Claude cites real numbers)
- The cleaned transcript (speaker-labeled, filler words optionally stripped)
- **Current owner profile JSON** (so Claude proposes a diff, not a rewrite)
- **Strict instruction to return ONLY the JSON schema in §10, no prose, no markdown fences**

Add a "Copy prompt" button.

### 8b. JSON ingestion
- Textarea to paste Claude's JSON response.
- Validate against the §10 schema (fail loudly on malformed JSON).
- Store in `analyses.llm_output_json`.
- Apply the `owner_profile_update` diff → write new `owner_profile` version.
- Render analysis in UI.

This keeps you in full control: you see exactly what Claude returned before it's ingested.

---

## 9. Owner profile / archetype logic

- Profile accumulates **only from owner utterances** across files.
- Each analysis returns a **diff proposal** (`owner_profile_update`), not a full rewrite. Platform merges: append to recurring topics, accumulate style notes, update goals/concerns.
- **Archetype** = a derived summary layer, regenerated as evidence grows. Recommend **emergent** (let Claude name the archetype from accumulated evidence) rather than forcing a fixed taxonomy up front. Optionally seed with a light framework later if you want comparability across users.
- Store `archetype_notes` (the evidence trail) alongside the label so it's auditable, not a black box.

---

## 10. Analysis output schema (the JSON contract)

The single most important artifact — makes the paste-back loop deterministic. Instruct Claude to return exactly this:

```json
{
  "summary": "2-4 sentence neutral summary",
  "key_topics": [
    { "topic": "", "salience": "high|medium|low", "driven_by_speaker": "owner|speaker_1|..." }
  ],
  "sentiment_arc": [
    { "segment": "opening|mid|close|<timestamp>", "speaker": "", "sentiment": "positive|neutral|negative|mixed", "note": "" }
  ],
  "overall_sentiment": { "owner": "", "conversation": "" },
  "pivot_points": [
    { "utterance_ref": "<approx timestamp or quote>", "from_topic": "", "to_topic": "", "initiated_by": "", "significance": "" }
  ],
  "conversation_gaps": [
    { "type": "unexplored_topic|unasked_question|unmet_intent", "description": "" }
  ],
  "follow_ups": {
    "questions": [""],
    "actions": [""]
  },
  "owner_insights": {
    "communication_style": "",
    "notable_behaviors": [""]
  },
  "owner_profile_update": {
    "recurring_topics_add": [""],
    "communication_style_notes": [""],
    "goals_concerns_add": [""],
    "archetype_signal": ""
  }
}
```

`owner_insights` and `owner_profile_update` are the only owner-specific blocks; everything else covers all speakers.

---

## 11. Front-end design

**Approach:** The design foundation is the project's **base design system** — the design tokens in `static/css/tokens.css` and the component recipes in `static/css/app.css`. Build on it rather than inventing a parallel visual system. In practice:

1. Keep `tokens.css` loaded first (it holds the `:root { … }` token block), reference everything downstream via `var(--name)` (never raw hex outside the token block), and reuse the base component recipes (`.btn*`, `.field`, `.card`, `.badge*`, `.nav`, links) for markup and states.
2. This is **transaction / functional gear, NOT showcase/marketing gear.** It's a working tool (upload, forms, metrics, transcript) — not a landing page. No hero sections, no oversized display type, no marketing imagery.
3. **Light theme only** (predominantly white per the vibe brief).
4. Apply the vibe brief below as a **steering overlay** on top of the tokens: favor generous whitespace, soft contrast, and calm/unhurried spacing over dense or high-drama treatments.
5. Design work (including installed design skills) should evolve this base design system rather than generate a competing one.

**Vibe brief — the feeling to transmit:**
The user uploads recordings of personal and professional conversations, so the interface must read as a **quiet, trusted, private space** — not an analytics dashboard.
- **Calm, private, safe.** The tool should reassure the user that their data is secure and handled with care.
- **Predominantly white**, light and airy; generous whitespace; soft (not harsh) contrast.
- **Minimal, clean, functional.** No decorative noise. One clear action per screen. Every element earns its place.
- **Comfortable and unhurried.** Soft edges, gentle transitions, readable typography, room to breathe. Nothing clinical or aggressive.
- **Quiet confidence.** Considered, reliable, reassuring — the polish itself signals that the user's data is safe here.

**Screens (minimal — few steps, each intentionally designed):**
1. **Upload** → file picker
2. **Categorize** → button form + optional text fields
3. **Transcribing** → status/spinner
4. **Speaker tag** → label owner
5. **Metrics + transcript** → deterministic numbers, full transcript
6. **Analyze** → "Copy prompt" button + paste-back textarea
7. **Result** → rendered analysis + updated profile view

---

## 12. Build order (checklist)

- [ ] Project skeleton (FastAPI + SQLite + static frontend)
- [ ] DB schema (§4)
- [ ] Upload endpoint + file storage
- [ ] Categorization form → persist metadata
- [ ] Deepgram integration (§3) → store transcript + utterances + raw JSON
- [ ] Speaker-tagging UI → set `is_owner`
- [ ] Deterministic metrics module (§7) + display
- [ ] Prompt-emission builder (§8a) + copy button
- [ ] JSON paste-back + schema validation + storage (§8b)
- [ ] Owner-profile merge logic (§9) + profile view
- [ ] **STOP.** Validate transcript quality + prompt output in Claude chat.

---

## 13. Open decisions for later (not blocking v1)

- Get a Claude API key → collapse the paste loop into one automated call
- Add speaker embeddings for cross-file identity (needs local compute; T14 iGPU runs it slowly)
- Whether to adopt a fixed archetype framework vs. keep emergent
- Sentiment cross-check: re-enable Deepgram native sentiment as a cheap sanity check against Claude's
- "% English used" metric — derive from Deepgram's per-word BCP-47 language tags (data is already there; just not surfaced in v1)

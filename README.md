# Provenance Guard

A small Flask service that analyzes a piece of text-based content (a poem, a story excerpt, a blog post) and returns an AI-vs-human attribution, a calibrated confidence score, and a plain-language transparency label — plus an appeals workflow, rate limiting, and a structured audit log.

Full design rationale, the five spec questions, the architecture diagram, and the AI tool plan live in [`planning.md`](./planning.md). This README is the evidence record: exact label text, real test outputs, rate-limit proof, and audit log samples.

## Contents

- [Setup](#setup)
- [API](#api)
- [Detection signals](#detection-signals)
- [Confidence scoring](#confidence-scoring)
- [Transparency label](#transparency-label)
- [Appeals workflow](#appeals-workflow)
- [Rate limiting](#rate-limiting)
- [Audit log](#audit-log)
- [Known limitations](#known-limitations)
- [Spec reflection](#spec-reflection)
- [AI usage](#ai-usage)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
# .venv\Scripts\activate          # Windows (Command Prompt)
# source .venv/Scripts/activate   # Windows (Git Bash)

pip install -r requirements.txt
cp .env.example .env              # then add your GROQ_API_KEY
python app.py                     # serves on http://localhost:5000
```

To exercise the signals and scoring logic without running the server (used to produce the evidence in this README):

```bash
python test_signals.py
```

If `GROQ_API_KEY` is missing or the Groq API call fails for any reason, `classify_with_llm()` degrades gracefully to a neutral `0.5` score (logged with an `error` field) instead of crashing the request — the stylometric signal alone still carries the submission, and the result simply skews toward `uncertain`.

## API

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/submit` | POST | `{"text": str, "creator_id": str}` | `content_id`, `attribution`, `confidence`, `label`, both signal scores, `status` |
| `/appeal` | POST | `{"content_id": str, "creator_reasoning": str}` | `content_id`, `status: "under_review"`, `appeal_reasoning`, `appeal_timestamp` |
| `/log` | GET | — (optional `?limit=N`) | `{"entries": [...]}` — most recent audit log rows, newest first |
| `/health` | GET | — | `{"status": "ok"}` |

## Detection signals

Two independent signals, combined with fixed weights (full rationale in `planning.md`):

| Signal | What it captures | Implementation |
|---|---|---|
| **LLM-based classification** | Holistic semantic/stylistic plausibility — does this read like something a model would generate? | `signals.classify_with_llm()` — prompts Groq `llama-3.3-70b-versatile` for a structured `{"ai_probability": float}` JSON response |
| **Stylometric heuristics** | Structural/statistical uniformity — sentence-length variance, vocabulary diversity (type-token ratio), long-word density | `signals.stylometric_score()` — pure Python, no external libraries |

`combined_score = 0.6 * llm_score + 0.4 * stylometric_score`. The LLM signal is weighted higher because it's generally more discriminative on its own; the stylometric signal still carries real weight so one bad/odd LLM call can't unilaterally decide a verdict.

**Why these are genuinely distinct, not two versions of the same idea:** the LLM signal reads and reasons about the text's meaning and plausibility; the stylometric signal never "understands" anything — it only counts and measures. In testing below, they frequently disagree (e.g. the "lightly edited AI" sample: `llm_score = 0.2`, leaning human, while the structural signal alone would have flagged it higher), which is exactly the kind of independent information that makes combining them worthwhile.

## Confidence scoring

`confidence = 0.5 + abs(combined_score - 0.5)`, giving a range of `[0.5, 1.0]` where `0.5` means "pure coin flip, don't trust this" and `1.0` means "maximally sure, in whichever direction." Attribution buckets use **asymmetric thresholds** — `combined_score >= 0.78` → `likely_ai`, `combined_score <= 0.30` → `likely_human`, otherwise `uncertain` — deliberately making `likely_ai` harder to reach than `likely_human`, because a false accusation of AI authorship is more damaging to a creator than a missed detection (see `planning.md` for the full reasoning, including how these exact numbers were calibrated).

**How I tested whether the scores are meaningful:** I ran the pipeline on the four sample inputs from the assignment (clearly AI, clearly human, borderline formal human, borderline lightly-edited AI) plus a deliberately maximal "textbook AI" paragraph, and checked that the scores moved in the expected direction and that all three label categories were actually reachable — not just theoretically defined. Two representative results, pulled directly from `python test_signals.py` output:

**High-confidence example** — clearly human (ramen review):
```
llm_score: 0.1
stylometric_score: 0.145
combined_score: 0.118
confidence: 0.882  (88%)
attribution: likely_human
```

**Lower-confidence example** — clearly AI-generated (canonical sample, short text):
```
llm_score: 0.8
stylometric_score: 0.467
combined_score: 0.667
confidence: 0.667  (67%)
attribution: uncertain
```

These two cases show real variation (88% vs. 67%, and a different attribution bucket entirely) rather than a constant score. The second case is also an honest example of the system *not* over-claiming: even though the LLM signal alone was fairly confident (`0.8`), the stylometric signal disagreed enough (`0.467` — pulled down by high vocabulary diversity in a short excerpt) that the combined score stayed below the `likely_ai` bar, and the system reported `uncertain` instead of accusing the text of being AI-written. A third data point, a maximally formulaic AI paragraph built to test the upper end of the scale, produced `llm_score: 0.9`, `combined_score: 0.793`, `confidence: 79%`, `attribution: likely_ai` — confirming the high-confidence-AI label is genuinely reachable, not just defined on paper.

## Transparency label

The label returned by `/submit` is generated by `labels.get_label(attribution, confidence)`. `{pct}` is `round(confidence * 100)`. **Exact text of all three variants:**

| Variant | Exact text |
|---|---|
| **High-confidence AI** | `⚠️ Likely AI-Generated — Our analysis indicates this content was very likely produced by an AI system (confidence: {pct}%). This assessment is based on multiple independent signals. If you believe this is incorrect, you can appeal this classification.` |
| **High-confidence human** | `✅ Likely Human-Written — Our analysis indicates this content was very likely written by a human (confidence: {pct}%). Multiple independent signals support this assessment.` |
| **Uncertain** | `❓ Uncertain — Our system could not confidently determine whether this content is AI-generated or human-written (confidence: {pct}%). Treat this result as inconclusive rather than a verdict. You can appeal if you believe this assessment is unfair.` |

Each variant states the confidence percentage in plain language instead of exposing raw signal internals. The AI-generated label is the only one that explicitly opens with an invitation to appeal, since it's the highest-stakes accusation a creator could receive. The uncertain label explicitly tells the reader not to treat the result as a verdict — that sentence is the one doing the most work, since an "uncertain" badge that still *reads* confident would defeat the purpose.

Real responses showing all three reachable (from live testing against the running server / `test_signals.py`):

- `⚠️ Likely AI-Generated — ... (confidence: 79%). ...` — formulaic AI test paragraph
- `✅ Likely Human-Written — ... (confidence: 88%). ...` — casual ramen review
- `❓ Uncertain — ... (confidence: 67%). ...` — canonical "clearly AI" sample (short text defeats the stylometric signal — see Known Limitations)

## Appeals workflow

`POST /appeal` looks up the original row by `content_id`, sets `status` to `under_review`, and writes `appeal_reasoning` + `appeal_timestamp` onto that **same row** (not a separate table) so the appeal always lives alongside the original decision.

Tested with the scenario from the assignment (a non-native English speaker whose formal academic writing was flagged as `uncertain`):

```
POST /appeal
{
  "content_id": "80d18c2f-2f7c-4aba-b92b-9e3913f86b2d",
  "creator_reasoning": "I wrote this myself from personal experience studying economics. I am a non-native English speaker and my writing style may appear more formal than typical."
}
```

Response:
```json
{
  "appeal_reasoning": "I wrote this myself from personal experience studying economics. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "appeal_timestamp": "2026-06-30T22:08:37.314214+00:00",
  "content_id": "80d18c2f-2f7c-4aba-b92b-9e3913f86b2d",
  "message": "Appeal received and logged. This content is now under review.",
  "status": "under_review"
}
```

`GET /log` confirms the same row now shows both the original decision and the appeal together (excerpted):
```json
{
  "content_id": "80d18c2f-2f7c-4aba-b92b-9e3913f86b2d",
  "attribution": "uncertain",
  "confidence": 0.7004,
  "llm_score": 0.8,
  "stylometric_score": 0.5509,
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself from personal experience studying economics. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "appeal_timestamp": "2026-06-30T22:08:37.314214+00:00"
}
```

We deliberately do **not** auto-reclassify on appeal — an appeal routes to a human reviewer (a future `GET /log` filtered to `status == "under_review"` would serve as the review queue); automatically re-running the same pipeline on the same text would almost always reproduce the same score.

## Rate limiting

`POST /submit` is limited to **10 requests per minute and 100 per day per client IP**, via Flask-Limiter with in-memory storage:

```python
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit(): ...
```

**Reasoning:** A real creator drafting and checking their own work might submit a handful of pieces in a session — 10/minute comfortably covers that with headroom, while still making a scripted flood of requests (e.g. an attacker hammering the endpoint, or a buggy client retry loop) hit a wall almost immediately rather than burning through the Groq API quota or DB writes. The 100/day ceiling is generous for a single prolific human user across a whole day, but meaningfully caps sustained abuse from any one IP.

**Evidence — 12 rapid requests against the running server (limit is 10/minute):**
```
1  200
2  200
3  200
4  200
5  200
6  200
7  200
8  200
9  200
10 200
11 429
12 429
```
Exactly the first 10 succeeded; requests 11 and 12 were rejected with `429 Too Many Requests`, confirming the limit triggers correctly.

## Audit log

Every submission and appeal is written to a structured SQLite table (`submissions`, in `provenance_guard.db`) with: `content_id`, `creator_id`, `text`, `timestamp`, `llm_score`, `stylometric_score`, `combined_score`, `confidence`, `attribution`, `label`, `status`, `appeal_reasoning`, `appeal_timestamp`. `GET /log` exposes it as JSON for documentation/grading visibility (no auth — in a real deployment this route would require reviewer-level auth).

**Three real entries from a live run** (`GET /log`, truncated to the relevant fields):

```json
[
  {
    "content_id": "80d18c2f-2f7c-4aba-b92b-9e3913f86b2d",
    "creator_id": "creator-formal-writer",
    "attribution": "uncertain",
    "llm_score": 0.8,
    "stylometric_score": 0.5509,
    "combined_score": 0.7004,
    "confidence": 0.7004,
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself from personal experience studying economics. I am a non-native English speaker and my writing style may appear more formal than typical.",
    "appeal_timestamp": "2026-06-30T22:08:37.314214+00:00"
  },
  {
    "content_id": "88cc008d-faee-425a-bdad-d90fe803e40d",
    "creator_id": "creator-human-test",
    "attribution": "likely_human",
    "llm_score": 0.1,
    "stylometric_score": 0.1455,
    "combined_score": 0.1182,
    "confidence": 0.8818,
    "status": "classified",
    "appeal_reasoning": null,
    "appeal_timestamp": null
  },
  {
    "content_id": "70b5206b-78d0-4106-b311-4734b1f25756",
    "creator_id": "creator-ai-test",
    "attribution": "uncertain",
    "llm_score": 0.8,
    "stylometric_score": 0.6014,
    "combined_score": 0.7206,
    "confidence": 0.7206,
    "status": "classified",
    "appeal_reasoning": null,
    "appeal_timestamp": null
  }
]
```

(A full live run produced 14 entries total — 4 distinct test submissions plus 10 successful requests from the rate-limit burst test above; this is a representative excerpt, not the full set.)

## Known limitations

1. **Short, repetitive creative writing reads as AI-like to the stylometric signal.** A poem or piece that deliberately repeats a refrain, or any short excerpt in general, produces low sentence-length variance and an unreliable type-token ratio — both of which our heuristics interpret as "uniform = AI-like." This isn't hypothetical: in testing, the assignment's own canonical "clearly AI" sample (a short paragraph) only reached a stylometric sub-score of `0.467` because its type-token ratio sub-score was nearly `0` (i.e., looked very human-like by that one metric, since short texts naturally have high vocabulary diversity) — that disagreement is *why* the combined score landed in `uncertain` rather than `likely_ai`. This is a structural property of the heuristic, not a generic "needs more data" problem: type-token ratio is mathematically less informative the shorter the text gets.
2. **Lightly edited AI output can slip through as "likely human."** The "borderline edited AI" sample from the assignment (a casual paragraph about remote work, originally AI-generated but written in a more natural, personal register) scored `llm_score: 0.2` and a `combined_score` of `0.29` — landing as `likely_human` (71% confidence) in this system. Both signals were fooled simultaneously: the LLM signal because the phrasing genuinely reads as more personal/informal, and the stylometric signal because the editing introduced enough sentence-length variation to look human. This is a real false negative produced during testing, not a theoretical risk, and it's the mirror image of the false-positive problem this system is otherwise tuned to avoid — it shows the cost of biasing thresholds toward "default to human": some AI-assisted content will be under-flagged.

## Spec reflection

**Where the spec helped:** Writing out the exact three label strings in `planning.md` *before* touching `labels.py` made the implementation almost mechanical — `get_label()` is a direct lookup with one string-format call per branch, because every design decision (what each label says, when each one fires) had already been made on paper. There was no ambiguity to resolve mid-coding.

**Where the implementation diverged from the spec:** The original `planning.md` draft set the `likely_ai` threshold at `combined_score >= 0.85` (paired with `<= 0.25` for `likely_human`) to maximize the false-positive-avoidance asymmetry. Live testing in Milestone 4 showed this was too strict — even a maximally formulaic, textbook-AI paragraph (explicit transition words on nearly every sentence, heavy repetition, `llm_score = 0.9`) only produced a `combined_score` of `0.79`, meaning the high-confidence-AI label would have been practically unreachable and would have failed the "must be reachable" requirement outright. I lowered the threshold to `0.78` (and the human threshold to `0.30`) so genuine strong evidence can clear the bar while keeping the gap between the two thresholds meaningful. This is documented in `planning.md`'s "Uncertainty Representation" section as a deliberate, evidence-based deviation rather than an oversight.

## AI usage

Two specific instances where I used an AI tool during implementation, what it produced, and what I changed:

1. **M3 — Flask skeleton + LLM signal function.** I gave the AI tool the "Detection Signals" section of `planning.md` and asked for a Flask app skeleton (`/submit` stub) and a `classify_with_llm(text)` function. It produced a function that called the Groq chat completion API and returned `response.choices[0].message.content` as a raw string. I revised it to (a) instruct the model explicitly to return strict JSON and parse it with `json.loads`, (b) strip markdown code fences defensively (Groq occasionally wraps JSON in ` ```json ` blocks despite instructions not to), and (c) wrap the whole thing in a `try/except` that degrades to a neutral `0.5` score on any failure, since the original version would raise an unhandled exception and 500 the whole `/submit` request if the API call or JSON parse ever failed.
2. **M4 — confidence scoring logic.** I gave the AI tool the "Uncertainty Representation" section (with the original `0.85` / `0.25` thresholds) and asked it to implement `combine_scores()`. The generated function matched the weighting and thresholds correctly on paper, but when I tested it against the four sample inputs (per the spec's instruction to "verify that the generated scoring function actually matches the thresholds you defined"), I discovered the thresholds themselves — not the code — were the problem: nothing in my test set could reach `likely_ai`. I overrode the spec's original numbers (recalibrating to `0.78` / `0.30`, documented in the Spec Reflection above) rather than the generated code, since the implementation was a faithful, correct translation of a threshold design that needed revising.


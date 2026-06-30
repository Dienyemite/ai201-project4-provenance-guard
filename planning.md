# Provenance Guard — Planning Document

## Architecture Narrative

A piece of text enters the system through `POST /submit` along with a `creator_id`. The Flask route hands the raw text to two independent detection signals: the **LLM signal** (Groq `llama-3.3-70b-versatile`) asks the model to holistically judge whether the text reads as human- or AI-written and returns a `0–1` probability. The **stylometric signal** (pure Python) computes three structural metrics — sentence-length variance, type-token ratio, and long-word density — and folds them into a second `0–1` probability. Both scores feed into the **scoring module**, which combines them with fixed weights into a single `combined_score`, derives a `confidence` value, and classifies the result into one of three `attribution` buckets (`likely_ai`, `likely_human`, `uncertain`) using **asymmetric thresholds** (it takes more evidence to call something AI than to call it human — see "Uncertainty Representation" below). The **label module** turns `(attribution, confidence)` into the exact text a reader sees. Every field produced along the way — both raw signal scores, the combined score, the confidence, the attribution, and the label — is written to the **audit log** (SQLite) before the JSON response is returned to the caller.

A creator who disagrees with a verdict calls `POST /appeal` with the `content_id` and their reasoning. The appeal handler looks up the original audit log row, sets `status` to `under_review`, stores the `appeal_reasoning` and an `appeal_timestamp` on that same row (so the appeal always lives "alongside" the original decision, not in a separate disconnected table), and returns a confirmation. `GET /log` exposes the full structured history — submissions and appeals together — for grading/documentation visibility.

## Architecture

```
                         SUBMISSION FLOW
                         ───────────────

   client
     │  POST /submit { text, creator_id }
     ▼
┌─────────────────┐
│  Flask route     │  raw text
│  /submit         │───────────────┐
└─────────────────┘                │
                                    ▼
                          ┌───────────────────┐
                          │ Signal 1: Groq LLM │  → llm_score (0–1)
                          │ classify()         │
                          └───────────────────┘
                                    │
                                    │  raw text (independently)
                                    ▼
                          ┌───────────────────────┐
                          │ Signal 2: Stylometric  │ → stylometric_score (0–1)
                          │ heuristics()           │
                          └───────────────────────┘
                                    │
                     llm_score + stylometric_score
                                    ▼
                          ┌───────────────────┐
                          │ Confidence Scoring │ → combined_score, confidence,
                          │ combine_scores()   │   attribution
                          └───────────────────┘
                                    │
                     attribution + confidence
                                    ▼
                          ┌───────────────────┐
                          │ Transparency Label │ → label text (3 variants)
                          │ get_label()         │
                          └───────────────────┘
                                    │
        content_id, all scores, attribution, label, status="classified"
                                    ▼
                          ┌───────────────────┐
                          │   Audit Log (SQLite)│ → structured row written
                          │   log_submission()   │
                          └───────────────────┘
                                    │
                                    ▼
                              JSON response
                          { content_id, attribution,
                            confidence, label, ... }


                            APPEAL FLOW
                            ───────────

   client
     │  POST /appeal { content_id, creator_reasoning }
     ▼
┌─────────────────┐
│  Flask route     │
│  /appeal         │
└─────────────────┘
          │  content_id, creator_reasoning
          ▼
┌─────────────────────┐
│ Status update         │  status: "classified" → "under_review"
│ + audit log update     │  appeal_reasoning, appeal_timestamp written
│ update_appeal()         │  onto the ORIGINAL row
└─────────────────────┘
          │
          ▼
     JSON response
   { content_id, status: "under_review", appeal logged: true }
```

Both flows converge on the same SQLite table, so `GET /log` always shows the full lifecycle of a piece of content — its original classification and, if filed, its appeal — as a single structured record.

## 1. Detection Signals

**Signal 1 — LLM-based classification (Groq, `llama-3.3-70b-versatile`)**
- **What it measures:** Holistic semantic and stylistic coherence — does the text "read" like something a language model would produce (generic phrasing, hedged claims, transition-heavy structure, lack of specific sensory/personal detail) or like something a human wrote (idiosyncratic word choice, concrete specific details, irregular structure)?
- **Output format:** A float in `[0, 1]` (`llm_score`) representing the model's estimated probability that the text is AI-generated. We prompt Groq to return strict JSON (`{"ai_probability": float, "reasoning": str}`) so the output is directly parseable.
- **Blind spot:** It is a black box — we can't verify *why* it assigned a score, it can be confidently wrong on text that mimics AI style (e.g., very formal human academic writing), and it has no notion of calibration guarantees. It also inherits whatever bias the underlying model has about what "sounds like AI."

**Signal 2 — Stylometric heuristics (pure Python)**
- **What it measures:** Three structural/statistical properties that are cheap to compute and don't require understanding meaning at all:
  1. **Sentence-length variance** (coefficient of variation of words-per-sentence) — human writing tends to mix short and long sentences; AI text is often more uniformly paced.
  2. **Type-token ratio (TTR)** (unique words ÷ total words) — a proxy for vocabulary diversity; heavily templated AI prose tends to lean on a smaller, more repetitive set of transition/filler words over longer passages.
  3. **Long-word density** (words with 7+ characters ÷ total words) — a proxy for the more uniformly "formal" register AI tends to default to without being asked.
- **Output format:** Each metric is normalized to a `[0, 1]` "AI-likelihood" sub-score, then averaged into a single `stylometric_score` in `[0, 1]`.
- **Blind spot:** It has no notion of meaning or factual content — a human who writes in a formal, uniform register (e.g., academic or legal writing) will score high on this signal despite being entirely human-written. It is also length-sensitive: very short excerpts produce noisy TTR and variance estimates.

**Why these two are genuinely distinct:** Signal 1 is *semantic* (it reads the content and reasons about plausibility); Signal 2 is *structural* (it never "reads" the text in a comprehension sense, only counts and measures it). They can — and in testing, do — disagree, which is exactly the information we want: when they agree, we should be more confident; when they disagree, that disagreement is itself useful uncertainty information.

**Combining them:** `combined_score = 0.6 * llm_score + 0.4 * stylometric_score`. The LLM signal is weighted higher because it is generally the more discriminative of the two on its own, but the stylometric signal is given real weight (40%) specifically so a single bad LLM call can't unilaterally decide the verdict — this is also what satisfies the "multi-signal" requirement in a meaningful (not token) way.

## 2. Uncertainty Representation

`combined_score` (defined above) is the system's estimate of "probability this text is AI-generated," in `[0, 1]`.

**Confidence** is *not* the same number. Confidence measures how far the combined score is from a coin-flip (0.5), rescaled to `[0.5, 1.0]`:

```
confidence = 0.5 + abs(combined_score - 0.5)
```

A `combined_score` of exactly `0.5` → `confidence = 0.5` (pure coin flip, maximally uncertain). A `combined_score` of `0.0` or `1.0` → `confidence = 1.0` (maximally certain, in either direction). This is deliberate: **confidence describes how sure the system is in *whichever* direction it's leaning, not how "AI" the text is.** A confidence of `0.51` means "barely better than a coin flip — don't trust this" and must produce a meaningfully different (and much more hedged) label than a confidence of `0.95` ("very sure").

**Attribution thresholds** decide which of the three buckets a `combined_score` falls into, and they are **intentionally asymmetric**, reflecting the hint that a false positive (calling a human's work AI-generated) is more damaging than a false negative on a writing platform:

| `combined_score` range | `attribution` | rationale |
|---|---|---|
| `>= 0.78` | `likely_ai` | requires *strong* evidence (confidence ≥ 0.78) before asserting AI authorship |
| `<= 0.30` | `likely_human` | requires comparatively less evidence (confidence ≥ 0.70) to default toward "human" — the safer default |
| `0.30 < combined_score < 0.78` | `uncertain` | the wider middle band is the deliberate safety margin; a score that merely leans AI without clearing the bar lands here rather than triggering a confident AI accusation |

This means the `likely_ai` bucket requires a noticeably higher bar than `likely_human` — by design. The system would rather tell a creator "we're not sure" than wrongly accuse them of using AI.

These exact numbers (`0.78` / `0.30`) were calibrated empirically, not picked arbitrarily: an early draft used `0.85` / `0.25`, but testing showed that even a maximally formulaic, textbook AI-generated paragraph (explicit transition words on every sentence, heavy repetition, `llm_score = 0.9`) only reached a `combined_score` of `0.79` — meaning `likely_ai` was nearly unreachable in practice and the label was failing its own "must be reachable" requirement. We lowered the bar to `0.78` so genuinely strong AI evidence can clear it, while keeping it meaningfully higher than the `likely_human` bar (`0.30`) to preserve the asymmetry. See README "Confidence Scoring" section for the actual before/after numbers from this calibration pass.

## 3. Transparency Label Design

The label is generated from `(attribution, confidence)` by `labels.get_label()`. The exact text of each of the three variants (`{pct}` is `confidence * 100`, rounded to the nearest whole number):

| Variant | Exact text |
|---|---|
| **High-confidence AI** (`attribution == "likely_ai"`) | `"⚠️ Likely AI-Generated — Our analysis indicates this content was very likely produced by an AI system (confidence: {pct}%). This assessment is based on multiple independent signals. If you believe this is incorrect, you can appeal this classification."` |
| **High-confidence human** (`attribution == "likely_human"`) | `"✅ Likely Human-Written — Our analysis indicates this content was very likely written by a human (confidence: {pct}%). Multiple independent signals support this assessment."` |
| **Uncertain** (`attribution == "uncertain"`) | `"❓ Uncertain — Our system could not confidently determine whether this content is AI-generated or human-written (confidence: {pct}%). Treat this result as inconclusive rather than a verdict. You can appeal if you believe this assessment is unfair."` |

Design notes:
- Every label states the confidence percentage in plain language rather than exposing raw signal scores, since `llm_score` / `stylometric_score` are implementation detail, not user-facing concepts.
- The AI-generated label is the only one that explicitly invites an appeal in its first sentence, since it's the highest-stakes accusation a creator could receive.
- The uncertain label explicitly tells the reader not to treat the result as a verdict — this is the most important sentence in the whole label set, because an "uncertain" badge that *looks* confident would defeat the purpose of having uncertainty at all.

## 4. Appeals Workflow

- **Who can submit an appeal:** Any creator, identified by the `content_id` of a previously classified submission. We don't enforce that the appealing party is the original `creator_id` in this version (no auth layer exists yet) — see Known Limitations in the README.
- **What they provide:** `content_id` and `creator_reasoning` (free text — their explanation of why the classification is wrong).
- **What the system does on receipt:** Looks up the row for `content_id`. If found, sets `status = "under_review"`, stores `appeal_reasoning` and `appeal_timestamp` on that same row, and returns a confirmation JSON payload. If the `content_id` doesn't exist, returns a `404`.
- **What a human reviewer would see in the appeal queue:** `GET /log` (or a future `GET /appeals` filtered view) shows every row where `status == "under_review"`, with the full original decision (both signal scores, combined score, confidence, attribution, label) sitting right next to the creator's `appeal_reasoning` — so a reviewer never has to cross-reference two systems to make a decision.

We deliberately do **not** auto-reclassify on appeal. An appeal is a signal for a *human* to look at the case; automatically re-running the same pipeline on the same text would almost always reproduce the same score and give creators false hope that appealing "fixes" anything algorithmically.

## 5. Anticipated Edge Cases

1. **Short, simple, repetitive creative writing (e.g., a children's poem or a piece using deliberate repetition as a literary device).** Stylometric heuristics expect variety; a poem that repeats a refrain on purpose will show low sentence-length variance and low type-token ratio, both of which our heuristics read as "AI-like." This is a known structural blind spot of Signal 2, not a hypothetical — it's the basis for why the LLM signal is weighted higher (0.6 vs 0.4), since the LLM is more likely to recognize "this is a poem using repetition as device" than the bare statistics are.
2. **Formal, technical, or academic human writing (e.g., a literature review, a legal brief, a policy memo).** This kind of writing is naturally uniform, transition-word-heavy, and hedged ("it is important to note that...") — exactly the register our heuristics associate with AI generation, and exactly the register an LLM has also learned to associate with "this looks AI-written" because so much AI output mimics formal registers. Both signals can be fooled by the same surface feature simultaneously, which is the scenario most likely to produce a wrongful `likely_ai` classification. This is precisely why the `likely_ai` threshold is set well above the midpoint (`combined_score >= 0.78`) — a single signal leaning AI isn't enough on its own; both signals need to lean the same direction with real strength, and even then the appeal path exists as a safety net. (In live testing, this exact scenario — the "borderline_formal_human" sample below — produced `llm_score = 0.8` and `combined_score = 0.70`, landing in `uncertain` rather than `likely_ai`, which is the system behaving as designed.)
3. **Non-native English speaker writing in a more formal or careful register than is typical for casual platforms.** This overlaps with edge case #2 but deserves separate mention because it's an equity concern, not just an accuracy one — a writer choosing more careful, "textbook" phrasing because English isn't their first language risks the same false-positive pattern, and the cost of being wrong here falls disproportionately on a group that didn't do anything different from "writing carefully." (This exact scenario is used as the appeal test case in Milestone 5.)
4. **Very short submissions (a tweet-length excerpt or a single sentence).** Both signals lose reliability: sentence-length variance and TTR are close to meaningless with one or two sentences, and the LLM has little context to reason from. The system has no minimum-length gate today — short inputs will produce a real but noisy score, which tends to push toward `uncertain` simply because there isn't enough signal in either direction, which is the "safe" failure mode but still worth naming as a limitation.

## AI Tool Plan

**M3 (submission endpoint + first signal):**
- **Spec sections provided to the AI tool:** "1. Detection Signals" (Signal 1 description + output format) and the `## Architecture` diagram above (submission flow only).
- **What I'll ask it to generate:** (1) a Flask app skeleton with a `POST /submit` route stub that accepts `{text, creator_id}` and returns a hardcoded JSON response; (2) a `classify_with_llm(text) -> float` function that calls Groq with a prompt instructing it to return `{"ai_probability": float, "reasoning": str}` and parses the JSON.
- **How I'll verify:** Call `classify_with_llm()` directly from a Python shell / test script on 2–3 known inputs (one obviously AI, one obviously human) before wiring it into the route at all, and confirm the function signature matches the spec (returns a float in `[0,1]`, not a string or boolean).

**M4 (second signal + confidence scoring):**
- **Spec sections provided:** "1. Detection Signals" (Signal 2 description) + "2. Uncertainty Representation" (thresholds and confidence formula) + the architecture diagram.
- **What I'll ask for:** (1) a `stylometric_score(text) -> float` function implementing the three named metrics (sentence-length CV, TTR, long-word density) and averaging them; (2) a `combine_scores(llm_score, stylometric_score) -> dict` function implementing the exact weighting (`0.6/0.4`) and the exact asymmetric thresholds from the table above.
- **What I'll check:** Run both signals on the four sample inputs from Milestone 4 (clearly AI, clearly human, borderline-formal-human, borderline-lightly-edited-AI) and confirm scores vary meaningfully and the thresholds match what's written in this document — not a "reasonable-looking" threshold the AI tool invented on its own.

**M5 (production layer):**
- **Spec sections provided:** "3. Transparency Label Design" (the exact three strings) + "4. Appeals Workflow" + the architecture diagram (appeal flow).
- **What I'll ask for:** (1) a `get_label(attribution, confidence) -> str` function that reproduces the three exact label strings with the confidence percentage interpolated; (2) the `POST /appeal` route that looks up a row by `content_id`, updates `status`/`appeal_reasoning`/`appeal_timestamp`, and writes to the audit log.
- **How I'll verify:** Call `get_label()` directly with synthetic `(attribution, confidence)` pairs to confirm all three exact strings are reachable and match this document; test the appeal route with `curl` using a real `content_id` from a prior `/submit` call and confirm `GET /log` shows `status: "under_review"` with `appeal_reasoning` populated on the same row as the original decision.

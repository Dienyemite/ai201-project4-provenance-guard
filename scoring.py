"""Confidence scoring: combine the two signals into a single calibrated
confidence score and an attribution bucket.

See planning.md "2. Uncertainty Representation" for the full rationale,
including why the thresholds below are deliberately asymmetric.
"""

# Weights: LLM signal carries more weight because it is generally more
# discriminative on its own, but the stylometric signal still has real
# influence (40%) so a single bad LLM call can't unilaterally decide a
# verdict.
LLM_WEIGHT = 0.6
STYLOMETRIC_WEIGHT = 0.4

# Asymmetric attribution thresholds: it takes much stronger evidence to
# label something "likely_ai" than "likely_human", because a false
# accusation of AI authorship is more damaging to a creator on a writing
# platform than a missed detection.
LIKELY_AI_THRESHOLD = 0.78
LIKELY_HUMAN_THRESHOLD = 0.30


def combine_scores(llm_score: float, stylometric_score: float) -> dict:
    """Combine the two raw signal scores into a confidence score + attribution.

    Returns:
        {
            "combined_score": float [0,1]   # P(AI-generated)
            "confidence": float [0.5,1.0]    # how sure, in whichever direction
            "attribution": "likely_ai" | "likely_human" | "uncertain"
        }
    """
    combined_score = LLM_WEIGHT * llm_score + STYLOMETRIC_WEIGHT * stylometric_score
    combined_score = max(0.0, min(1.0, combined_score))

    confidence = 0.5 + abs(combined_score - 0.5)

    if combined_score >= LIKELY_AI_THRESHOLD:
        attribution = "likely_ai"
    elif combined_score <= LIKELY_HUMAN_THRESHOLD:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    return {
        "combined_score": round(combined_score, 4),
        "confidence": round(confidence, 4),
        "attribution": attribution,
    }

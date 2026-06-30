"""Transparency label generation.

The exact text below is the canonical copy of the three label variants and
must match planning.md / README.md word-for-word. If you change the wording
here, update those two documents too.
"""

LABEL_AI = (
    "\u26a0\ufe0f Likely AI-Generated \u2014 Our analysis indicates this content was very "
    "likely produced by an AI system (confidence: {pct}%). This assessment is "
    "based on multiple independent signals. If you believe this is incorrect, "
    "you can appeal this classification."
)

LABEL_HUMAN = (
    "\u2705 Likely Human-Written \u2014 Our analysis indicates this content was very "
    "likely written by a human (confidence: {pct}%). Multiple independent "
    "signals support this assessment."
)

LABEL_UNCERTAIN = (
    "\u2753 Uncertain \u2014 Our system could not confidently determine whether this "
    "content is AI-generated or human-written (confidence: {pct}%). Treat this "
    "result as inconclusive rather than a verdict. You can appeal if you "
    "believe this assessment is unfair."
)


def get_label(attribution: str, confidence: float) -> str:
    """Map (attribution, confidence) to the exact transparency label text."""
    pct = round(confidence * 100)

    if attribution == "likely_ai":
        return LABEL_AI.format(pct=pct)
    if attribution == "likely_human":
        return LABEL_HUMAN.format(pct=pct)
    return LABEL_UNCERTAIN.format(pct=pct)

"""Standalone test script for the detection signals and scoring logic --
run this directly (no Flask server needed) to sanity-check the pipeline
end-to-end on a fixed set of sample inputs, per the Milestone 4 instructions.

Usage:
    python test_signals.py
"""

import json

import labels
import scoring
import signals

SAMPLES = {
    "clearly_ai": (
        "Artificial intelligence represents a transformative paradigm shift in "
        "modern society. It is important to note that while the benefits of AI "
        "are numerous, it is equally essential to consider the ethical "
        "implications. Furthermore, stakeholders across various sectors must "
        "collaborate to ensure responsible deployment."
    ),
    "clearly_human": (
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in "
        "it and i was thirsty for like three hours after. my friend got the "
        "spicy version and said it was better. probably won't go back unless "
        "someone drags me there"
    ),
    "borderline_formal_human": (
        "The relationship between monetary policy and asset price inflation "
        "has been extensively studied in the literature. Central banks face a "
        "fundamental tension between their mandate for price stability and the "
        "unintended consequences of prolonged low interest rates on equity and "
        "real estate valuations."
    ),
    "borderline_edited_ai": (
        "I've been thinking a lot about remote work lately. There are genuine "
        "tradeoffs \u2014 flexibility and no commute on one side, isolation and "
        "blurred work-life boundaries on the other. Studies show productivity "
        "varies widely by individual and role type."
    ),
}


def run():
    results = {}
    for name, text in SAMPLES.items():
        llm_result = signals.classify_with_llm(text)
        sty_result = signals.stylometric_score(text)
        scored = scoring.combine_scores(llm_result["score"], sty_result["score"])
        label_text = labels.get_label(scored["attribution"], scored["confidence"])

        results[name] = {
            "llm_score": llm_result["score"],
            "llm_error": llm_result["error"],
            "stylometric_score": sty_result["score"],
            "stylometric_breakdown": {
                "sentence_length_cv_score": sty_result["sentence_length_cv_score"],
                "type_token_ratio_score": sty_result["type_token_ratio_score"],
                "long_word_density_score": sty_result["long_word_density_score"],
            },
            "combined_score": scored["combined_score"],
            "confidence": scored["confidence"],
            "attribution": scored["attribution"],
            "label": label_text,
        }

    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    run()

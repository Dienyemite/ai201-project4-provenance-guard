"""Provenance Guard — Flask app.

Endpoints:
    POST /submit  - submit text for attribution analysis
    POST /appeal  - contest a prior classification
    GET  /log     - view the structured audit log (documentation/grading use)
    GET  /health  - trivial liveness check
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit_log
import labels
import scoring
import signals

load_dotenv()

app = Flask(__name__)

# Flask-Limiter >= 3.x requires storage_uri. In-memory storage is fine for a
# single-process local/dev deployment; see README for production notes.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

audit_log.init_db()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be a non-empty string."}), 400
    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "Field 'creator_id' is required and must be a string."}), 400

    llm_result = signals.classify_with_llm(text)
    stylometric_result = signals.stylometric_score(text)

    scored = scoring.combine_scores(
        llm_score=llm_result["score"],
        stylometric_score=stylometric_result["score"],
    )

    label_text = labels.get_label(scored["attribution"], scored["confidence"])

    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "timestamp": timestamp,
        "llm_score": llm_result["score"],
        "stylometric_score": stylometric_result["score"],
        "combined_score": scored["combined_score"],
        "confidence": scored["confidence"],
        "attribution": scored["attribution"],
        "label": label_text,
        "status": "classified",
    }
    audit_log.log_submission(entry)

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": scored["attribution"],
            "confidence": scored["confidence"],
            "label": label_text,
            "llm_score": llm_result["score"],
            "stylometric_score": stylometric_result["score"],
            "status": "classified",
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not isinstance(content_id, str):
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not creator_reasoning or not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required and must be non-empty."}), 400

    updated = audit_log.log_appeal(content_id, creator_reasoning)
    if updated is None:
        return jsonify({"error": f"No content found with content_id '{content_id}'."}), 404

    return jsonify(
        {
            "content_id": updated["content_id"],
            "status": updated["status"],
            "appeal_reasoning": updated["appeal_reasoning"],
            "appeal_timestamp": updated["appeal_timestamp"],
            "message": "Appeal received and logged. This content is now under review.",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": audit_log.get_log(limit=limit)})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

"""Post-interview evaluation: transcript -> structured screening report.

Runs offline (after the call ends), so it uses the same plain OpenAI-compatible
provider fallback as resume parsing.
"""

import json

from resume import _llm_providers

EVALUATION_PROMPT = """You are an HR screening evaluator. You are given the transcript
of a first-round AI-conducted screening interview. Produce ONLY a JSON object:

{
  "candidate_name": "from the transcript",
  "ctc_current": "as stated, e.g. '5.5 LPA', or null",
  "ctc_expected": "as stated, or null",
  "notice_period": "as stated, or null",
  "reason_for_leaving": "one line, or null",
  "relocation": "willing / not willing / conditional / not discussed",
  "communication": {"score": 1-5, "notes": "clarity, fluency, professionalism"},
  "experience_credibility": {"score": 1-5, "notes": "did answers show real depth about their claimed work?"},
  "red_flags": ["specific concerns, empty if none"],
  "highlights": ["specific positives, empty if none"],
  "summary": "3-4 sentence overall assessment",
  "recommendation": "proceed" | "reject" | "borderline",
  "recommendation_reason": "one sentence"
}

Rules:
- Judge ONLY from the transcript. Do not invent details.
- Speech-to-text can garble words and numbers; do not penalize the candidate
  for obvious transcription artifacts.
- An incomplete interview (missing stages, call ended early) should be noted
  in red_flags and usually means "borderline".
- Be specific in notes: quote or reference what the candidate actually said."""


def _transcript_to_text(history: dict) -> str:
    """Flatten a session history dict into readable 'role: text' lines."""
    lines = []
    for item in history.get("items", []):
        if item.get("type") != "message":
            continue
        role = item.get("role", "?")
        if role == "system":
            continue  # the evaluator must judge the candidate, not our instructions
        content = item.get("content", [])
        text = " ".join(c for c in content if isinstance(c, str))
        if text.strip():
            lines.append(f"{role}: {text.strip()}")
    return "\n".join(lines)


def evaluate_transcript(history: dict) -> dict:
    """Score an interview transcript. Raises if every provider fails."""
    transcript_text = _transcript_to_text(history)
    if not transcript_text.strip():
        raise ValueError("empty transcript, nothing to evaluate")

    from openai import OpenAI

    last_error: Exception | None = None
    for provider in _llm_providers():
        try:
            client = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"])
            response = client.chat.completions.create(
                model=provider["model"],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EVALUATION_PROMPT},
                    {"role": "user", "content": transcript_text[:30000]},
                ],
            )
            report = json.loads(response.choices[0].message.content)
            report["_evaluated_by"] = f"{provider['name']}/{provider['model']}"
            return report
        except Exception as e:  # noqa: BLE001 — try the next provider
            last_error = e
    raise RuntimeError(f"All LLM providers failed to evaluate: {last_error}")

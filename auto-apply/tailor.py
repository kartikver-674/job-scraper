"""
Draft a tailored, résumé-grounded application email with Gemini. The system
instruction forbids inventing any fact; the model receives ONLY the résumé text,
the single job row, and answers.yaml. Output is structured JSON so parsing is
reliable. One API call per job.
"""

import json

from google import genai
from google.genai import types

SYSTEM_INSTRUCTION = (
    "You write concise, professional job-application emails for a software engineer. "
    "RULES: Every factual claim MUST be supported by the provided résumé text or the "
    "answers block. Never invent employers, job titles, dates, metrics, or skills the "
    "résumé does not state. If the job asks for something absent from the résumé, omit "
    "it — do not claim it. Keep the body ~120-180 words, address the specific role and "
    "company, and end with the candidate's name only (no invented contact details). "
    "Return ONLY a JSON object with keys: subject (string), body (string), "
    "grounding_notes (string listing which résumé/answers facts each claim rests on)."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "grounding_notes": {"type": "string"},
    },
    "required": ["subject", "body", "grounding_notes"],
}


def build_prompt(resume_text, job, answers):
    """Assemble the user-content prompt from résumé, the job row, and answers."""
    answers_block = "\n".join(f"- {k}: {v}" for k, v in answers.items()) or "(none)"
    return (
        "=== JOB ===\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}  Remote: {job.get('remote?', '')}\n"
        f"Experience required: {job.get('experience_required', '')}\n"
        f"Salary: {job.get('salary', '')}\n"
        f"Skills the posting matched: {job.get('matched_skills', '')}\n"
        f"Source: {job.get('source_site', '')}\n\n"
        "=== RÉSUMÉ (the only source of truth about the candidate) ===\n"
        f"{resume_text}\n\n"
        "=== ANSWERS (extra facts not in the résumé; use only if relevant) ===\n"
        f"{answers_block}\n\n"
        "Write the application email now."
    )


def get_client(api_key):
    """Construct a Gemini client from an AI Studio API key."""
    return genai.Client(api_key=api_key)


def draft_email(client, model, job, resume_text, answers):
    """Return {'subject','body','grounding_notes'} for one job via one Gemini call."""
    prompt = build_prompt(resume_text, job, answers)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0.4,
        ),
    )
    data = json.loads(response.text)
    return {
        "subject": data.get("subject", "").strip(),
        "body": data.get("body", "").strip(),
        "grounding_notes": data.get("grounding_notes", "").strip(),
    }

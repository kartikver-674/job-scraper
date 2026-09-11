"""Ask a model one question: which of these skills matter most?

Everything else is settled. Extraction, the vocabulary scan, its gate,
retrieval, keyword ranking and the scoring formula are frozen and
deterministic. One thing is not reproducible from structure: among
terms with IDENTICAL structural evidence, which are specialist and
which are incidental. Lovish writes sosl, queueable apex, schedulable
apex, visualforce, postman, eclipse and vs code exactly once each, in
his skills list, never repeated and never used in prose. Structure
scores all seven the same. Gemini scores the first three at 5 and
postman and eclipse at 2, because it knows what they are.

So this is the narrowest possible model call: a verified skill list and
one line of context in, a 1-5 number per skill out. It cannot extract,
it cannot generate keywords, and it is given no résumé — only terms
that already passed the gate.

WHAT IT IS NOT ALLOWED TO DO, and what enforces it:

  invent    a returned skill that was not sent is discarded
  delete    a skill it omits keeps its deterministic centrality
  rename    matching is exact after lowercasing; a reworded term is
            treated as invented and discarded
  reorder   the caller keeps its own list; only the numbers come back

The fallback matters as much as the call: anything the model does not
answer for is not lost, it simply keeps the structural score it already
had. A failed or truncated call degrades to today's behaviour.

    python -m bench.skill_rank --demo
    python -m bench.skill_rank qwen3:8b
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = {
    "type": "object",
    "properties": {
        "importance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["skill", "score"],
            },
        },
    },
    "required": ["importance"],
}

PROMPT = """Someone works in {field}.

Score how central each skill below is to doing THAT work, 1 to 5.

  5  the work is largely this
  4  used constantly in this work
  3  genuinely part of the job
  2  a general tool, used by most people in most jobs
  1  barely relevant to this work

Judge the skill itself, not how often it is written down. A specialist
technology of this field scores high even if it is listed once. A tool
every developer uses — an editor, a source control client, a generic
API client — scores low however central the person's work is.

Copy each skill back exactly as written. Score every one. Add nothing.

Skills:
{skills}"""


def ask(model, field, skills, timeout=900, url=None):
    """{skill: 1-5} as the model returned it, plus (seconds, prompt chars)."""
    import urllib.request
    from bench.run import ctx_for, KEEP_ALIVE, OLLAMA

    listed = "\n".join(f"  - {s}" for s in skills)
    prompt = PROMPT.format(field=field or "this field", skills=listed)
    body = json.dumps({
        "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
        "think": False, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0,
                    "num_ctx": ctx_for(prompt, reply_tokens=900)},
    }).encode()
    started = time.time()
    request = urllib.request.Request(url or OLLAMA, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.loads(json.loads(response.read())["response"])
    got = {}
    for row in answer.get("importance") or ():
        name = str(row.get("skill", "")).strip().lower()
        if name:
            got[name] = row.get("score")
    return got, time.time() - started, len(prompt)


def accept(returned, asked, fallback):
    """(scores, rejected) — the model may only answer, never change the list.

    Every skill keeps a score: the model's where it gave a usable one,
    the deterministic centrality where it did not. Nothing it invents
    survives, and nothing it omits disappears.
    """
    asked = {str(s).strip().lower() for s in asked}
    scores, rejected = {}, []
    for name, score in (returned or {}).items():
        # Matched case-insensitively, which is matching and not
        # renaming: "Apex" and "apex" are the term that was sent.
        name = str(name).strip().lower()
        if name not in asked:
            rejected.append((name, score, "not one of the skills sent"))
            continue
        if not isinstance(score, int) or not 1 <= score <= 5:
            rejected.append((name, score, "not an integer 1-5"))
            continue
        scores[name] = score
    missing = []
    for name in sorted(asked):
        if name not in scores:
            scores[name] = fallback.get(name, 3)
            missing.append(name)
    return scores, rejected, missing


def demo():
    asked = ["apex", "postman", "queueable apex"]
    fallback = {"apex": 5, "postman": 3, "queueable apex": 3}

    scores, rejected, missing = accept(
        {"apex": 5, "postman": 2, "queueable apex": 5}, asked, fallback)
    assert scores == {"apex": 5, "postman": 2, "queueable apex": 5}
    assert rejected == [] and missing == []

    # It may not invent. A skill that was not sent is discarded, however
    # plausible it looks.
    scores, rejected, _m = accept({"apex": 4, "kubernetes": 5}, asked, fallback)
    assert "kubernetes" not in scores
    assert rejected[0][0] == "kubernetes" and "not one of" in rejected[0][2]

    # It may not delete. An unanswered skill keeps its structural score,
    # so a truncated or empty answer degrades to today's behaviour
    # rather than losing the skill.
    scores, _r, missing = accept({"apex": 4}, asked, fallback)
    assert scores["postman"] == 3 and scores["queueable apex"] == 3
    assert missing == ["postman", "queueable apex"]
    # The failure that actually happened: a well-formed EMPTY answer, on
    # five of thirteen people, at temperature 0. Every skill falls back
    # and nothing is lost or silently zeroed.
    scores, rejected, missing = accept({}, asked, fallback)
    assert scores == fallback and len(missing) == len(asked)
    assert accept(None, asked, fallback)[0] == fallback

    # Out-of-range or non-integer answers are refused, not clamped.
    scores, rejected, _m = accept({"apex": 9, "postman": "high"},
                                  asked, fallback)
    assert scores["apex"] == 5 and scores["postman"] == 3
    assert len(rejected) == 2 and all("1-5" in r[2] for r in rejected)

    # Matching is exact after lowercasing; a reworded term is invention.
    scores, rejected, _m = accept({"Apex": 4, "apex code": 5}, asked, fallback)
    assert scores["apex"] == 4
    assert [r[0] for r in rejected] == ["apex code"]
    print("skill_rank demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    sys.exit(__doc__)


if __name__ == "__main__":
    main()

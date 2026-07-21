import json
import unittest

import tailor


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse(json.dumps(self._payload))


class FakeClient:
    def __init__(self, payload):
        self.models = FakeModels(payload)


JOB = {
    "title": "Full Stack Developer", "company": "Jinrai Technologies",
    "location": "India", "remote?": "False",
    "matched_skills": "node, react, typescript, mongodb",
    "experience_required": "1-3 Yrs", "salary": "", "source_site": "naukri",
}


class TestTailor(unittest.TestCase):
    def test_prompt_includes_job_resume_and_answers(self):
        prompt = tailor.build_prompt("RESUME: React Native, Node.js",
                                     JOB, {"notice_period": "30 days"})
        self.assertIn("Jinrai Technologies", prompt)
        self.assertIn("Full Stack Developer", prompt)
        self.assertIn("React Native, Node.js", prompt)
        self.assertIn("30 days", prompt)

    def test_draft_email_returns_structured_fields(self):
        client = FakeClient({
            "subject": "Application: Full Stack Developer",
            "body": "Dear Hiring Team, ...",
            "grounding_notes": "Node.js and React from résumé skills section.",
        })
        result = tailor.draft_email(client, "gemini-2.5-flash", JOB,
                                    "RESUME TEXT", {"notice_period": "30 days"})
        self.assertEqual(result["subject"], "Application: Full Stack Developer")
        self.assertIn("Dear", result["body"])
        self.assertIn("grounding_notes", result)
        # It actually asked the model with our model string + JSON mime type.
        self.assertEqual(client.models.last_kwargs["model"], "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()

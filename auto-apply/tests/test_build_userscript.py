import unittest
import build_userscript as bu


class TestBuildBank(unittest.TestCase):
    def test_maps_known_facts_to_answers(self):
        answers = {
            "notice_period": "30 days",
            "expected_ctc": "12 LPA",
            "current_location": "Delhi/NCR",
            "willing_to_relocate": "yes",
            "work_authorization": "India (citizen)",
            "years_experience": "2",
        }
        bank = bu.build_bank(answers)
        pairs = {tuple(e["keywords"]): e["answer"] for e in bank}
        self.assertEqual(pairs[("notice",)], "30 days")
        self.assertEqual(pairs[("expected", "ctc")], "12 LPA")
        self.assertEqual(pairs[("years", "experience")], "2")

    def test_skips_missing_facts(self):
        bank = bu.build_bank({"notice_period": "30 days"})
        keys = [tuple(e["keywords"]) for e in bank]
        self.assertIn(("notice",), keys)
        self.assertNotIn(("expected", "ctc"), keys)  # no expected_ctc -> not emitted

    def test_never_emits_blank_answer(self):
        bank = bu.build_bank({"notice_period": "   "})
        self.assertEqual(bank, [])


class TestRenderUserscript(unittest.TestCase):
    def test_replaces_all_tokens_and_embeds_values(self):
        tpl = ("const B=/*__BANK__*/;const F=/*__FREE_TEXT__*/;const R=/*__RESUME__*/;"
               "const K=/*__GROQ_KEY__*/;const M=/*__GROQ_MODEL__*/;"
               "const E=/*__GROQ_ENDPOINT__*/;const ME=/*__ME__*/;")
        out = bu.render_userscript(
            tpl,
            bank=[{"keywords": ["notice"], "answer": "30 days"}],
            free_text=[{"keywords": ["why"], "template": "at {company}"}],
            resume_text='résumé with "quotes"\nand newline',
            groq_key="gsk_secret",
            groq_model="llama-3.3-70b-versatile",
            groq_endpoint="https://api.groq.com/openai/v1/chat/completions",
            me={"name": "Kartik Verma"},
        )
        self.assertNotIn("/*__", out)                 # every token replaced
        self.assertIn('"30 days"', out)
        self.assertIn("gsk_secret", out)
        self.assertIn("llama-3.3-70b-versatile", out)
        # résumé quotes/newlines are JSON-escaped -> still valid JS string
        self.assertIn('\\"quotes\\"', out)
        self.assertIn("\\n", out)


if __name__ == "__main__":
    unittest.main()

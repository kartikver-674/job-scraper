import os
import tempfile
import unittest

import answers


class TestAnswers(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_parses_key_values(self):
        path = self._write("notice_period: 30 days\nexpected_ctc: 12 LPA\n")
        result = answers.load_answers(path)
        self.assertEqual(result["notice_period"], "30 days")
        self.assertEqual(result["expected_ctc"], "12 LPA")

    def test_ignores_comments_and_blanks(self):
        path = self._write("# a comment\n\nnotice_period: 30 days\n   \n")
        self.assertEqual(answers.load_answers(path), {"notice_period": "30 days"})

    def test_strips_surrounding_quotes(self):
        path = self._write('location: "Delhi/NCR"\nrole: \'SWE\'\n')
        result = answers.load_answers(path)
        self.assertEqual(result["location"], "Delhi/NCR")
        self.assertEqual(result["role"], "SWE")

    def test_value_may_contain_colon(self):
        path = self._write("note: available: immediately\n")
        self.assertEqual(answers.load_answers(path)["note"], "available: immediately")

    def test_missing_file_returns_empty(self):
        self.assertEqual(answers.load_answers("/no/such/file.yaml"), {})


if __name__ == "__main__":
    unittest.main()

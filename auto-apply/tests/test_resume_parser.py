import os
import tempfile
import time
import unittest

import resume_parser


class TestLoadResume(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pdf = os.path.join(self.dir, "resume.pdf")
        self.cache = os.path.join(self.dir, "resume.txt")
        with open(self.pdf, "w", encoding="utf-8") as f:
            f.write("pdf-bytes-placeholder")
        self.calls = []

    def _fake_extractor(self, path):
        self.calls.append(path)
        return "EXTRACTED RESUME TEXT"

    def test_no_cache_extracts_and_writes(self):
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "EXTRACTED RESUME TEXT")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(os.path.exists(self.cache))

    def test_fresh_cache_is_reused_without_extracting(self):
        with open(self.cache, "w", encoding="utf-8") as f:
            f.write("CACHED TEXT")
        # Make cache newer than pdf.
        future = time.time() + 100
        os.utime(self.cache, (future, future))
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "CACHED TEXT")
        self.assertEqual(self.calls, [])

    def test_stale_cache_triggers_reparse(self):
        with open(self.cache, "w", encoding="utf-8") as f:
            f.write("OLD")
        # Make pdf newer than cache.
        future = time.time() + 100
        os.utime(self.pdf, (future, future))
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "EXTRACTED RESUME TEXT")
        self.assertEqual(len(self.calls), 1)

    def test_missing_pdf_raises(self):
        with self.assertRaises(FileNotFoundError):
            resume_parser.load_resume(os.path.join(self.dir, "nope.pdf"),
                                      self.cache, extractor=self._fake_extractor)


if __name__ == "__main__":
    unittest.main()

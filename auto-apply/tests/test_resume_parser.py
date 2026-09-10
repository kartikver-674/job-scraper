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


class TestNormalise(unittest.TestCase):
    """A PDF text layer is not plain text, and every exact match downstream
    assumes it is."""

    def test_ligatures_fold_to_their_letters(self):
        # Chrome renders "airflow" with a single U+FB02 glyph, so the term
        # the model reads back is not the term any job listing contains.
        # bench/ hit this on two of eight résumés before a model was
        # involved at all.
        self.assertEqual(resume_parser.normalise("air\ufb02ow"), "airflow")
        self.assertEqual(resume_parser.normalise("snow\ufb02ake"), "snowflake")
        self.assertEqual(resume_parser.normalise("o\ufb03ce"), "office")
        self.assertEqual(resume_parser.normalise("\ufb00"), "ff")

    def test_accents_are_kept(self):
        # NFKC, not NFKD-plus-strip-combining: that pair is for the profile
        # NAME slug, where "María" must become a filename. Flattening body
        # text corrupts employers and institutions that carry accents.
        self.assertEqual(resume_parser.normalise("María Peña"), "María Peña")
        self.assertEqual(resume_parser.normalise("Université Hassan II"),
                         "Université Hassan II")

    def test_characters_inside_words_are_removed(self):
        # A soft hyphen or zero-width joiner sits INSIDE a word, so the term
        # carrying one never matches the same term without it.
        self.assertEqual(resume_parser.normalise("soft\u00adhyphen"),
                         "softhyphen")
        self.assertEqual(resume_parser.normalise("a\u200bb"), "ab")
        self.assertEqual(resume_parser.normalise("\ufeffleading"), "leading")

    def test_a_non_breaking_space_becomes_a_space(self):
        self.assertEqual(resume_parser.normalise("Node\u00a0js"), "Node js")

    def test_ordinary_text_is_unchanged(self):
        plain = "React Native, Node.js — 5 years (2019-2024)"
        self.assertEqual(resume_parser.normalise(plain), plain)

    def test_extraction_normalises(self):
        # The guard belongs at the boundary, not at each caller.
        got = resume_parser.extract_text.__doc__
        self.assertIn("normalised", got)


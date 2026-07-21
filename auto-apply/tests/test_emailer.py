import os
import tempfile
import unittest

import emailer


class TestEmailer(unittest.TestCase):
    def test_clean_password_strips_spaces(self):
        self.assertEqual(emailer.clean_password("Agtps nsvh ehta pfef"),
                         "Agtpsnsvhehtapfef")

    def test_build_message_sets_headers(self):
        msg = emailer.build_message("me@gmail.com", "hr@co.com", "Subj", "Body text")
        self.assertEqual(msg["From"], "me@gmail.com")
        self.assertEqual(msg["To"], "hr@co.com")
        self.assertEqual(msg["Subject"], "Subj")
        self.assertIn("Body text", msg.get_content())

    def test_build_message_attaches_file(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)
        msg = emailer.build_message("me@gmail.com", "hr@co.com", "S", "B",
                                    attachment_path=path)
        attachments = [p for p in msg.iter_attachments()]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), os.path.basename(path))


if __name__ == "__main__":
    unittest.main()

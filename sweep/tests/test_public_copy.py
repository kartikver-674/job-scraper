"""What a stranger is allowed to be told, and what they are allowed to reach.

Two classes of defect, both found by walking the rendered beta rather than
the templates:

  false copy      Three sentences described the OPERATOR's machine to a
                  visitor on Render — "Runs on this machine", "Saved as
                  profiles/<name>.py", "already on your disk". One visibly
                  untrue claim discounts every true one beside it, and the
                  trust copy on these screens is load-bearing.

  dead ends       /rescore and /second-key are in public.OPERATOR_ONLY, but
                  the templates gated their controls on `free_only` rather
                  than on `public_mode`. A visitor who pasted their OWN Apify
                  key is not free_only, so they were shown "Re-rank" and "Add
                  this key and continue" — both of which POST to a plain-text
                  404 with no navigation on it.

Asserted against the rendered HTML of every public screen, because that is
where the bug was: each individual template branch was defensible and the
combination was not. The local console is asserted in the same file, from
the same screens, so a fix that silences the public page by deleting the
operator's control fails here rather than in production.
"""

import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from sweep import app as app_module  # noqa: E402
from sweep.logic import SITE_LABELS, site_label  # noqa: E402
from sweep.tests.test_app import DERIVED  # noqa: E402
from sweep.tests.test_public_sweep import (PDF, give_rows,  # noqa: E402
                                           only_run, stack)
from sweep.tests.test_worker_link import CODE, render_app  # noqa: E402

# Every phrase that describes the operator's own machine, terminal, files or
# credentials. None of these may appear on a page a stranger can open.
OPERATOR_ONLY_WORDS = (
    "terminal running Sweep", "your own terminal", "Runs on this machine",
    "on your disk", "in this folder", "profiles/", ".py</code>",
    "pip install", "openpyxl", "Apify actor", "actor runs", ".env",
)

# The routes public.OPERATOR_ONLY refuses. A rendered public page must not
# offer a form that posts to one.
UNREACHABLE_ACTIONS = ("/rescore", "/second-key", "/merge", "/applied")


# A paid plan, as the engine's dry run would hand one back. Injected rather
# than fetched: the worker in `stack()` has no engine, so a real paid plan
# cannot be priced there — and the paid path is exactly the one whose screens
# used to leak the operator's controls, so it has to be renderable here.
PAID_PLAN = {
    "profile": "beta_user",
    "sites": {
        "linkedin": [{"keywords": k, "location": l}
                     for k in ("React Native Developer", "Full Stack Engineer")
                     for l in ("Bengaluru", "Gurgaon")],
        "naukri": [{"keywords": "React Native Developer",
                    "location": "Bengaluru"}],
    },
    "max_results": {"linkedin": 15, "naukri": 50},
    "free_sources": 6,
}

ROW = {"title": "React Native Developer", "company": "Acme", "score": "42",
       "source_site": "greenhouse:acme", "apply_url": "https://a/1",
       "location": "Remote", "date_posted": "2026-09-14", "salary": "",
       "experience_required": "", "matched_skills": "react native, node.js"}


@contextlib.contextmanager
def public_screens(free=True):
    """Every public screen's HTML, by path, for a visitor who got as far as
    results on the free or the paid path."""
    with stack() as (url, store):
        extra = {} if free else {"fetch_plan": lambda profile: dict(PAID_PLAN),
                                 "read_done": lambda profile, day: set(),
                                 "read_spend": lambda: 0.06,
                                 "read_rows": lambda profile: [dict(ROW)]}
        app = render_app(url, check_token=lambda t: (4.25, None), **extra)
        client = app.test_client()
        client.post("/beta", data={"code": CODE})
        client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                    content_type="multipart/form-data")
        client.post("/derive")
        client.post("/review", data={"name": "beta_user"})
        if free:
            client.post("/key/free")
        else:
            # The path that used to un-gate the operator's controls: a
            # visitor spending their OWN key is not free_only.
            client.post("/key", data={"token": "apify_api_" + "x" * 30})
        client.get("/configure")
        client.post("/run")
        if free:
            give_rows(store, only_run(store))
        pages = {}
        for path in ("/", "/review", "/key", "/configure", "/confirm",
                     "/running", "/results"):
            answer = client.get(path)
            assert answer.status_code == 200, (
                f"{path} answered {answer.status_code} on the "
                f"{'free' if free else 'paid'} path — this walk has to reach "
                f"every screen or it asserts nothing about them")
            pages[path] = answer.get_data(as_text=True)
        yield pages


class TestNothingDescribesTheOperatorsMachine(unittest.TestCase):

    def test_free_path_says_nothing_about_a_terminal_or_a_disk(self):
        with public_screens(free=True) as pages:
            self._assert_clean(pages)

    def test_paid_path_says_nothing_about_a_terminal_or_a_disk(self):
        with public_screens(free=False) as pages:
            self._assert_clean(pages)

    def _assert_clean(self, pages):
        for path, html in pages.items():
            for word in OPERATOR_ONLY_WORDS:
                with self.subTest(path=path, word=word):
                    # assertTrue, not assertNotIn: unittest prints the whole
                    # haystack on failure, and the haystack here is a rendered
                    # page. The message already names the page and the phrase.
                    self.assertTrue(
                        word not in html,
                        f"{path} tells a beta visitor about {word!r}, which "
                        f"is the operator's machine and not theirs")


class TestNoPublicControlLeadsToA404(unittest.TestCase):
    """The controls and the allowlist have to agree."""

    def test_free_path_offers_no_operator_only_form(self):
        with public_screens(free=True) as pages:
            self._assert_no_dead_forms(pages)

    def test_paid_path_offers_no_operator_only_form(self):
        with public_screens(free=False) as pages:
            self._assert_no_dead_forms(pages)

    def _assert_no_dead_forms(self, pages):
        for path, html in pages.items():
            for action in UNREACHABLE_ACTIONS:
                with self.subTest(path=path, action=action):
                    self.assertTrue(
                        f'action="{action}"' not in html,
                        f"{path} renders a form posting to {action}, which "
                        f"public mode answers with a plain-text 404")

    def test_those_routes_really_are_refused(self):
        """The other half of the pair: if one of them became reachable this
        test would pass vacuously above, so assert the refusal too."""
        with stack() as (url, _store):
            client = render_app(url).test_client()
            client.post("/beta", data={"code": CODE})
            for path in UNREACHABLE_ACTIONS:
                with self.subTest(path=path):
                    self.assertEqual(client.post(path).status_code, 404)


class TestTheLocalConsoleKeepsItsControls(unittest.TestCase):
    """Public mode hides these; local mode must not lose them."""

    def _local(self):
        app = app_module.create_app(
            state={"resume_text": "x", "derived": dict(DERIVED),
                   "profile": "kartik", "cap_usd": 4.25,
                   "credit_total_usd": 4.25},
            derive=lambda text, prefs: dict(DERIVED),
            read_rows=lambda profile: [
                {"title": "React Native Developer", "company": "Acme",
                 "score": "42", "source_site": "greenhouse:acme",
                 "apply_url": "https://a/1", "location": "Remote",
                 "date_posted": "2026-09-14", "salary": "",
                 "experience_required": "", "matched_skills": "react native"}],
            list_sweeps=lambda profile: [])
        app.config["TESTING"] = True
        return app

    def test_results_still_offers_the_re_rank_editor(self):
        body = self._local().test_client().get("/results").get_data(as_text=True)
        self.assertIn('action="/rescore', body)

    def test_review_still_explains_where_the_profile_file_goes(self):
        body = self._local().test_client().get("/review").get_data(as_text=True)
        self.assertIn("profiles/", body)


class TestBoardsAreNamedTheWayBoardsSpellThem(unittest.TestCase):

    def test_the_platform_is_taken_from_a_company_scoped_key(self):
        # sources/ats.py writes `platform:company`; the company has its own
        # column, so the source cell is the platform alone.
        self.assertEqual(site_label("greenhouse:sumup"), "Greenhouse")
        self.assertEqual(site_label("lever:stripe"), "Lever")

    def test_an_unknown_board_falls_through_to_its_own_key(self):
        self.assertEqual(site_label("brandnewboard"), "brandnewboard")

    def test_nothing_renders_as_the_word_none(self):
        self.assertEqual(site_label(None), "")
        self.assertEqual(site_label(""), "")

    def test_every_adapter_the_engine_ships_has_a_label(self):
        """A board the engine can return but this cannot name would reach the
        results screen as a lowercase key."""
        import sources.ats
        import sources.enterprise
        for key in sorted(sources.ats.ATS):
            with self.subTest(key=key):
                self.assertIn(key, SITE_LABELS)
        for key in sorted(sources.enterprise.EMPLOYERS):
            with self.subTest(key=key):
                self.assertIn(key, SITE_LABELS)


if __name__ == "__main__":
    unittest.main()

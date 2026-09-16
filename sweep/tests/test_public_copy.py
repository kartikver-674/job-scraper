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


class TestTheQueueIsVisible(unittest.TestCase):
    """The worker runs MAX_ACTIVE=1, so waiting behind somebody else is the
    NORMAL state for a beta with more than one visitor — and it used to be
    indistinguishable from a hang: an indeterminate bar, and a panel saying
    there was no progress to show.

    `queue_position` has been in the worker's status since it was written
    and nothing on Render read it. These drive the whole chain: worker
    status -> worker_link.read_queue -> snapshot() -> the screen.
    """

    def _running(self, position):
        """A public app whose sweep is at `position` in the queue."""
        app = app_module.create_app(
            state={"resume_text": "x", "derived": dict(DERIVED),
                   "profile": "beta_user", "free_only": True,
                   "raw_plan": {"profile": "beta_user", "sites": {},
                                "max_results": {}, "free_sources": 6},
                   "proc": _Alive(), "run_started_at": 0},
            derive=lambda text, prefs: dict(DERIVED),
            read_rows=lambda profile: [dict(ROW)],
            read_live=lambda profile, since: [dict(ROW)],
            read_done=lambda profile, day: set(),
            read_queue=lambda: position)
        app.config["TESTING"] = True
        # The template branches on public_mode, which harden() sets; this
        # test wants the public screen without a worker behind it.
        app.config["PUBLIC_MODE"] = True
        from sweep.logic import PUBLIC_STAGES
        app.config["STEPS"] = PUBLIC_STAGES
        app.state["plan"] = {"lines": [], "total": 0.0, "spend_cap": 0.0,
                             "free_sources": 6, "already_done": 0,
                             "over_cap": False, "total_searches": 0}
        return app

    def test_position_one_says_you_are_next(self):
        app = self._running(1)
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("You are in the queue", body)
        self.assertIn("Yours is next", body)
        # The progress JSON the poll reads carries it too, or the screen
        # would be right once and wrong four seconds later.
        live = app.test_client().get("/progress").get_json()
        self.assertTrue(live["queued"])
        self.assertEqual(live["queue_position"], 1)

    def test_a_later_position_is_counted_not_guessed_at(self):
        app = self._running(4)
        live = app.test_client().get("/progress").get_json()
        self.assertEqual(live["queue_position"], 4)
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("sweeps ahead of yours", body)
        # No duration promised for a queue whose length nobody can time.
        self.assertNotIn("40 minutes", body)

    def test_not_queued_reads_as_searching_not_as_position_zero(self):
        app = self._running(None)
        live = app.test_client().get("/progress").get_json()
        self.assertFalse(live["queued"])
        self.assertEqual(live["queue_position"], 0)
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("Sweep is searching for you", body)

    def test_the_free_path_gets_the_tab_promise_and_a_real_stage(self):
        """The old free screen was an indeterminate bar plus a panel saying
        there was no progress to show — indistinguishable from a hang."""
        body = self._running(None).test_client().get(
            "/running").get_data(as_text=True)
        self.assertIn("Sweep is searching for you", body)
        self.assertIn("You can close this tab", body)
        self.assertNotIn("no per-search progress to show", body)

    def test_a_free_sweep_never_shows_a_count_it_cannot_produce(self):
        """scraper.py calls fetch_free() once and emits a single checkpoint
        after it, so a free sweep has no rows until the end and live_feed()
        returns early on that path. A headline reading "0 jobs found so far"
        for the whole run would be the bar's own dishonesty with a number
        painted on it."""
        body = self._running(None).test_client().get(
            "/running").get_data(as_text=True)
        found = body[body.find("headline-figure"):]
        found = found[:found.find("</div>")]
        self.assertIn("display:none", found,
                      "the count is shown before there is anything to count")

    def test_a_local_sweep_reports_no_queue_rather_than_an_unknown(self):
        """There is nothing in front of a subprocess, so "not queued" is a
        fact locally rather than a missing reading."""
        app = app_module.create_app(
            state={"resume_text": "x", "derived": dict(DERIVED),
                   "profile": "kartik", "free_only": True,
                   "raw_plan": {"profile": "kartik", "sites": {},
                                "max_results": {}, "free_sources": 6},
                   "proc": _Alive(), "run_started_at": 0},
            derive=lambda text, prefs: dict(DERIVED),
            read_rows=lambda profile: [], read_live=lambda p, s: [],
            read_done=lambda profile, day: set())
        app.config["TESTING"] = True
        live = app.test_client().get("/progress").get_json()
        self.assertFalse(live["queued"])
        self.assertEqual(live["queue_position"], 0)


class _Alive:
    """A child that is still going, as far as _liveness() is concerned."""

    def poll(self):
        return None

    def terminate(self):
        pass


class TestTheShortlistSurvivesAPhone(unittest.TestCase):
    """The reflow is CSS over one DOM, so these assert the contract the
    stylesheet depends on rather than re-testing the browser: every cell the
    phone layout places by name has to carry that name."""

    AREAS = ("score", "role", "src", "loc", "pay", "exp", "skills", "act")

    def test_every_cell_carries_the_class_its_grid_area_is_named_for(self):
        with public_screens(free=True) as pages:
            html = pages["/results"]
        for area in self.AREAS:
            with self.subTest(area=area):
                self.assertIn(f'class="{area}', html,
                              f"no cell carries .{area}, so the phone grid "
                              f"cannot place it and the row falls back to "
                              f"auto-placement")

    def test_the_stylesheet_places_all_of_them(self):
        import pathlib
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        phone = css[css.index("@media (max-width: 47.99rem)"):]
        phone = phone[:phone.index("\n}\n")]
        for area in self.AREAS:
            with self.subTest(area=area):
                self.assertIn(f".listings td.{area}", phone)

    def test_the_touch_overrides_come_last(self):
        """Equal specificity, so source order decides. The block sat mid-file
        once and .remove's own 28px box, defined below it, silently won."""
        import pathlib
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        touch = css.index("@media (pointer: coarse) {\n  .stepper button")
        for defined_earlier in (".remove > span[aria-hidden] {",
                                ".pick-x {", ".stepper button {",
                                "button.primary, a.primary {"):
            with self.subTest(rule=defined_earlier):
                self.assertLess(css.index(defined_earlier), touch)


class TestExperienceReadsAsYearsAndMonths(unittest.TestCase):
    """`experience_months` is a TOTAL — local_extract and with_experience
    both write years * 12 + months — so every screen showing experience has
    to divide it, and `years_experience` alone is only ever whole years.

    A 22-month résumé therefore has years_experience 1, and "1 year" for
    someone with one year and ten months reads as a misparse of exactly the
    field the review screen exists to let people correct.
    """

    def test_the_split_comes_from_the_total_not_the_whole_years(self):
        from sweep.logic import experience_parts
        self.assertEqual(
            experience_parts({"years_experience": 1, "experience_months": 22}),
            (1, 10))

    def test_whole_years_are_the_fallback_when_there_is_no_total(self):
        from sweep.logic import experience_parts
        self.assertEqual(experience_parts({"years_experience": 3}), (3, 0))

    def test_nothing_read_is_not_a_zero(self):
        from sweep.logic import experience_parts, experience_text
        self.assertEqual(experience_parts({}), (None, 0))
        self.assertIsNone(experience_text({}))
        self.assertIsNone(experience_text(None))

    def test_the_wording(self):
        from sweep.logic import experience_text
        for derived, said in (
                ({"experience_months": 22}, "1 year 10 months"),
                ({"experience_months": 13}, "1 year 1 month"),
                ({"experience_months": 12}, "1 year"),
                ({"experience_months": 24}, "2 years"),
                ({"experience_months": 7}, "7 months"),
                ({"experience_months": 1}, "1 month"),
                ({"experience_months": 0}, "Less than a year"),
                ({"years_experience": 2}, "2 years"),
                ({"years_experience": 1}, "1 year")):
            with self.subTest(derived=derived):
                self.assertEqual(experience_text(derived), said)

    def test_the_profile_card_says_both(self):
        with public_screens(free=True) as pages:
            card = pages["/review"]
        # DERIVED carries whole years only, so this is the fallback path —
        # the months path is covered by the unit cases above and by
        # test_app's front-door rows.
        self.assertIn("of experience", card)

    def test_the_stepper_pair_is_labelled_for_what_it_holds(self):
        """Two controls, each with its own unit underneath, so the group
        heading names the fact rather than one of them."""
        with public_screens(free=True) as pages:
            card = " ".join(pages["/review"].split())
        self.assertIn('id="exp-label">Experience<', card)
        self.assertIn('name="experience_years"', card)
        self.assertIn('name="experience_months"', card)
        self.assertNotIn('id="exp-label">Years of experience<', card)


class TestGettingAnApifyKey(unittest.TestCase):
    """The key screen asks a stranger for a credential to a service most of
    them have never used. It used to explain where to find it in one
    sentence naming three places; it is a guide now, nested so that somebody
    who already has a key never opens it.

    The links do the work a screenshot cannot — step 2 lands on the exact
    page the token is on — which is why the guide has to read correctly with
    no pictures present at all.
    """

    def _key_screen(self):
        with public_screens(free=True) as pages:
            return " ".join(pages["/key"].split())

    def test_the_steps_are_there_without_any_screenshots(self):
        body = self._key_screen()
        self.assertIn("Don't have an Apify key yet?", body)
        for step in ("Create a free Apify account",
                     "Open your API token page",
                     "Copy your Personal API token"):
            with self.subTest(step=step):
                self.assertIn(step, body)

    def test_it_links_to_apify_rather_than_describing_a_path(self):
        body = self._key_screen()
        self.assertIn("https://apify.com/sign-up", body)
        # The deep link is the point: "Settings, then API & Integrations" is
        # a sentence, this is the page.
        self.assertIn("https://console.apify.com/settings/integrations", body)

    def test_every_outbound_link_is_safe_to_open(self):
        """target=_blank without rel=noopener hands the opened page a handle
        on this one — on the screen where an API key is typed."""
        import re
        body = self._key_screen()
        for tag in re.findall(r"<a [^>]*https://[^>]*>", body):
            with self.subTest(tag=tag[:60]):
                self.assertIn('target="_blank"', tag)
                self.assertIn("noopener", tag)
                self.assertIn("noreferrer", tag)

    def test_no_broken_images_when_the_shots_are_not_there(self):
        body = self._key_screen()
        self.assertNotIn('class="shot"', body)
        self.assertNotIn("apify/1-signup.png", body)

    def test_a_shot_appears_only_when_its_file_does(self):
        """The guide is wired to a directory listing read once at start-up,
        so adding a picture is adding a file — and a missing one is a quieter
        guide rather than a broken image."""
        app = app_module.create_app(derive=lambda t, p: dict(DERIVED))
        self.assertEqual(app.config["APIFY_GUIDE"], {})

        import os
        import pathlib
        folder = (pathlib.Path(app_module.__file__).parent
                  / "static" / "apify")
        folder.mkdir(parents=True, exist_ok=True)
        planted = folder / "1-signup.png"
        planted.write_bytes(b"\x89PNG\r\n\x1a\n")
        try:
            again = app_module.create_app(derive=lambda t, p: dict(DERIVED))
            self.assertEqual(again.config["APIFY_GUIDE"],
                             {"1-signup": "apify/1-signup.png"})
        finally:
            planted.unlink()
            with contextlib.suppress(OSError):
                os.rmdir(folder)

"""Search Preferences Correctness V1 — what the user picks is what runs.

The forensic audit (docs/search-preferences-forensic-audit.md) found seven
settings on one screen and three of them dead or degenerate in Free Sweep,
two silently reset on every return to the screen, and two spending money on
rows the engine was guaranteed to discard. These are the tests that say the
answers are now the same at both ends.

Deliberately end-to-end rather than per-module: every bug in that audit lived
in a JOIN — a template that rendered one thing and posted another, a scope
that reached the paid plan but not the free filter, a country code no profile
could override. A test of either side alone would have passed throughout. So
each case here drives the real route, renders the real profile through
make_profile, applies it through config's own overlay, and then asks the
engine what it would actually do with a row.

No network, no Apify, no worker.
"""

import copy
import os
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import config  # noqa: E402
import scraper  # noqa: E402
from sweep import app as app_module  # noqa: E402

DERIVED = {
    "field_summary": "Front-end engineer.",
    "notes": "Reads as a React developer.",
    "years_experience": 3,
    "skill_weights": [{"term": "react", "weight": 5},
                      {"term": "node", "weight": 4}],
    "penalty_terms": [{"term": "wordpress", "weight": 3}],
    "role_keywords": ["Frontend Developer", "React Developer"],
    "domain_half_a": ["react"], "domain_half_b": ["node"],
    "domain_title_terms": ["full stack"], "domain_bonus": 3,
    "skills_added": {},
}

FREE_PLAN = {"profile": "prefs_probe", "sites": {}, "max_results": {},
             "free_sources": 134}
PAID_PLAN = {
    "profile": "prefs_probe",
    "sites": {"linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 4},
    "max_results": {"linkedin": 15},
    "free_sources": 134,
}


def make_app(free=True, plan=None, **extra_state):
    """A Search-preferences session, already past the review screen."""
    state = {"profile": "prefs_probe", "derived": copy.deepcopy(DERIVED)}
    if free:
        state["free_only"] = True
        state["sites_enabled"] = {s: False for s in app_module.paid_sites()}
    else:
        state["cap_usd"] = 25.0
    state.update(extra_state)
    app = app_module.create_app(
        state=state, extract=lambda p: "x", derive=lambda t, p: copy.deepcopy(DERIVED),
        check_token=lambda t: (25.0, None),
        fetch_plan=lambda name: dict(plan or (FREE_PLAN if free else PAID_PLAN),
                                     profile=name),
        output_dir=tempfile.mkdtemp(), env_path=tempfile.mktemp())
    app.config.update(TESTING=True, PUBLIC_MODE=True)
    app.written = []
    app.write_profile = lambda n, src: app.written.append(src)
    return app


def form(**over):
    """Exactly what configure.html's own FormData produces in free mode."""
    base = {"scope": "remote", "locations": "", "max_age_days": "14",
            "min_comp_usd": "", "avoid": ""}
    base.update(over)
    return base


class EngineUnder:
    """Run the engine under one rendered profile, then put config back.

    config._overlay mutates the module globals in place and scraper imported
    those same objects by reference, which is the whole reason a profile can
    change how a sweep behaves — and the whole reason a test that forgets to
    restore them poisons every later one.
    """

    NAMES = ("SEARCH", "SITES", "SCORING", "SETTINGS", "FEEDS", "LOCATION_HINTS")

    def __init__(self, source):
        self.source = source

    def __enter__(self):
        self._saved = {n: copy.deepcopy(getattr(config, n)) for n in self.NAMES}
        module = types.ModuleType("prefs_probe")
        exec(compile(self.source, "prefs_probe.py", "exec"), module.__dict__)
        config._overlay(module)
        # scraper precompiles its term tables at import, so a SCORING change
        # means nothing until they are rebuilt — the same reason config.py's
        # own comment says the profile name is read at import time.
        scraper.PENALTY_PATTERNS = {
            t: (p, scraper._compile(t))
            for t, p in config.SCORING["penalty_terms"].items()}
        return self

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            live = getattr(config, name)
            if isinstance(live, list):
                live[:] = value
            else:
                live.clear()
                live.update(value)
        scraper.PENALTY_PATTERNS = {
            t: (p, scraper._compile(t))
            for t, p in config.SCORING["penalty_terms"].items()}
        return False


def day(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def job(title, location, posted=None, salary="", description="React and Node."):
    """One row in the engine's internal schema, as a free source produces it."""
    return {"Title": title, "Company": f"Co{abs(hash(title + location)) % 997}",
            "Location": location, "Salary": salary, "Experience": "",
            "Posted Date": posted or day(1), "Description": description,
            "Job URL": f"https://example.test/{abs(hash(title + location))}",
            "Source": "greenhouse:probe", "hires_home": ""}


# One row per case the audit's Phase 3 table asked about.
CORPUS = [
    job("React Developer A", "Anywhere in the World", description="Fully remote, work from anywhere."),
    job("React Developer B", "United States, Remote", description="Remote within the United States only."),
    job("React Developer C", "India, Remote", description="Remote for candidates in India."),
    job("React Developer D", "Bengaluru, Karnataka", description="Hybrid: three days a week in the office."),
    job("React Developer E", "Bangalore, India", description="Onsite in our Bangalore office."),
    job("React Developer F", "Hyderabad, Telangana", description="Onsite role."),
    job("React Developer G", "London, United Kingdom", description="Onsite in our London office."),
    job("React Developer H", "Berlin, Germany", description="Onsite in Berlin."),
    job("React Developer I", "Gurugram", description="Onsite."),
]


def kept(source, rows=None):
    """The titles the engine keeps, under a rendered profile."""
    with EngineUnder(source):
        # location_allowed is the FREE sources' own gate, applied as they are
        # read rather than in finalize — so a free-path test has to apply it
        # here too, or it would be testing only half the filter.
        survived = [r for r in (rows if rows is not None else CORPUS)
                    if scraper.location_allowed(r["Location"])]
        out = scraper.finalize([dict(r) for r in survived])
    return sorted(r["title"].rsplit(" ", 1)[-1] for r in out)


# ---------------------------------------------------------------------------
# Persistence — the form submission is authoritative (audit B1, B2, B5, B9, B10)
# ---------------------------------------------------------------------------
class TestPersistence(unittest.TestCase):
    def leave_and_return(self, app):
        """What the browser posts after navigating away and coming back.

        The screen is re-rendered, and the whole form is re-submitted because
        every control shares one <form> — so anything the template fails to
        render from state gets written back as an instruction. That is
        exactly how a 30-day window silently became 14 and a pay floor was
        silently deleted.
        """
        body = app.test_client().get("/configure").get_data(as_text=True)
        posted = form(
            scope=re.search(r'name="scope" value="(\w+)"[^>]*checked', body).group(1)
            if re.search(r'name="scope" value="\w+"[^>]*checked', body) else "remote",
            max_age_days=re.search(
                r'<option value="(\d+)" selected>', body).group(1),
            min_comp_usd=(re.search(
                r'name="min_comp_usd"[^>]*value="([^"]*)"', body) or
                re.match("", "")).group(1) if 'name="min_comp_usd"' in body else "",
            avoid=re.search(r'name="avoid"[^>]*value="([^"]*)"', body).group(1),
        )
        depth = re.search(r'name="max_results"[^>]*value="(\d*)"', body)
        if depth:
            posted["max_results"] = depth.group(1)
        app.test_client().post("/configure", data=posted)
        return body

    def test_a_pay_floor_survives_leaving_the_screen_and_coming_back(self):
        app = make_app()
        app.test_client().post("/configure", data=form(min_comp_usd="80000"))
        self.assertEqual(app.state["min_comp_usd"], 80000)
        body = self.leave_and_return(app)
        self.assertIn('name="min_comp_usd"', body)
        self.assertRegex(body, r'name="min_comp_usd"[^>]*value="80000"')
        self.assertEqual(app.state["min_comp_usd"], 80000)
        self.assertIn('"min_comp_usd": 80000', app.written[-1])

    def test_a_freshness_window_survives_leaving_the_screen_and_coming_back(self):
        app = make_app()
        app.test_client().post("/configure", data=form(max_age_days="30"))
        self.assertEqual(app.state["max_age_days"], 30)
        body = self.leave_and_return(app)
        self.assertIn('<option value="30" selected>', body)
        self.assertNotIn('<option value="14" selected>', body)
        self.assertEqual(app.state["max_age_days"], 30)

    def test_the_depth_box_shows_the_depth_that_is_in_force(self):
        # The box carried only placeholder="15", so a stored 25 displayed as
        # 15 — which is where "is the 15 real?" came from. The 15 was real;
        # the display was not.
        app = make_app(free=False)
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertRegex(body, r'name="max_results"[^>]*value="15"')
        app.test_client().post("/configure", data=dict(form(), max_results="25"))
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertRegex(body, r'name="max_results"[^>]*value="25"')
        self.assertIn('"max_results": 25', app.written[-1])

    def test_an_avoid_term_can_be_taken_back(self):
        app = make_app()
        app.test_client().post("/configure", data=form(avoid="Salesforce, CRM"))
        self.assertIn("'salesforce': -12", app.written[-1])
        self.assertIn("'crm': -12", app.written[-1])

        app.test_client().post("/configure", data=form(avoid="Salesforce"))
        self.assertIn("'salesforce': -12", app.written[-1])
        self.assertNotIn("'crm'", app.written[-1])

    def test_clearing_the_box_clears_the_user_terms_but_not_the_models(self):
        app = make_app()
        app.test_client().post("/configure", data=form(avoid="Salesforce"))
        app.test_client().post("/configure", data=form(avoid=""))
        source = app.written[-1]
        self.assertNotIn("salesforce", source)
        # The derivation's own penalty stays: it is not the user's to clear.
        self.assertIn("'wordpress': -3", source)

    def test_the_submitted_form_wins_with_no_estimate_call_at_all(self):
        # The point of POST /configure. With JavaScript unavailable the
        # live-cost fetch never fires, and before this every control on the
        # screen was inert.
        app = make_app()
        r = app.test_client().post("/configure", data=form(
            scope="india", max_age_days="7", min_comp_usd="40000",
            avoid="Salesforce"))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(app.state["max_age_days"], 7)
        self.assertEqual(app.state["min_comp_usd"], 40000)
        self.assertEqual(app.state["avoid"], ["Salesforce"])
        self.assertEqual(app.state["scope"], "india")

    def test_an_invalid_field_comes_back_on_the_screen_and_changes_nothing(self):
        app = make_app()
        app.test_client().post("/configure", data=form(max_age_days="30"))
        r = app.test_client().post("/configure", data=form(min_comp_usd="-1"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Minimum pay", r.get_data(as_text=True))
        self.assertEqual(app.state["max_age_days"], 30)
        self.assertIsNone(app.state.get("min_comp_usd"))


# ---------------------------------------------------------------------------
# Defaults — the untouched screen runs the sweep it describes (audit B11)
# ---------------------------------------------------------------------------
class TestDefaults(unittest.TestCase):
    def plan_of(self, source):
        """The paid search plan a rendered profile produces, per site."""
        with EngineUnder(source):
            return {site: [(s["keywords"], s["location"])
                           for s in scraper.plan_for_site(
                               site, types.SimpleNamespace(keywords=None,
                                                           test=False, limit=None))]
                    for site in ("linkedin", "indeed")}

    def test_an_untouched_screen_and_a_touched_one_plan_the_same_sweep(self):
        # It used to quote four LinkedIn searches on arrival (config's own
        # India + Remote) and two after any click, under a radio that said
        # "Remote roles" the whole time.
        untouched = make_app(free=False)
        untouched.test_client().get("/configure")
        before = self.plan_of(untouched.written[-1])

        touched = make_app(free=False)
        touched.test_client().get("/configure")
        touched.test_client().post("/configure", data=form())
        after = self.plan_of(touched.written[-1])

        self.assertEqual(before, after)
        self.assertEqual(before["linkedin"],
                         [(k, "Remote") for k in DERIVED["role_keywords"]])

    def test_changing_a_setting_and_changing_it_back_is_a_no_op(self):
        app = make_app(free=False)
        app.test_client().post("/configure", data=form())
        baseline = app.written[-1]
        app.test_client().post("/configure", data=form(scope="india",
                                                       max_age_days="30"))
        app.test_client().post("/configure", data=form())
        self.assertEqual(app.written[-1], baseline)


# ---------------------------------------------------------------------------
# Free Sweep — all three answers are different sweeps (audit B3, B4, B6)
# ---------------------------------------------------------------------------
class TestFreeSweep(unittest.TestCase):
    def rendered(self, **over):
        app = make_app()
        app.test_client().post("/configure", data=form(**over))
        return app.written[-1]

    def test_the_three_answers_filter_differently(self):
        remote = kept(self.rendered(scope="remote"))
        india = kept(self.rendered(scope="india"))
        world = kept(self.rendered(scope="global"))
        # They used to be: remote = 2 of 9, india = 9 of 9, global = 9 of 9,
        # with india and global byte-identical.
        self.assertNotEqual(india, world)
        self.assertNotEqual(remote, india)
        self.assertNotEqual(remote, world)

    def test_remote_keeps_remote_roles_and_drops_office_ones(self):
        got = kept(self.rendered(scope="remote"))
        self.assertIn("A", got)          # worldwide
        self.assertIn("C", got)          # geo-locked TO India, reachable
        self.assertNotIn("B", got)       # remote, United States only
        for office in ("D", "E", "F", "G", "H", "I"):
            self.assertNotIn(office, got, f"{office} is an office role")

    def test_india_keeps_indian_office_roles_only(self):
        got = kept(self.rendered(scope="india"))
        self.assertEqual(got, ["D", "E", "F", "I"])   # BLR hybrid, BLR, HYD, GGN
        # Every spelling a real board actually uses. "Bangalore, India",
        # "Bengaluru, Karnataka", "Hyderabad, Telangana" and "Gurugram" were
        # read off 2,083 rows in output/; matching only "India" would have
        # kept one of the four.
        self.assertNotIn("A", got)       # remote is not an office role
        self.assertNotIn("G", got)       # London
        self.assertNotIn("H", got)       # Berlin

    def test_global_keeps_office_roles_anywhere(self):
        got = kept(self.rendered(scope="global"))
        self.assertEqual(got, ["D", "E", "F", "G", "H", "I"])
        self.assertNotIn("A", got)
        self.assertNotIn("B", got)
        self.assertNotIn("C", got)

    def test_a_picked_city_narrows_a_free_sweep(self):
        # The headline dead control. Free sources have no location parameter,
        # so the picker reached none of them: LOCATION_HINTS was [] and no
        # generated profile could set it.
        source = self.rendered(scope="india", locations="Bengaluru")
        self.assertIn("LOCATION_HINTS", source)
        got = kept(source)
        self.assertEqual(got, ["D", "E"])       # the two Bengaluru rows
        self.assertNotIn("F", got)              # Hyderabad
        self.assertNotIn("I", got)              # Gurugram

    def test_two_picked_cities_match_either(self):
        got = kept(self.rendered(scope="india", locations="Bengaluru, Hyderabad"))
        self.assertEqual(got, ["D", "E", "F"])

    def test_a_picked_country_narrows_a_free_sweep(self):
        got = kept(self.rendered(scope="global", locations="United Kingdom"))
        self.assertEqual(got, ["G"])

    def test_clearing_the_picker_goes_back_to_the_whole_scope(self):
        app = make_app()
        app.test_client().post("/configure",
                               data=form(scope="india", locations="Bengaluru"))
        self.assertEqual(kept(app.written[-1]), ["D", "E"])
        app.test_client().post("/configure", data=form(scope="india", locations=""))
        self.assertEqual(kept(app.written[-1]), ["D", "E", "F", "I"])

    def test_a_posting_with_no_location_is_kept(self):
        # Defined rather than incidental. 0 of 7,132 real rows in output/ have
        # a blank location, and this file's standing rule is that a blank
        # signal is "the posting didn't say", never "no".
        rows = [job("React Developer Z", "")]
        self.assertEqual(kept(self.rendered(scope="india"), rows), ["Z"])

    def test_free_mode_does_not_offer_a_depth_control(self):
        # It reached nothing: fetch_free() has no depth argument, and every
        # free adapter pulls a fixed amount. It was rendered, validated,
        # stored, shipped to the worker and read by nobody.
        body = make_app().test_client().get("/configure").get_data(as_text=True)
        self.assertNotIn('name="max_results"', body)
        # And no copy about it either: a control that is not there needs no
        # explanation, and the paid-only wording would be a second thing to
        # reconcile.
        self.assertNotIn("Jobs per search", body)

    def test_the_free_summary_shows_only_what_is_active(self):
        app = make_app()
        app.test_client().post("/configure",
                               data=form(scope="india", locations="Bengaluru"))
        body = app.test_client().get("/configure").get_data(as_text=True)
        summary = body.split("What Sweep will search", 1)[1].split("</dl>", 1)[0]
        self.assertIn("Onsite or hybrid, in India", summary)
        self.assertIn("Bengaluru", summary)
        # And no depth row, because there is no per-search depth here.
        self.assertNotIn("Per search", summary)

    def test_the_summary_does_not_claim_a_location_under_remote(self):
        # "Where: Bengaluru" on a sweep that searches everywhere was the
        # audit's clearest example of the screen lying.
        app = make_app()
        app.test_client().post("/configure",
                               data=form(scope="india", locations="Bengaluru"))
        app.test_client().post("/configure",
                               data=form(scope="remote", locations="Bengaluru"))
        body = app.test_client().get("/configure").get_data(as_text=True)
        summary = body.split("What Sweep will search", 1)[1].split("</dl>", 1)[0]
        self.assertIn("Remote roles", summary)
        self.assertNotIn("Bengaluru", summary)
        self.assertEqual(app.state["locations"], ["Remote"])


# ---------------------------------------------------------------------------
# Avoid terms — ranking only, and what the penalty is worth (audit B14)
# ---------------------------------------------------------------------------
class TestAvoidTerms(unittest.TestCase):
    ROWS = [job("React Developer plain", "Anywhere in the World",
                description="React, Node, TypeScript."),
            job("React Developer crm", "Anywhere in the World",
                description="React front end over a Salesforce CRM backend.")]

    def scores(self, source):
        with EngineUnder(source):
            rows = scraper.finalize([dict(r) for r in self.ROWS])
        return {r["title"].rsplit(" ", 1)[-1]: r["score"] for r in rows}

    def rendered(self, avoid):
        app = make_app()
        app.test_client().post("/configure", data=form(avoid=avoid))
        return app.written[-1]

    def test_avoiding_a_term_lowers_the_rank_and_removes_nothing(self):
        before = self.scores(self.rendered(""))
        after = self.scores(self.rendered("Salesforce"))
        self.assertEqual(set(before), set(after))          # same jobs
        self.assertEqual(before["plain"], after["plain"])  # untouched
        self.assertEqual(after["crm"], before["crm"] - 12)

    def test_the_limit_is_stated_and_enforced(self):
        app = make_app()
        terms = ", ".join(f"term{i}" for i in range(app_module.MAX_AVOID_TERMS + 1))
        r = app.test_client().post("/configure", data=form(avoid=terms))
        self.assertEqual(r.status_code, 400)
        body = r.get_data(as_text=True)
        self.assertIn(str(app_module.MAX_AVOID_TERMS), body)
        self.assertIsNone(app.state.get("avoid"))
        # Rejected, not truncated.
        ok = ", ".join(f"term{i}" for i in range(app_module.MAX_AVOID_TERMS))
        self.assertEqual(
            app.test_client().post("/configure",
                                   data=form(avoid=ok)).status_code, 303)
        self.assertEqual(len(app.state["avoid"]), app_module.MAX_AVOID_TERMS)

    def test_repeats_do_not_count_twice_against_the_limit(self):
        app = make_app()
        app.test_client().post("/configure", data=form(avoid="CRM, crm, CRM"))
        self.assertEqual(app.state["avoid"], ["CRM"])


# ---------------------------------------------------------------------------
# Paid actor inputs — the two that spend money (audit B7, B8, B16)
# ---------------------------------------------------------------------------
class TestPaidActorInputs(unittest.TestCase):
    def inputs(self, site, source):
        with EngineUnder(source):
            args = types.SimpleNamespace(keywords=None, test=False, limit=None)
            return [scraper.build_input(site, scraper.effective_search(site, s))
                    for s in scraper.plan_for_site(site, args)]

    def rendered(self, **over):
        app = make_app(free=False)
        app.test_client().post("/configure", data=form(**over))
        return app.written[-1]

    def test_remote_keeps_the_linkedin_remote_filter(self):
        for url in (u for i in self.inputs("linkedin", self.rendered(scope="remote"))
                    for u in i["urls"]):
            self.assertIn("f_WT=2", url)

    def test_a_city_cannot_strip_the_linkedin_remote_filter(self):
        # THE money bug. The picker replaced the "Remote" token with a city,
        # _build_linkedin_url stopped adding f_WT=2, and LinkedIn was billed
        # for onsite rows finalize() then discarded for not being remote.
        # Two guards: the picker is dropped under remote, AND the scope
        # states remote_only rather than relying on the token.
        source = self.rendered(scope="remote", locations="Bengaluru")
        for url in (u for i in self.inputs("linkedin", source) for u in i["urls"]):
            self.assertIn("f_WT=2", url)
        self.assertIn('"remote_only": True', source)

    def test_a_forged_remote_plus_city_still_cannot_buy_onsite_rows(self):
        # Belt to the picker's braces: the screen hides the control, this is
        # what holds when the screen is not there.
        source = self.rendered(scope="remote")
        with EngineUnder(source):
            config.SITES["linkedin"]["locations"] = ["Bengaluru"]
            args = types.SimpleNamespace(keywords=None, test=False, limit=None)
            urls = [u for s in scraper.plan_for_site("linkedin", args)
                    for u in scraper.build_input(
                        "linkedin", scraper.effective_search("linkedin", s))["urls"]]
        for url in urls:
            self.assertIn("f_WT=2", url)

    def test_indeed_searches_the_market_the_location_is_in(self):
        for country, picked in (("GB", "United Kingdom"), ("DE", "Germany"),
                                ("US", "United States")):
            source = self.rendered(scope="global", locations=picked)
            got = {i["country"] for i in self.inputs("indeed", source)}
            self.assertEqual(got, {country}, picked)

    def test_indeed_searches_india_for_an_india_sweep(self):
        source = self.rendered(scope="india")
        self.assertEqual({i["country"] for i in self.inputs("indeed", source)},
                         {"IN"})

    def test_indeed_refuses_a_location_it_cannot_map_to_a_market(self):
        # Fail closed, like LINKEDIN_GEO_IDS: a wrong market does not raise,
        # it bills for the wrong country's jobs.
        with self.assertRaises(ValueError) as caught:
            scraper.build_input("indeed", {"keywords": "k", "location": "Atlantis",
                                           "max_results": 15})
        self.assertIn("INDEED_COUNTRIES", str(caught.exception))

    def test_duplicate_locations_do_not_duplicate_paid_searches(self):
        source = self.rendered(scope="india", locations="Delhi, Delhi, Delhi")
        seen = [i["location"] for i in self.inputs("indeed", source)]
        self.assertEqual(sorted(set(seen)), ["Delhi"])
        self.assertEqual(len(seen), len(DERIVED["role_keywords"]))

    def test_the_paid_summary_states_the_depth_in_force(self):
        app = make_app(free=False)
        app.test_client().post("/configure", data=dict(form(), max_results="25"))
        body = app.test_client().get("/confirm").get_data(as_text=True)
        summary = body.split("<dl", 1)[1].split("</dl>", 1)[0]
        self.assertIn("Per search", summary)
        self.assertIn("25 jobs", summary)


# ---------------------------------------------------------------------------
# Navigation and starting over (audit B12)
# ---------------------------------------------------------------------------
class TestNavigation(unittest.TestCase):
    def test_going_on_to_confirm_and_back_keeps_every_value(self):
        app = make_app(free=False)
        client = app.test_client()
        client.post("/configure", data=dict(
            form(scope="india", locations="Bengaluru", max_age_days="30",
                 min_comp_usd="80000", avoid="Salesforce"), max_results="25"))
        client.get("/confirm")
        body = client.get("/configure").get_data(as_text=True)
        self.assertIn('<option value="30" selected>', body)
        self.assertRegex(body, r'name="min_comp_usd"[^>]*value="80000"')
        self.assertRegex(body, r'name="max_results"[^>]*value="25"')
        self.assertRegex(body, r'name="avoid"[^>]*value="Salesforce"')
        self.assertIn('picked: ["Bengaluru"]', body)
        self.assertRegex(body, r'name="scope" value="india"[^>]*checked')

    def test_a_new_resume_resets_every_preference(self):
        # It used to clear the avoid-list (which lived inside `derived`) and
        # keep the other six, with nothing on screen saying which.
        app = make_app()
        app.test_client().post("/configure", data=form(
            scope="india", locations="Bengaluru", max_age_days="30",
            min_comp_usd="80000", avoid="Salesforce"))
        app.state["resume_text"] = "old"
        for key in ("scope", "locations", "max_age_days", "min_comp_usd", "avoid"):
            self.assertIsNotNone(app.state.get(key), key)

        import io
        app.test_client().post("/resume", data={
            "resume": (io.BytesIO(b"%PDF-1.4\nhello"), "cv.pdf")},
            content_type="multipart/form-data")
        for key in app_module.PREFERENCE_KEYS:
            self.assertIsNone(app.state.get(key), f"{key} leaked to the next résumé")

    def test_the_advanced_badge_reports_the_real_state(self):
        app = make_app()
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertIn("using defaults", body)
        app.test_client().post("/configure", data=form(max_age_days="30"))
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertIn("customised", body)
        self.assertNotIn("using defaults", body)


# ---------------------------------------------------------------------------
# One form, one submission (audit: mobile/desktop consistency)
# ---------------------------------------------------------------------------
class TestOneForm(unittest.TestCase):
    def test_every_control_appears_exactly_once(self):
        # Responsiveness here is CSS only, so there is no second copy of a
        # control that could submit a different value on a phone. Asserted
        # structurally, because that is the property — not the screenshot.
        for free in (True, False):
            body = make_app(free=free).test_client().get(
                "/configure").get_data(as_text=True)
            for field in ("locations", "max_age_days", "min_comp_usd", "avoid"):
                self.assertEqual(body.count(f'name="{field}"'), 1,
                                 f"{field}, free={free}")
            self.assertEqual(len(re.findall(r'name="scope"', body)), 3)
            self.assertEqual(body.count('name="max_results"'), 0 if free else 1)

    def test_the_preferences_form_can_be_submitted_without_javascript(self):
        for free, action in ((True, "/run"), (False, "/configure")):
            body = make_app(free=free).test_client().get(
                "/configure").get_data(as_text=True)
            tag = re.search(r'<form class="split"[^>]*>', body, re.S).group(0)
            self.assertIn('method="post"', tag)
            self.assertIn(f'action="{action}"', tag)
            self.assertRegex(body, r'<button class="primary[^"]*" type="submit"')

    def test_no_submit_button_disables_itself_on_click(self):
        """A submit button that disables itself in its own click handler
        never submits.

        Shipped and reported within the hour: the Start Free Sweep button
        carried @click="sent = true" next to :disabled="sent", Alpine
        flushed the binding before the click's default action ran, and the
        form-submission algorithm re-checks the submitter — so the button
        greyed out and nothing happened. The guard belongs on the FORM's
        @submit, which fires once the browser has already committed.
        """
        checked = 0
        for free in (True, False):
            body = make_app(free=free).test_client().get(
                "/configure").get_data(as_text=True)
            form_tag = re.search(r'<form class="split"[^>]*>', body, re.S).group(0)
            for tag in re.findall(r"<button[^>]*>", body, re.S):
                disabled = re.search(r':disabled="([^"]+)"', tag)
                if 'type="submit"' not in tag or not disabled:
                    continue
                checked += 1
                flag = disabled.group(1).strip()
                click = re.search(r'@click(?:\.\w+)*="([^"]*)"', tag)
                if click:
                    # An assignment to the same name, not merely a mention.
                    self.assertNotRegex(
                        click.group(1), rf"\b{re.escape(flag)}\s*=[^=]",
                        f"this submit button cancels itself "
                        f"(free={free}): {tag}")
                # ...and something else has to be setting it, or the guard
                # against a double-click is not there at all.
                self.assertIn(f'@submit="{flag} = true"', form_tag)
        # The assertions above are all conditional, so the loop has to prove
        # it found something — a renamed class or attribute would otherwise
        # make this test quietly vacuous.
        self.assertEqual(checked, 1, "expected exactly one guarded submit button")

    def test_the_free_start_button_carries_the_preferences(self):
        # It used to be a second <form> of its own posting nothing, which is
        # why preferences had to be saved by the estimate request firing.
        started = []
        app = make_app()
        app.start_sweep = None
        app = app_module.create_app(
            state={"profile": "prefs_probe", "derived": copy.deepcopy(DERIVED),
                   "free_only": True,
                   "sites_enabled": {s: False for s in app_module.paid_sites()}},
            extract=lambda p: "x", derive=lambda t, p: copy.deepcopy(DERIVED),
            fetch_plan=lambda name: dict(FREE_PLAN, profile=name),
            start_sweep=lambda name: started.append(name) or FakeProc(),
            read_spend=lambda: None,
            output_dir=tempfile.mkdtemp(), env_path=tempfile.mktemp())
        app.config.update(TESTING=True, PUBLIC_MODE=True)
        app.written = []
        app.write_profile = lambda n, src: app.written.append(src)
        r = app.test_client().post("/run", data=form(scope="india",
                                                     max_age_days="7"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(started, ["prefs_probe"])
        self.assertEqual(app.state["scope"], "india")
        self.assertEqual(app.state["max_age_days"], 7)
        self.assertIn('"work_scope": \'india\'', app.written[-1])


class FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass


if __name__ == "__main__":
    unittest.main()

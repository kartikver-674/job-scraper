"""Refuse to compare two profiles whose envelopes differ.

WHAT THIS EXISTS BECAUSE OF
---------------------------
The first live comparison of the local pipeline against Gemini's returned
8 jobs for one and 16 for the other, and the difference was not the
pipelines. Three SETTINGS keys differed:

    max_age_days      local 30    gemini 14
    min_comp_usd      local None  gemini 10000
    remote_scopes     local None  gemini []

The third did the damage. make_profile.render() OMITS a setting entirely
when prefs pass None, so config.py's own default applies — and config
filters to remote-only. 16 of 60 rows were removed from the local run for
a reason that had nothing to do with its keywords. The run cost $0.24 and
told us nothing, and the mistake was invisible in the output: both runs
printed a plausible "Filtered out:" line, just with different filters.

A re-score was then tried to avoid paying twice, and had to be discarded
as well: rescore_from_apify.py scans RECENT APIFY RUNS, so it pulled all
120 rows from both sweeps and scored one pipeline's keywords over the
other's results.

So: before spending anything on a comparison, run this. Two profiles
being compared may differ ONLY in the fields under test. Everything that
decides which rows survive has to be identical, or the comparison is
measuring the settings.

    python -m bench.envelope sarthak_local sarthak_gemini_test
    python -m bench.envelope --demo
"""

import sys

# The keys that decide which rows survive scoring. A difference in any of
# them changes the result for reasons unrelated to the search fields.
GATES = ("max_age_days", "min_comp_usd", "remote_scopes", "drop_excluded",
         "max_experience_years", "experience_aggregate", "drop_undated",
         "min_score", "max_results", "max_spend_usd")

# Fields a comparison is ALLOWED to differ in — the ones under test.
UNDER_TEST = ("role_keywords", "skill_weights", "penalty_terms",
              "frontend_terms", "backend_terms", "fullstack_title_terms",
              "fullstack_bonus")


def envelope(module):
    """The settings that must match, read from a profile module."""
    import config

    settings = dict(config.SETTINGS)
    settings.update(getattr(module, "SETTINGS", {}) or {})
    search = dict(config.SEARCH)
    search.update(getattr(module, "SEARCH", {}) or {})
    got = {}
    for key in GATES:
        got[key] = settings.get(key, search.get(key))
    # Locations and enabled sites decide what is even fetched.
    got["locations"] = tuple(search.get("locations") or ())
    got["sites"] = tuple(sorted(
        name for name, site in (getattr(module, "SITES", None)
                                or config.SITES).items()
        if site.get("enabled")))
    return got


def differences(a, b):
    """{key: (a, b)} for every envelope key the two disagree on."""
    left, right = envelope(a), envelope(b)
    return {key: (left.get(key), right.get(key))
            for key in sorted(set(left) | set(right))
            if left.get(key) != right.get(key)}


def check(name_a, name_b):
    """Print the verdict. Exit non-zero when the two are not comparable."""
    import importlib

    a = importlib.import_module("profiles." + name_a)
    b = importlib.import_module("profiles." + name_b)
    diff = differences(a, b)
    if not diff:
        print(f"comparable: {name_a} and {name_b} differ in no envelope key")
        return 0
    print(f"NOT COMPARABLE: {name_a} vs {name_b}")
    for key, (left, right) in diff.items():
        print(f"  {key:<24} {left!r:<30} {right!r}")
    print("\nA comparison run now would measure these, not the search "
          "fields.\nThe last time this happened it cost $0.24 and one "
          "invalid result.")
    return 1


def demo():
    class Fake:
        def __init__(self, settings=None, search=None, sites=None):
            self.SETTINGS = settings or {}
            self.SEARCH = search or {}
            if sites is not None:
                self.SITES = sites

    base = Fake({"max_age_days": 14, "min_comp_usd": 10000,
                 "remote_scopes": []}, {"locations": ["Delhi"]})
    same = Fake({"max_age_days": 14, "min_comp_usd": 10000,
                 "remote_scopes": []}, {"locations": ["Delhi"]})
    assert differences(base, same) == {}

    # The exact three that made the first live comparison worthless.
    for key, value in (("max_age_days", 30), ("min_comp_usd", None),
                       ("remote_scopes", None)):
        settings = dict(base.SETTINGS)
        settings[key] = value
        assert key in differences(base, Fake(settings,
                                             {"locations": ["Delhi"]})), key

    # An omitted key is NOT the same as a matching one: it inherits
    # config's default, which is how remote-only filtering arrived
    # unannounced. envelope() resolves the inheritance so the difference
    # is visible rather than implied.
    missing = Fake({"max_age_days": 14, "min_comp_usd": 10000},
                   {"locations": ["Delhi"]})
    assert "remote_scopes" in differences(base, missing)

    # Locations and enabled sites decide what is fetched at all.
    assert "locations" in differences(
        base, Fake(dict(base.SETTINGS), {"locations": ["Delhi", "Pune"]}))

    # The fields under test are allowed to differ and are not checked.
    loud = Fake(dict(base.SETTINGS),
                {"locations": ["Delhi"], "role_keywords": ["anything"]})
    assert differences(base, loud) == {}
    assert "role_keywords" not in GATES
    print("envelope demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(names) != 2:
        sys.exit(__doc__)
    raise SystemExit(check(*names))


if __name__ == "__main__":
    main()

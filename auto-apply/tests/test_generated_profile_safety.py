"""What a résumé is allowed to put in a file config.py imports.

render() builds profiles/<name>.py with an f-string. Every value goes
through repr() — except the two free-prose fields, field_summary and
notes, which were interpolated raw into the module's triple-quoted
docstring. An f-string escapes nothing, so prose containing

    \"\"\"
    WHATEVER = 1

closed the docstring and left WHATEVER as a real top-level statement.
Proven offline with a benign marker; the payload was never executed, and
is not executed here either.

The prose is model output, and the model is reading a stranger's PDF.
Two layers, because one of them is a razor and the other is a net:

  _prose()        removes the only two symbols with syntactic power
                  inside a \"\"\"...\"\"\" block.
  check_module()  parses the result and refuses it unless it is the
                  docstring plus an allowlist of data assignments.

check_module judges GENERATED source only. The hand-written profiles in
profiles/ import helpers and define their own locals; they are reviewed,
in git, and none of this applies to them.
"""

import ast
import glob
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_profile  # noqa: E402

PREFS = {"locations": ["Remote"], "exclude_levels": ["intern"],
         "min_comp_usd": None, "avoid": []}

BASE = {"field_summary": "s", "notes": "n", "years_experience": 2,
        "role_keywords": ["react native developer"],
        "skill_weights": [{"term": "react native", "weight": 5}],
        "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
        "domain_title_terms": [], "domain_bonus": 0,
        "title_hints": ["react native"], "title_exclude": []}

# The marker the audit used. Inert: it is only ever searched for in an AST,
# never run.
MARKER = "AUDIT_MARKER"


def render(summary="s", notes="n", name="probe"):
    return make_profile.render(name, dict(BASE, field_summary=summary,
                                          notes=notes), PREFS)


def assignments(source):
    """Top-level names the generated module actually defines."""
    return sorted({t.id for node in ast.parse(source).body
                   if isinstance(node, ast.Assign)
                   for t in node.targets if isinstance(t, ast.Name)})


class TestProseCannotBecomeCode(unittest.TestCase):

    def test_the_audit_payload_no_longer_creates_a_statement(self):
        source = render(summary=f'ok\n"""\n{MARKER} = 1\n__doc__ = """x')
        self.assertNotIn(MARKER, assignments(source))
        self.assertEqual(assignments(source),
                         sorted(set(assignments(source))
                                & make_profile.PROFILE_NAMES))

    def test_a_long_run_of_quotes_does_not_rebuild_the_escape(self):
        """str.replace('\"\"\"', '\"') scans left to right without rescanning,
        so nine quotes would collapse back into three. _prose collapses
        every RUN instead."""
        for count in range(3, 13):
            source = render(summary="ok\n" + '"' * count + f"\n{MARKER} = 1")
            self.assertNotIn(MARKER, assignments(source), count)

    def test_notes_is_guarded_as_well_as_field_summary(self):
        source = render(notes=f'ok\n"""\n{MARKER} = 1\n__doc__ = """x')
        self.assertNotIn(MARKER, assignments(source))

    def test_a_trailing_backslash_cannot_escape_the_closing_quotes(self):
        for tail in ("ok\\", "ok\\\\", "ok\\\\\\"):
            self.assertNotIn(MARKER, assignments(render(summary=tail)), tail)

    def test_backslash_sequences_do_not_become_escapes(self):
        source = render(summary="a\\nb\\tc\\x41\\N{BULLET}")
        self.assertNotIn("\\", source.split("SEARCH =")[0].split('"""')[1])

    def test_ordinary_prose_still_reads_as_prose(self):
        """The guard must not mangle the common case."""
        source = render(summary="Full-stack React Native developer, 2 years.")
        self.assertIn("Full-stack React Native developer, 2 years.", source)

    def test_quotes_and_accents_survive(self):
        source = render(summary="Ingénieur — she said 'hello' once")
        self.assertIn("Ingénieur — she said 'hello' once", source)

    def test_empty_and_missing_prose_render(self):
        for value in ("", None):
            self.assertTrue(ast.parse(render(summary=value, notes=value)))


class TestTheGeneratedModuleIsOnlyData(unittest.TestCase):

    def test_a_normal_render_parses_and_defines_only_allowed_names(self):
        names = assignments(render())
        self.assertTrue(names)
        self.assertTrue(set(names) <= make_profile.PROFILE_NAMES,
                        set(names) - make_profile.PROFILE_NAMES)

    def test_every_allowed_value_is_a_literal(self):
        """No call, no name, no operator — ast.literal_eval proves it
        without running anything."""
        for node in ast.parse(render()).body:
            if isinstance(node, ast.Assign):
                ast.literal_eval(node.value)

    def test_an_extra_assignment_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            make_profile.check_module('"""d"""\nSEARCH = {}\nEVIL = 1\n')
        self.assertIn("EVIL", str(caught.exception))

    def test_an_import_is_refused(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nimport os\nSEARCH = {}\n')
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nfrom os import path\n')

    def test_a_call_is_refused_even_under_an_allowed_name(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nSEARCH = open("x")\n')

    def test_a_bare_name_is_refused_even_under_an_allowed_name(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nSEARCH = __builtins__\n')

    def test_a_function_or_class_is_refused(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\ndef f():\n    pass\n')
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nclass C:\n    pass\n')

    def test_a_bare_expression_is_refused(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\nprint(1)\n')

    def test_a_dunder_assignment_is_refused(self):
        with self.assertRaises(ValueError):
            make_profile.check_module('"""d"""\n__doc__ = "x"\n')

    def test_a_malformed_module_is_refused_with_its_syntax_error(self):
        with self.assertRaises(ValueError) as caught:
            make_profile.check_module('"""d"""\nSEARCH = {\n')
        self.assertIn("not valid Python", str(caught.exception))

    def test_the_docstring_alone_is_fine(self):
        make_profile.check_module('"""just a docstring"""\n')

    def test_check_module_returns_the_source_so_render_can_wrap_it(self):
        source = '"""d"""\nSEARCH = {}\n'
        self.assertIs(make_profile.check_module(source), source)


class TestExistingProfilesStillWork(unittest.TestCase):
    """The nine hand-written profiles are NOT generated source, and the
    check is deliberately not pointed at them."""

    def paths(self):
        return [p for p in sorted(glob.glob(
            os.path.join(REPO_ROOT, "profiles", "*.py")))
            if not os.path.basename(p).startswith("_")]

    def test_there_are_profiles_to_check(self):
        self.assertGreater(len(self.paths()), 10)

    def test_every_existing_profile_is_still_importable_python(self):
        for path in self.paths():
            with open(path, encoding="utf-8") as handle:
                ast.parse(handle.read(), filename=path)

    def test_every_existing_profile_still_imports(self):
        """The actual contract: config.py does importlib.import_module on
        these, and nothing in the hardening may change that."""
        import importlib
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        for path in self.paths():
            name = os.path.basename(path)[:-3]
            importlib.import_module(f"profiles.{name}")

    def test_the_hand_written_ones_are_deliberately_out_of_scope(self):
        """Several use imports and private locals. That is fine, and it is
        why check_module is on render()'s return rather than on the
        directory — pointing it at profiles/ would break them."""
        outside = []
        for path in self.paths():
            with open(path, encoding="utf-8") as handle:
                try:
                    make_profile.check_module(handle.read())
                except ValueError:
                    outside.append(os.path.basename(path))
        self.assertTrue(outside, "the distinction this test documents is real")


class TestProfileSchemaCompatibility(unittest.TestCase):
    """Old profiles must keep working, and a profile from the future must
    fail with a sentence rather than an AttributeError four frames into a
    paid run."""

    def stamped(self, value=None, **kw):
        import types
        return types.SimpleNamespace(**kw) if value is None else \
            types.SimpleNamespace(PROFILE_SCHEMA=value)

    def test_a_profile_written_before_the_field_existed_is_readable(self):
        """Absence IS the version, which is why nothing on disk needs
        migrating."""
        got = make_profile.profile_schema(self.stamped())
        self.assertTrue(got["readable"])
        self.assertEqual(got["version"], make_profile.LEGACY_SCHEMA)
        self.assertEqual(got["engine"], "v1")

    def test_every_profile_on_disk_is_readable(self):
        import glob
        import importlib
        for path in sorted(glob.glob(os.path.join(REPO_ROOT, "profiles",
                                                  "*.py"))):
            name = os.path.basename(path)[:-3]
            if name.startswith("_"):
                continue
            module = importlib.import_module(f"profiles.{name}")
            self.assertTrue(make_profile.profile_schema(module)["readable"],
                            path)

    def test_a_current_profile_is_readable(self):
        got = make_profile.profile_schema(
            self.stamped({"version": make_profile.PROFILE_SCHEMA,
                          "engine": "v2"}))
        self.assertTrue(got["readable"])
        self.assertEqual(got["engine"], "v2")

    def test_a_future_schema_is_refused_with_an_explicit_message(self):
        got = make_profile.profile_schema(
            self.stamped({"version": make_profile.SWEEP_SCHEMA + 1,
                          "engine": "v9"}))
        self.assertFalse(got["readable"])
        self.assertIn("newer build", got["why"])
        self.assertIn("regenerate", got["why"])

    def test_a_malformed_stamp_is_refused(self):
        for bad in ("hello", [1], None if False else 1.5):
            got = make_profile.profile_schema(self.stamped(bad))
            self.assertFalse(got["readable"], bad)

    def test_load_profile_raises_the_compatibility_message(self):
        import sys as _sys
        import types
        fake = types.ModuleType("profiles.futureprofile")
        fake.PROFILE_SCHEMA = {"version": 99, "engine": "v9"}
        _sys.modules["profiles.futureprofile"] = fake
        try:
            with self.assertRaises(ValueError) as caught:
                make_profile.load_profile("futureprofile")
            self.assertIn("newer build", str(caught.exception))
        finally:
            _sys.modules.pop("profiles.futureprofile", None)

    def test_load_profile_returns_a_real_profile(self):
        module, stamp = make_profile.load_profile("kartik_reachable")
        self.assertTrue(hasattr(module, "SEARCH"))
        self.assertTrue(stamp["readable"])
        self.assertEqual(stamp["engine"], "v1")   # written before the stamp

    def test_a_generated_profile_records_the_engine_that_wrote_it(self):
        import skill_concepts
        before = os.environ.get(skill_concepts.VERSION_ENV)
        try:
            for version in ("v1", "v2"):
                os.environ[skill_concepts.VERSION_ENV] = version
                source = render()
                # Parsed, not string-matched: the point is the VALUE, and
                # repr's choice of quote is not the contract.
                node = next(n for n in ast.parse(source).body
                            if isinstance(n, ast.Assign)
                            and n.targets[0].id == "PROFILE_SCHEMA")
                self.assertEqual(ast.literal_eval(node.value),
                                 {"version": make_profile.PROFILE_SCHEMA,
                                  "engine": version})
        finally:
            os.environ.pop(skill_concepts.VERSION_ENV, None)
            if before is not None:
                os.environ[skill_concepts.VERSION_ENV] = before

    def test_the_stamp_is_inert_to_config(self):
        """config._overlay only reads the names it knows, so the stamp
        cannot change how a profile behaves."""
        import config
        self.assertNotIn("PROFILE_SCHEMA", config.OVERLAYABLE)

    def test_the_stamp_is_a_literal_like_every_other_field(self):
        source = render()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.Assign)
                    and n.targets[0].id == "PROFILE_SCHEMA")
        self.assertIsInstance(ast.literal_eval(node.value), dict)


if __name__ == "__main__":
    unittest.main()

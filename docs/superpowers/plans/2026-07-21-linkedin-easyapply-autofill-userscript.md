# LinkedIn Easy Apply Autofill Userscript — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a Tampermonkey userscript that auto-fills LinkedIn Easy Apply forms from a résumé-grounded answer bank (never auto-submitting), plus a clickable HTML shortlist of the top LinkedIn jobs to apply to.

**Architecture:** Two Python entry points and one generated browser artifact. `build_userscript.py` bakes an answer bank + free-text templates + résumé text + Groq config into `userscript_template.js` (token injection) → gitignored `linkedin-easyapply.user.js`. `linkedin_shortlist.py` reuses `selection.py` to write `shortlist.html`. The userscript resolves each Easy Apply field through four tiers: keyword bank → templated slot-fill → Groq LLM → flag-for-human.

**Tech Stack:** Python 3.14 (stdlib only — no new deps), stdlib `unittest`, JavaScript (Tampermonkey userscript), Groq OpenAI-compatible REST (`llama-3.3-70b-versatile`).

## Global Constraints

- **No new Python dependencies.** Stdlib only; tests use stdlib `unittest` (no pytest).
- **Never fabricate.** Every filled value comes from `answers.yaml` / `ME` / résumé; anything else is flagged, never guessed. The LLM prompt must instruct "reply exactly `FLAG` if not truthfully answerable from the résumé."
- **Never auto-submit.** The userscript fills fields only; the user reviews and clicks Submit. It must never click a submit/next-and-submit control.
- **Secrets never committed.** The generated `.user.js` embeds the Groq key + résumé → gitignored. Key read from `.env` at generate time. Never print the key.
- **Paths resolve from repo root** via `apply_config.py` constants (which use `__file__`), so commands work from any working directory.
- **Run tests from repo root:** `python -m unittest discover -s auto-apply/tests -t auto-apply -v` (expect the existing ~39 to stay green).
- **Provider (verified live 2026-07-21):** Groq endpoint `https://api.groq.com/openai/v1/chat/completions`, model `llama-3.3-70b-versatile`.

## File Structure

- Create `auto-apply/build_userscript.py` — answer-bank builder + userscript renderer + generator (I/O).
- Create `auto-apply/userscript_template.js` — static userscript with `/*__TOKEN__*/` injection points.
- Create `auto-apply/linkedin_shortlist.py` — clickable HTML shortlist generator.
- Create `auto-apply/tests/test_build_userscript.py` — tests for `build_bank` + `render_userscript`.
- Create `auto-apply/tests/test_linkedin_shortlist.py` — tests for `render_html`.
- Modify `auto-apply/selection.py` — add `select_linkedin_candidates`.
- Modify `auto-apply/tests/test_selection.py` — add LinkedIn selector tests.
- Modify `auto-apply/apply_config.py` — add Phase 2 knobs.
- Modify `auto-apply/tests/test_apply_config.py` — assert new knobs.
- Modify `auto-apply/answers.yaml` — add `years_experience` fact.
- Modify `auto-apply/README.md` — add Phase 2 section.
- Modify `.gitignore` — ignore the generated `.user.js` and `shortlist.html`.

---

### Task 1: Config knobs, résumé fact, and gitignore

**Files:**
- Modify: `auto-apply/apply_config.py` (add Phase 2 section after the SMTP block, before `CHANNELS`)
- Modify: `auto-apply/answers.yaml`
- Modify: `.gitignore`
- Test: `auto-apply/tests/test_apply_config.py`

**Interfaces:**
- Produces: `cfg.USERSCRIPT_TEMPLATE`, `cfg.USERSCRIPT_OUT`, `cfg.SHORTLIST_OUT`, `cfg.GROQ_ENDPOINT`, `cfg.GROQ_MODEL` (all `str`); `answers.yaml` gains `years_experience: 2`.

- [ ] **Step 1: Write the failing test**

Add to `auto-apply/tests/test_apply_config.py` (inside the existing test class, or as a new `TestPhase2Config`):

```python
import os
import unittest
import apply_config as cfg


class TestPhase2Config(unittest.TestCase):
    def test_phase2_knobs_exist(self):
        self.assertTrue(cfg.USERSCRIPT_OUT.endswith("linkedin-easyapply.user.js"))
        self.assertTrue(cfg.SHORTLIST_OUT.endswith("shortlist.html"))
        self.assertTrue(cfg.USERSCRIPT_TEMPLATE.endswith("userscript_template.js"))
        self.assertEqual(cfg.GROQ_MODEL, "llama-3.3-70b-versatile")
        self.assertTrue(cfg.GROQ_ENDPOINT.startswith("https://api.groq.com/"))

    def test_paths_are_absolute(self):
        for p in (cfg.USERSCRIPT_OUT, cfg.SHORTLIST_OUT, cfg.USERSCRIPT_TEMPLATE):
            self.assertTrue(os.path.isabs(p))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k Phase2Config`
Expected: FAIL with `AttributeError: module 'apply_config' has no attribute 'USERSCRIPT_OUT'`

- [ ] **Step 3: Add the config knobs**

In `auto-apply/apply_config.py`, add after the `# --- SMTP (Gmail) ---` block:

```python
# --- Phase 2: LinkedIn Easy Apply autofill userscript ----------------------
# Generated .user.js embeds the Groq key + résumé text -> gitignored.
USERSCRIPT_TEMPLATE = os.path.join(AUTO_APPLY_DIR, "userscript_template.js")
USERSCRIPT_OUT = os.path.join(AUTO_APPLY_DIR, "linkedin-easyapply.user.js")
SHORTLIST_OUT = os.path.join(AUTO_APPLY_DIR, "shortlist.html")
# LLM fallback for novel free-text questions (Gemini key has zero quota; Groq
# free tier verified working 2026-07-21). Key comes from .env GROQ_API_KEY.
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
```

- [ ] **Step 4: Add the résumé fact to answers.yaml**

Append to `auto-apply/answers.yaml`:

```yaml
years_experience: 2
```

- [ ] **Step 5: Add gitignore entries**

Append to `.gitignore`:

```
# Phase 2: generated userscript (embeds Groq key + résumé) and shortlist
auto-apply/linkedin-easyapply.user.js
auto-apply/shortlist.html
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k Phase2Config`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add auto-apply/apply_config.py auto-apply/answers.yaml auto-apply/tests/test_apply_config.py .gitignore
git commit -m "feat(auto-apply): add Phase 2 config knobs, years_experience fact, gitignore"
```

---

### Task 2: LinkedIn candidate selector

**Files:**
- Modify: `auto-apply/selection.py` (add function after `select_candidates`)
- Test: `auto-apply/tests/test_selection.py` (add a test method)

**Interfaces:**
- Consumes: `_score`, `job_key` (existing in `selection.py`).
- Produces: `select_linkedin_candidates(rows, min_score, applied_keys, limit) -> list[dict]` — filters `source_site == "linkedin"` (case-insensitive), `score >= min_score`, `job_key not in applied_keys`; preserves input order; applies `limit` (None = all).

- [ ] **Step 1: Write the failing test**

Add to `auto-apply/tests/test_selection.py`. Note the existing `_row` helper hardcodes `naukri` as source_site; add a source-aware row helper:

```python
    def _row_src(self, score, title, company, url, source):
        return (f'{score},"node, react",True,{title},{company},India,False,'
                f'1-3 Yrs,,,,{source},{url},2026-07-20\n')

    def test_select_linkedin_filters_source_score_and_applied(self):
        rows = [
            {"score": "30", "source_site": "linkedin", "apply_url": "u_li1", "title": "FS", "company": "A"},
            {"score": "5",  "source_site": "linkedin", "apply_url": "u_li2", "title": "FS", "company": "B"},
            {"score": "40", "source_site": "naukri",   "apply_url": "u_nk1", "title": "FS", "company": "C"},
            {"score": "22", "source_site": "LinkedIn", "apply_url": "u_li3", "title": "FS", "company": "D"},
            {"score": "25", "source_site": "linkedin", "apply_url": "u_done", "title": "FS", "company": "E"},
        ]
        picked = selection.select_linkedin_candidates(
            rows, min_score=10, applied_keys={"u_done"}, limit=None)
        urls = [r["apply_url"] for r in picked]
        self.assertEqual(urls, ["u_li1", "u_li3"])  # li2 below score, nk1 wrong source, done already applied

    def test_select_linkedin_respects_limit(self):
        rows = [
            {"score": "30", "source_site": "linkedin", "apply_url": "a"},
            {"score": "20", "source_site": "linkedin", "apply_url": "b"},
        ]
        picked = selection.select_linkedin_candidates(rows, 10, set(), limit=1)
        self.assertEqual([r["apply_url"] for r in picked], ["a"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k linkedin`
Expected: FAIL with `AttributeError: module 'selection' has no attribute 'select_linkedin_candidates'`

- [ ] **Step 3: Implement the selector**

Add to `auto-apply/selection.py`:

```python
def select_linkedin_candidates(rows, min_score, applied_keys, limit):
    """Filter to LinkedIn jobs, above-threshold, not-yet-applied; keep order; apply limit."""
    picked = []
    for row in rows:
        if (row.get("source_site") or "").strip().lower() != "linkedin":
            continue
        if _score(row) < min_score:
            continue
        if job_key(row) in applied_keys:
            continue
        picked.append(row)
    if limit is not None:
        picked = picked[:limit]
    return picked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k linkedin`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add auto-apply/selection.py auto-apply/tests/test_selection.py
git commit -m "feat(auto-apply): add select_linkedin_candidates selector"
```

---

### Task 3: Answer-bank builder and userscript renderer (pure logic)

**Files:**
- Create: `auto-apply/build_userscript.py` (pure functions only in this task; `generate()` I/O added in Task 4)
- Test: `auto-apply/tests/test_build_userscript.py`

**Interfaces:**
- Produces:
  - `BANK_PATTERNS: list[tuple[list[str], str]]` — (keywords, answers.yaml key).
  - `FREE_TEXT: list[dict]` — `[{"keywords": [...], "template": "...{company}...{title}..."}]`.
  - `build_bank(answers: dict) -> list[dict]` — `[{"keywords": [...], "answer": "..."}]`; skips patterns whose value is missing/blank.
  - `render_userscript(template, bank, free_text, resume_text, groq_key, groq_model, groq_endpoint, me) -> str` — replaces every `/*__TOKEN__*/` with a `json.dumps`'d value.

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_build_userscript.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k build_userscript`
Expected: FAIL with `ModuleNotFoundError: No module named 'build_userscript'`

- [ ] **Step 3: Implement the pure functions**

Create `auto-apply/build_userscript.py`:

```python
"""
Generate the LinkedIn Easy Apply autofill userscript.

Bakes a résumé-grounded answer bank, free-text templates, the résumé text, and
Groq config into a Tampermonkey userscript by injecting JSON into
userscript_template.js. The output file contains the Groq API key and résumé
text, so it is gitignored. Regenerate after editing answers.yaml.

Run:  python auto-apply/build_userscript.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Seed keyword patterns -> the answers.yaml key that answers them. All lowercase
# keywords must appear in a question label to match. Values come only from
# answers.yaml -- nothing is invented; unmatched questions are flagged.
BANK_PATTERNS = [
    (["notice"], "notice_period"),
    (["expected", "ctc"], "expected_ctc"),
    (["salary"], "expected_ctc"),
    (["current", "location"], "current_location"),
    (["relocat"], "willing_to_relocate"),
    (["authoriz"], "work_authorization"),
    (["work", "permit"], "work_authorization"),
    (["years", "experience"], "years_experience"),
]

# Free-text templates: keywords -> template with {company}/{title} slots filled
# from the page at runtime. Grounded, user-editable prose.
FREE_TEXT = [
    {
        "keywords": ["why"],
        "template": (
            "I'm excited about the {title} role at {company} - it fits my ~2 years "
            "of full-stack experience with React, React Native, TypeScript, Node.js, "
            "Express and MongoDB."
        ),
    },
]


def build_bank(answers):
    """Seed patterns + answers.yaml values -> [{keywords, answer}]. Skip any
    pattern whose value is missing/blank (never emit a blank/fabricated answer)."""
    bank = []
    for keywords, key in BANK_PATTERNS:
        value = (answers.get(key) or "").strip()
        if value:
            bank.append({"keywords": keywords, "answer": value})
    return bank


def render_userscript(template, bank, free_text, resume_text,
                      groq_key, groq_model, groq_endpoint, me):
    """Replace each /*__TOKEN__*/ with a json.dumps'd value (valid JS literals)."""
    replacements = {
        "/*__BANK__*/": json.dumps(bank),
        "/*__FREE_TEXT__*/": json.dumps(free_text),
        "/*__RESUME__*/": json.dumps(resume_text),
        "/*__GROQ_KEY__*/": json.dumps(groq_key),
        "/*__GROQ_MODEL__*/": json.dumps(groq_model),
        "/*__GROQ_ENDPOINT__*/": json.dumps(groq_endpoint),
        "/*__ME__*/": json.dumps(me),
    }
    out = template
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k build_userscript`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add auto-apply/build_userscript.py auto-apply/tests/test_build_userscript.py
git commit -m "feat(auto-apply): answer-bank builder and userscript renderer"
```

---

### Task 4: Userscript template and generator wiring

**Files:**
- Create: `auto-apply/userscript_template.js`
- Modify: `auto-apply/build_userscript.py` (add `_read_env_key`, `generate`, `__main__`)

**Interfaces:**
- Consumes: `build_bank`, `render_userscript`, `BANK_PATTERNS`, `FREE_TEXT` (Task 3); `apply_config` constants (Task 1); `answers.load_answers`, `resume_parser.load_resume`.
- Produces: `generate() -> str` (path written); running the module writes `cfg.USERSCRIPT_OUT`.

- [ ] **Step 1: Create the userscript template**

Create `auto-apply/userscript_template.js`. The `/*__TOKEN__*/` markers are replaced at generate time. Tiers: keyword bank → templated slot-fill → Groq → flag. Never clicks Submit.

```javascript
// ==UserScript==
// @name         LinkedIn Easy Apply Autofill (grounded)
// @namespace    job-scraper.auto-apply
// @match        https://www.linkedin.com/jobs/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      api.groq.com
// @version      1.0
// @description  Fills the Easy Apply modal from a résumé-grounded answer bank. Never submits.
// ==/UserScript==
(function () {
  "use strict";

  const BANK = /*__BANK__*/;            // [{keywords:[...], answer:"..."}]
  const FREE_TEXT = /*__FREE_TEXT__*/;  // [{keywords:[...], template:"...{company}...{title}..."}]
  const RESUME_TEXT = /*__RESUME__*/;   // string
  const GROQ_KEY = /*__GROQ_KEY__*/;    // "" disables tier 3
  const GROQ_MODEL = /*__GROQ_MODEL__*/;
  const GROQ_ENDPOINT = /*__GROQ_ENDPOINT__*/;
  const ME = /*__ME__*/;

  const norm = (s) => (s || "").toLowerCase();

  function bankAnswer(label) {
    const l = norm(label);
    for (const e of BANK) if (e.keywords.every((k) => l.includes(k))) return e.answer;
    return null;
  }

  function freeTextAnswer(label, ctx) {
    const l = norm(label);
    for (const t of FREE_TEXT) {
      if (t.keywords.every((k) => l.includes(k))) {
        return t.template.replace(/{company}/g, ctx.company).replace(/{title}/g, ctx.title);
      }
    }
    return null;
  }

  // React-controlled inputs need the native setter + input/change events.
  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype
      : el.tagName === "SELECT" ? window.HTMLSelectElement.prototype
      : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function flag(el) {
    el.style.outline = "2px solid #e11";
    if (el.dataset.aaFlagged) return;
    el.dataset.aaFlagged = "1";
    const note = document.createElement("div");
    note.textContent = "⚠ answer me";
    note.style.cssText = "color:#e11;font-size:12px;font-weight:600;";
    if (el.parentElement) el.parentElement.appendChild(note);
  }

  function labelFor(el) {
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return lab.innerText;
    }
    const group = el.closest(
      "[data-test-form-element], .fb-dash-form-element, fieldset, .jobs-easy-apply-form-element"
    ) || el.parentElement;
    if (group) {
      const lab = group.querySelector("label, legend");
      if (lab) return lab.innerText;
      return group.innerText;
    }
    return el.getAttribute("aria-label") || el.name || "";
  }

  function jobContext() {
    const q = (sel) => (document.querySelector(sel)?.innerText || "").trim();
    return {
      title: q(".job-details-jobs-unified-top-card__job-title") || q("h1"),
      company: q(".job-details-jobs-unified-top-card__company-name"),
      jd: q("#job-details") || q(".jobs-description__content"),
    };
  }

  function groqAnswer(question, ctx) {
    return new Promise((resolve) => {
      if (!GROQ_KEY) return resolve(null);
      const prompt =
        "You are filling a job application field for the candidate below. Answer the " +
        "question in 1-2 sentences using ONLY facts present in the resume. Do not invent " +
        "skills, years, employers, or numbers. If it cannot be answered truthfully from " +
        "the resume, reply with exactly: FLAG\n\nRESUME:\n" + RESUME_TEXT +
        "\n\nJOB:\n" + (ctx.jd || "").slice(0, 2000) +
        "\n\nQUESTION: " + question + "\nANSWER:";
      GM_xmlhttpRequest({
        method: "POST",
        url: GROQ_ENDPOINT,
        headers: { Authorization: "Bearer " + GROQ_KEY, "Content-Type": "application/json" },
        data: JSON.stringify({
          model: GROQ_MODEL,
          messages: [{ role: "user", content: prompt }],
          max_tokens: 200,
          temperature: 0.2,
        }),
        onload: (r) => {
          try {
            const txt = JSON.parse(r.responseText).choices[0].message.content.trim();
            resolve(txt === "FLAG" ? null : txt);
          } catch (e) { resolve(null); }
        },
        onerror: () => resolve(null),
      });
    });
  }

  function applyAnswer(el, value) {
    if (el.tagName === "SELECT") {
      const opt = Array.from(el.options).find(
        (o) => norm(o.text).includes(norm(value)) || norm(value).includes(norm(o.text))
      );
      if (opt) setValue(el, opt.value); else flag(el);
      return;
    }
    if (el.type === "radio" || el.type === "checkbox") {
      const lab = labelFor(el);
      const wantYes = /^(yes|true)$/i.test(value);
      if (norm(lab).includes(norm(value)) || (wantYes && norm(lab).includes("yes"))) el.click();
      return;
    }
    setValue(el, value);
  }

  async function fillField(el, ctx) {
    if (el.type === "file" || el.type === "hidden" || el.disabled) return;
    const label = labelFor(el);
    const bank = bankAnswer(label);
    if (bank !== null) return applyAnswer(el, bank);        // tier 1
    if (el.tagName === "TEXTAREA") {
      const tmpl = freeTextAnswer(label, ctx);              // tier 2
      if (tmpl !== null) return applyAnswer(el, tmpl);
      const llm = await groqAnswer(label, ctx);             // tier 3
      if (llm !== null) return applyAnswer(el, llm);
    }
    flag(el);                                               // tier 4
  }

  async function autofill() {
    const modal = document.querySelector(".jobs-easy-apply-modal, [data-test-modal]") || document;
    const ctx = jobContext();
    for (const el of modal.querySelectorAll("input, select, textarea")) {
      await fillField(el, ctx);
    }
    // Intentionally never clicks Submit/Next — the user reviews and submits.
  }

  function addButton() {
    if (document.getElementById("aa-autofill-btn")) return;
    const btn = document.createElement("button");
    btn.id = "aa-autofill-btn";
    btn.textContent = "⚡ Autofill";
    btn.style.cssText =
      "position:fixed;bottom:20px;right:20px;z-index:99999;padding:10px 14px;" +
      "background:#0a66c2;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;";
    btn.onclick = autofill;
    document.body.appendChild(btn);
  }

  new MutationObserver(addButton).observe(document.body, { childList: true, subtree: true });
  addButton();
})();
```

- [ ] **Step 2: Add the generator wiring to build_userscript.py**

Append to `auto-apply/build_userscript.py` (imports at top; functions + `__main__` at bottom):

```python
import apply_config as cfg
from answers import load_answers
from resume_parser import load_resume


def _read_env_key(name):
    """Read a KEY=value from the repo-root .env (secrets are not in apply_config)."""
    env_path = os.path.join(cfg.REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def generate():
    answers = load_answers(cfg.ANSWERS_FILE)
    resume_text = load_resume(cfg.RESUME_PDF, cfg.RESUME_TXT)
    groq_key = os.environ.get("GROQ_API_KEY") or _read_env_key("GROQ_API_KEY")
    if not groq_key:
        print("WARNING: GROQ_API_KEY not set — LLM fallback (tier 3) disabled.")
    with open(cfg.USERSCRIPT_TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()
    out = render_userscript(
        template, build_bank(answers), FREE_TEXT, resume_text,
        groq_key, cfg.GROQ_MODEL, cfg.GROQ_ENDPOINT, cfg.ME,
    )
    with open(cfg.USERSCRIPT_OUT, "w", encoding="utf-8") as f:
        f.write(out)
    return cfg.USERSCRIPT_OUT


if __name__ == "__main__":
    print("Wrote " + generate())
```

- [ ] **Step 3: Run the generator**

Run: `.venv/bin/python auto-apply/build_userscript.py`
Expected: `Wrote /Users/.../auto-apply/linkedin-easyapply.user.js` (no crash; a WARNING line only if the key is missing).

- [ ] **Step 4: Verify the generated output (no leftover tokens, values embedded)**

Run: `grep -c '/\*__' auto-apply/linkedin-easyapply.user.js; grep -c 'llama-3.3-70b-versatile' auto-apply/linkedin-easyapply.user.js`
Expected: first count `0` (all tokens replaced), second count `>= 1` (model embedded).

- [ ] **Step 5: Verify the generated file is gitignored**

Run: `git check-ignore auto-apply/linkedin-easyapply.user.js`
Expected: prints the path (confirms it is ignored — will not be committed).

- [ ] **Step 6: Run the full test suite**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: all pass (existing ~39 + new).

- [ ] **Step 7: Commit**

```bash
git add auto-apply/userscript_template.js auto-apply/build_userscript.py
git commit -m "feat(auto-apply): userscript template + generator (never auto-submits)"
```

---

### Task 5: Clickable HTML shortlist generator

**Files:**
- Create: `auto-apply/linkedin_shortlist.py`
- Test: `auto-apply/tests/test_linkedin_shortlist.py`

**Interfaces:**
- Consumes: `selection.load_jobs`, `selection.load_applied_keys`, `selection.select_linkedin_candidates` (Task 2); `apply_config` (Task 1).
- Produces: `render_html(jobs: list[dict]) -> str`; `generate() -> tuple[str, int]` (path, count).

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_linkedin_shortlist.py`:

```python
import unittest
import linkedin_shortlist as ls


class TestRenderHtml(unittest.TestCase):
    def test_renders_clickable_anchors(self):
        jobs = [{"score": "30", "title": "Full Stack Dev", "company": "Acme",
                 "location": "Delhi", "apply_url": "https://linkedin.com/jobs/1"}]
        out = ls.render_html(jobs)
        self.assertIn('href="https://linkedin.com/jobs/1"', out)
        self.assertIn('target="_blank"', out)
        self.assertIn("Full Stack Dev", out)
        self.assertIn("Acme", out)

    def test_escapes_html_in_fields(self):
        jobs = [{"score": "10", "title": "Dev <script>", "company": "A&B",
                 "location": "X", "apply_url": "u"}]
        out = ls.render_html(jobs)
        self.assertNotIn("<script>", out)      # escaped
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("A&amp;B", out)

    def test_empty_shows_message(self):
        out = ls.render_html([])
        self.assertIn("No LinkedIn jobs", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k shortlist`
Expected: FAIL with `ModuleNotFoundError: No module named 'linkedin_shortlist'`

- [ ] **Step 3: Implement the shortlist generator**

Create `auto-apply/linkedin_shortlist.py`:

```python
"""
Write a clickable HTML shortlist of the top LinkedIn jobs to apply to.

Reuses selection.py to filter the latest ranked CSV to LinkedIn postings above
MIN_SCORE that aren't already in applications.csv, sorted by the CSV's order
(already score-desc). Open with `open auto-apply/shortlist.html` and click jobs
top-down.

Run:  python auto-apply/linkedin_shortlist.py
"""

import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
from selection import load_jobs, load_applied_keys, select_linkedin_candidates


def render_html(jobs):
    items = []
    for j in jobs:
        url = html.escape(j.get("apply_url", ""), quote=True)
        title = html.escape(j.get("title", ""))
        company = html.escape(j.get("company", ""))
        score = html.escape(j.get("score", ""))
        location = html.escape(j.get("location", ""))
        items.append(
            '<li><a href="' + url + '" target="_blank" rel="noopener">'
            "<b>" + score + "</b> — " + title + " @ " + company +
            " <small>(" + location + ")</small></a></li>"
        )
    body = "\n".join(items) or "<li>No LinkedIn jobs above threshold.</li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>LinkedIn shortlist</title>"
        "<h1>LinkedIn jobs to apply to</h1>"
        "<ol>" + body + "</ol>"
    )


def generate():
    csv_path = cfg.latest_input_csv()
    if not csv_path:
        raise SystemExit("No input CSV in output/. Run the scraper or copy a CSV first.")
    rows = load_jobs(csv_path)
    applied = load_applied_keys(cfg.APPLICATIONS_LOG)
    jobs = select_linkedin_candidates(rows, cfg.MIN_SCORE, applied, limit=None)
    with open(cfg.SHORTLIST_OUT, "w", encoding="utf-8") as f:
        f.write(render_html(jobs))
    return cfg.SHORTLIST_OUT, len(jobs)


if __name__ == "__main__":
    path, n = generate()
    print("Wrote " + path + " (" + str(n) + " jobs)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v -k shortlist`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add auto-apply/linkedin_shortlist.py auto-apply/tests/test_linkedin_shortlist.py
git commit -m "feat(auto-apply): clickable LinkedIn shortlist HTML generator"
```

---

### Task 6: README Phase 2 section and full-suite verification

**Files:**
- Modify: `auto-apply/README.md`

**Interfaces:** none (docs + verification).

- [ ] **Step 1: Append the Phase 2 section to the README**

Add to `auto-apply/README.md`:

```markdown
## Phase 2 — LinkedIn Easy Apply autofill (userscript)

Auto-fills the LinkedIn Easy Apply modal from a résumé-grounded answer bank.
**It never submits — you review and click Submit.** No new Python deps.

### Setup (once)
1. Ensure `GROQ_API_KEY=gsk_...` is in the repo-root `.env` (free key from
   https://console.groq.com — powers the LLM fallback for novel free-text
   questions). Without it, tiers 1–2 still work and unknowns are flagged.
2. Install the **Tampermonkey** browser extension.
3. Generate the userscript:
   ```
   python auto-apply/build_userscript.py
   ```
   This writes `auto-apply/linkedin-easyapply.user.js` (gitignored — it embeds
   your key + résumé). Open that file in Tampermonkey (dashboard → Utilities →
   Import, or drag it in) to install.

### Each session
1. Build the shortlist of top LinkedIn jobs not yet applied to:
   ```
   python auto-apply/linkedin_shortlist.py
   open auto-apply/shortlist.html
   ```
2. Click a job → LinkedIn opens → click **Easy Apply**.
3. Click the floating **⚡ Autofill** button. Fields fill in four tiers:
   keyword bank → templated free-text → Groq LLM → **⚠ flagged** (red outline)
   for anything not truthfully answerable.
4. **Review every field**, fix any flagged ones, then click **Submit yourself**.

### After editing answers
Edit `answers.yaml` (facts) or `build_userscript.py` (`BANK_PATTERNS` /
`FREE_TEXT`), then re-run `python auto-apply/build_userscript.py` and re-import
into Tampermonkey.

### Notes
- Dedup: the shortlist excludes jobs already in `applications.csv`; LinkedIn also
  badges applied jobs "Applied ✓".
- Grounding: answers come only from `answers.yaml` / `ME` / résumé; the LLM is
  told to reply `FLAG` (→ flagged) rather than invent anything.
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: all pass (existing ~39 + the new build_userscript/selection/shortlist/config tests).

- [ ] **Step 3: Commit**

```bash
git add auto-apply/README.md
git commit -m "docs(auto-apply): Phase 2 LinkedIn autofill README"
```

---

## Self-Review

**Spec coverage:**
- Answer bank (keyword patterns + free_text templates, grounded) → Task 3 (`BANK_PATTERNS`, `FREE_TEXT`, `build_bank`); Task 1 adds `years_experience`. *Refinement vs. spec:* patterns/templates live in `build_userscript.py` (code, unit-tested) rather than as new `answers.yaml` sections — `answers.yaml` stays the flat facts store, so `answers.py` needs no parser change (the spec left the sub-schema to the plan and permitted extending `answers.py` "if needed" — it isn't).
- Generator baking bank + résumé + key + prompt into gitignored `.user.js` → Tasks 3–4 + Task 1 gitignore.
- Userscript runtime with 4 fill tiers, no-submit, flag unknowns → Task 4 template.
- Groq client-side via `GM_xmlhttpRequest`, `FLAG` grounding → Task 4 `groqAnswer`.
- Clickable shortlist → Task 5.
- Config knobs → Task 1. Tracking (LinkedIn badge + shortlist exclusion) → Task 5 + README. Security (gitignore, key from .env, no print) → Tasks 1, 4. Testing → Tasks 2,3,5 unit; Task 4 command checks; README documents manual DOM verification.

*Deferred per spec (not in this plan):* GM-storage "export applied" button and browser→CSV write-back (spec marks these as later/`ponytail:` upgrades). The primary dedup — shortlist exclusion + LinkedIn's native badge — is implemented.

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output.

**Type consistency:** `select_linkedin_candidates(rows, min_score, applied_keys, limit)` used identically in Task 2 and Task 5. `build_bank(answers)->[{keywords,answer}]` and `render_userscript(...)` signatures match between Task 3 definition and Task 4 usage. `render_html(jobs)` / `generate()` consistent in Task 5. Template tokens (`/*__BANK__*/` etc.) match exactly between Task 4 template and Task 3 `render_userscript` replacements.

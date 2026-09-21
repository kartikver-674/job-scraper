#!/usr/bin/env python3
"""Drive the Search-preferences screen in a REAL browser.

The Python suites render the page and assert on its markup. That cannot see
what a browser does with an Alpine binding, and three bugs in a row lived
exactly there:

  * a submit button that disabled itself in its own click handler, so the
    form never submitted (84b0d27's parent);
  * @click.prevent on a checkbox, which undid the tick the browser had
    already applied, so every box showed the state from before its own click;
  * $dispatch("change") called from a checkbox's own @change handler, which
    re-entered that handler — 3,001 events from one click, and a frozen page.

None of those produce a Python failure. This does.

    python tools/ui_smoke.py            # both scopes
    python tools/ui_smoke.py --scope india

Needs Google Chrome (or set SWEEP_CHROME) and puppeteer-core. It is not part
of any suite and nothing imports it — it is the manual check, made runnable.
Exits non-zero on the first thing that is wrong.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("SWEEP_CHROME") or (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# What each scope's menu should hold: the all-locations row plus its options.
EXPECTED = {"india": 8, "global": 29}

DRIVER = r"""
const puppeteer = require("puppeteer-core");
const [page_url, chrome, scope, rows] = process.argv.slice(2);
const T = (p, ms, what) => Promise.race([p,
  new Promise((_, r) => setTimeout(() => r(new Error("MAIN THREAD BLOCKED: " + what)), ms))]);
const fail = (m) => { console.log("FAIL " + m); process.exit(1); };

(async () => {
  const browser = await puppeteer.launch({ executablePath: chrome, headless: "new",
    args: ["--allow-file-access-from-files", "--no-sandbox"] });
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push(String(e.message).slice(0, 200)));
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(page_url, { waitUntil: "networkidle0" });
  await new Promise(r => setTimeout(r, 400));
  // Count every event the page dispatches, so a storm is a number.
  await page.evaluate(() => {
    window.__n = 0; const o = EventTarget.prototype.dispatchEvent;
    EventTarget.prototype.dispatchEvent = function (e) {
      if (e.type === "change") window.__n++; return o.call(this, e); };
  });

  await page.click(".picker-toggle");
  await new Promise(r => setTimeout(r, 250));
  const got = await page.$$eval("#location-menu label.choice", n => n.length);
  if (got !== Number(rows))
    fail(`${scope}: menu rendered ${got} rows, expected ${rows} ` +
         `(a truncated x-data renders only the all-locations row)`);

  const names = await page.$$eval("#location-menu label.choice",
    ns => ns.slice(1).map(l => l.textContent.trim()));
  for (const name of names.slice(0, 3)) {
    const before = await page.evaluate(() => window.__n);
    await T(page.evaluate((n) => {
      [...document.querySelectorAll("#location-menu label.choice")]
        .find(l => l.textContent.trim() === n).querySelector("input").click();
    }, name), 6000, `clicking ${name}`);
    await new Promise(r => setTimeout(r, 150));
    const st = await T(page.evaluate((n) => {
      const rows = [...document.querySelectorAll("#location-menu label.choice")];
      const chips = [...document.querySelectorAll(".tag.pick span:first-child")]
        .map(s => s.textContent);
      return {
        ticked: rows.find(l => l.textContent.trim() === n).querySelector("input").checked,
        allTicked: rows[0].querySelector("input").checked,
        // every tick agrees with the chips, the count and the posted value
        inSync: rows.slice(1).every(l =>
          l.querySelector("input").checked === chips.includes(l.textContent.trim())),
        chips, n: window.__n,
        hidden: document.querySelector('input[name="locations"]').value,
      };
    }, name), 6000, `reading after ${name}`);
    const fired = st.n - before;
    if (!st.ticked) fail(`${scope}: ${name} did not tick on its own click`);
    if (st.allTicked) fail(`${scope}: the all-locations row stayed ticked`);
    if (!st.inSync) fail(`${scope}: a checkbox disagrees with the chips`);
    if (st.hidden !== st.chips.join(", "))
      fail(`${scope}: posted value ${JSON.stringify(st.hidden)} != chips ` +
           JSON.stringify(st.chips.join(", ")));
    if (fired !== 1)
      fail(`${scope}: one click fired ${fired} change events, expected 1 ` +
           `(an element dispatching an event it also listens for)`);
    console.log(`  ok  ${name.padEnd(22)} tick, chips, count and value agree; ` +
                `${fired} change event`);
  }
  // The all row clears everything and cannot be unticked directly.
  await T(page.evaluate(() =>
    document.querySelector("#location-menu label.choice input").click()),
    6000, "the all-locations row");
  await new Promise(r => setTimeout(r, 150));
  const end = await T(page.evaluate(() => ({
    allTicked: document.querySelector("#location-menu label.choice input").checked,
    others: [...document.querySelectorAll("#location-menu label.choice")].slice(1)
      .filter(l => l.querySelector("input").checked).length,
    hidden: document.querySelector('input[name="locations"]').value,
  })), 6000, "reading after the all row");
  if (!end.allTicked || end.others || end.hidden !== "")
    fail(`${scope}: the all-locations row left ${JSON.stringify(end)}`);
  console.log("  ok  all-locations row    clears every pick and stays ticked");
  if (errs.length) fail(`${scope}: page errors ${JSON.stringify(errs)}`);
  await browser.close();
})().catch(e => { console.log("FAIL " + e.message); process.exit(1); });
"""


def page_html(scope, out_dir):
    """The real /configure, with its assets pointed at the checkout."""
    sys.path[:0] = [REPO, os.path.join(REPO, "auto-apply")]
    from sweep.tests.test_search_prefs import form, make_app

    app = make_app(free=True)
    app.test_client().post("/configure", data=form(scope=scope))
    html = app.test_client().get("/configure").get_data(as_text=True)
    html = (html.replace('href="/static/', f'href="file://{REPO}/sweep/static/')
                .replace('src="/static/', f'src="file://{REPO}/sweep/static/'))
    html = re.sub(r'<link (href="https://fonts|rel="preconnect")[^>]*>', "", html)
    path = os.path.join(out_dir, f"{scope}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def node_home(out_dir):
    """A place with puppeteer-core in it, or a sentence saying how to get one."""
    for candidate in (os.environ.get("SWEEP_NODE_MODULES"), out_dir, REPO):
        if candidate and os.path.isdir(os.path.join(candidate, "node_modules",
                                                    "puppeteer-core")):
            return candidate
    sys.exit(
        "puppeteer-core is not installed, and it is deliberately not a\n"
        "dependency of this repo — nothing in the suites needs it.\n\n"
        "    mkdir -p /tmp/sweep-ui && cd /tmp/sweep-ui && npm init -y\n"
        "    npm install puppeteer-core\n"
        "    SWEEP_NODE_MODULES=/tmp/sweep-ui python tools/ui_smoke.py\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", choices=sorted(EXPECTED), action="append",
                    help="default: every scope with a picker")
    args = ap.parse_args()
    if not shutil.which(CHROME) and not os.path.exists(CHROME):
        sys.exit(f"no Chrome at {CHROME} — set SWEEP_CHROME")
    if not shutil.which("node"):
        sys.exit("node is not on PATH")

    out_dir = tempfile.mkdtemp(prefix="sweep-ui-")
    home = node_home(out_dir)
    driver = os.path.join(out_dir, "driver.js")
    with open(driver, "w", encoding="utf-8") as fh:
        fh.write(DRIVER)

    failed = 0
    for scope in (args.scope or sorted(EXPECTED)):
        print(f"\n{scope}: the location picker")
        # NODE_PATH, not cwd: require() resolves from the SCRIPT's directory,
        # and the script lives in a temp dir on purpose — this tool must not
        # write anything into the checkout.
        result = subprocess.run(
            ["node", driver, "file://" + page_html(scope, out_dir), CHROME,
             scope, str(EXPECTED[scope])],
            env={**os.environ, "NODE_PATH": os.path.join(home, "node_modules")},
            capture_output=True, text=True, timeout=180)
        print(result.stdout.rstrip() or result.stderr.rstrip())
        failed += result.returncode != 0
    print()
    if failed:
        sys.exit(f"{failed} scope(s) FAILED")
    print("ui smoke ok")


if __name__ == "__main__":
    main()

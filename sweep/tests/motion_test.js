/* The counter, driven by a fake clock, so the assertions are deterministic. */
const fs = require("fs");

function load(reduced) {
  /* Stable ids into a Map. The first version of this handed out indexes
     into an array it replaced on every tick, so cancelAnimationFrame was a
     no-op and the overwrite assertion below could not fail — which a
     mutation proved by surviving. `ran` counts callbacks so a cancelled
     loop that is still queued is visible. */
  const queue = new Map();
  let nextId = 1, ran = 0;
  const win = {
    matchMedia: () => ({ matches: reduced }),
    requestAnimationFrame: (fn) => { queue.set(nextId, fn); return nextId++; },
    cancelAnimationFrame: (id) => { queue.delete(id); },
  };
  global.window = win;
  global.requestAnimationFrame = win.requestAnimationFrame;
  global.cancelAnimationFrame = win.cancelAnimationFrame;
  eval(fs.readFileSync(__dirname + "/../static/motion.js", "utf8"));
  const tick = (t) => {
    const due = [...queue.values()];
    queue.clear();
    due.forEach((f) => { ran++; f(t); });
  };
  return { count: win.sweepCount, tick, frames: () => ran };
}

let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "  ok   " : "  FAIL ") + msg); if (!cond) fails++; };

// --- normal motion --------------------------------------------------------
let { count, tick, frames } = load(false);
const el = { textContent: "" };

count(el, 2.70);
ok(el.textContent === "$2.70", "first call sets the figure outright (no entrance animation)");

count(el, 0.90);
ok(el.textContent === "$2.70", "a change does not jump; it starts from where it was");
tick(0);            // first frame establishes the start time
tick(175);          // half of 350ms
const mid = parseFloat(el.textContent.slice(1));
ok(mid < 2.70 && mid > 0.90, `midway is between the two figures (${el.textContent})`);
tick(350);
ok(el.textContent === "$0.90", "lands exactly on the target, not on the easing's last frame");

// --- overwrite, not queue -------------------------------------------------
count(el, 5.00); tick(0); tick(50);
const interrupted = el.textContent;
count(el, 1.11);                       // interrupt mid-tween
const before = frames();
tick(0);
ok(frames() - before === 1,
   `the interrupted tween is cancelled, not left queued beside the new one `
   + `(${frames() - before} callbacks ran)`);
tick(350);
ok(el.textContent === "$1.11", `a second change overwrites the first (was ${interrupted})`);

count(el, 2.00); count(el, 3.00); count(el, 4.44);
tick(0); tick(350);
ok(el.textContent === "$4.44", "three rapid changes land on the last, not in sequence");

// --- guards ---------------------------------------------------------------
count(el, NaN);
ok(el.textContent === "$4.44", "a non-finite figure is ignored rather than rendered");
count(el, 4.44);
ok(el.textContent === "$4.44", "setting the same figure is a no-op");

// --- reduced motion -------------------------------------------------------
({ count, tick, frames } = load(true));
const r = { textContent: "" };
count(r, 2.70);
count(r, 0.90);
ok(r.textContent === "$0.90", "reduced motion sets the end state directly, no tween");

console.log(fails ? `\n${fails} FAILED` : "\ncounter: all assertions hold");
process.exit(fails ? 1 : 0);

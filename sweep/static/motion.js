/* The one beat of the motion spec CSS cannot do: a number tweening to a
   new number.
 *
 * docs/stitch-ui-prompt.md asks for GSAP. Everything else it describes is a
 * keyframe and a class and lives in sweep.css; this is the counter, and it
 * is small enough that vendoring an animation library for it would be the
 * larger change.
 *
 * Used by the Configure screen, where every toggle re-prices the sweep: the
 * fill tweens to its new width while the figure above it counts.
 */
(function () {
  "use strict";

  /* Read once. The spec's reduced-motion branch sets end states directly
     and leaves the counter as plain text. */
  var REDUCED = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var DURATION = 350;

  function money(n) {
    return "$" + n.toFixed(2);
  }

  /* Ease out cubic: fast off the mark, settling rather than stopping. */
  function ease(k) {
    return 1 - Math.pow(1 - k, 3);
  }

  /* Count `el` to `value`.
   *
   * The previous frame loop is cancelled rather than queued behind — this
   * is what the spec means by overwrite: 'auto'. Toggling four sites off in
   * a second must land on the fourth figure, not play four tweens in turn.
   *
   * The FIRST call sets the text outright. Alpine runs this on init, and a
   * figure that counts up from nothing on every page load is an entrance
   * animation, which the spec rules out.
   */
  window.sweepCount = function (el, value) {
    if (typeof value !== "number" || !isFinite(value)) {
      return;
    }
    var from = el._sweepValue;
    el._sweepValue = value;

    if (from === undefined || REDUCED || from === value) {
      el.textContent = money(value);
      return;
    }
    if (el._sweepFrame) {
      cancelAnimationFrame(el._sweepFrame);
    }
    var started = null;
    var delta = value - from;

    function step(now) {
      if (started === null) {
        started = now;
      }
      var k = Math.min(1, (now - started) / DURATION);
      el.textContent = money(from + delta * ease(k));
      if (k < 1) {
        el._sweepFrame = requestAnimationFrame(step);
      } else {
        /* No final assignment: k is clamped to 1, ease(1) is exactly 1, so
           the last frame has already written money(value). A mutation
           proved the line changed nothing. */
        el._sweepFrame = null;
      }
    }
    el._sweepFrame = requestAnimationFrame(step);
  };
})();

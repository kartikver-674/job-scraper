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
  const DEBUG = true;  // set false to silence the [autofill] console logs
  const log = (...a) => { if (DEBUG) console.log("[autofill]", ...a); };

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

  function txt(node) { return node && node.innerText ? node.innerText.trim() : ""; }

  function labelFor(el) {
    // 1. aria-labelledby -> resolve referenced nodes
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean)
        .map(txt).join(" ").trim();
      if (t) return t;
    }
    // 2. <label for=id>
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (txt(lab)) return txt(lab);
    }
    // 3. known form-element group classes
    const group = el.closest(
      "[data-test-form-element], .fb-dash-form-element, .jobs-easy-apply-form-element, fieldset"
    );
    if (group) {
      const lab = group.querySelector("label, legend");
      if (txt(lab)) return txt(lab);
    }
    // 4. walk a few ancestors looking for a single label (LinkedIn wraps each
    //    field in a plain <div><label>…</label><input></div> with no stable class)
    let g = el.parentElement;
    for (let i = 0; i < 4 && g; i++, g = g.parentElement) {
      const labs = g.querySelectorAll("label, legend");
      if (labs.length === 1 && txt(labs[0])) return txt(labs[0]);
    }
    return el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.name || "";
  }

  // LinkedIn's Easy Apply modal is a native <dialog> (obfuscated classes, no
  // role=dialog). Target the open dialog — the button must be a child of it since
  // showModal() makes everything outside the dialog inert (focus trap / top layer).
  function findModal() {
    return document.querySelector("dialog[open]") || document.querySelector("dialog");
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
            resolve(/\bflag\b/i.test(txt) ? null : txt);
          } catch (e) { resolve(null); }
        },
        onerror: () => resolve(null),
      });
    });
  }

  function applyAnswer(el, value) {
    if (el.tagName === "SELECT") {
      const opt = Array.from(el.options).find((o) => {
        const t = norm(o.text).trim();
        return t && (t.includes(norm(value)) || norm(value).includes(t));
      });
      if (opt) setValue(el, opt.value); else flag(el);
      return;
    }
    if (el.type === "radio" || el.type === "checkbox") {
      const lab = labelFor(el);
      const wantYes = /^(yes|true)$/i.test(value);
      if (norm(lab).includes(norm(value)) || (wantYes && norm(lab).includes("yes"))) {
        el.click();
      } else {
        flag(el);
      }
      return;
    }
    setValue(el, value);
  }

  function alreadyFilled(el) {
    if (el.tagName === "INPUT" && (el.type === "radio" || el.type === "checkbox")) return false;
    if (el.tagName === "SELECT") {
      const t = (el.options[el.selectedIndex] || {}).text || "";
      return !!el.value && !/^\s*(select|choose)\b/i.test(t);  // real option chosen
    }
    return !!(el.value && el.value.trim());  // text/tel/textarea already has a value
  }

  async function fillField(el, ctx) {
    if (el.type === "file" || el.type === "hidden" || el.disabled) return;
    if (alreadyFilled(el)) { log("skip filled", el.tagName); return; }  // leave LinkedIn's prefills alone
    const label = labelFor(el);
    const desc = el.tagName + "[type=" + (el.type || "-") + "] label=" + JSON.stringify((label || "").slice(0, 70));
    const bank = bankAnswer(label);
    if (bank !== null) { log("tier1 BANK  ", desc, "->", bank); return applyAnswer(el, bank); }
    if (el.tagName === "TEXTAREA") {
      const tmpl = freeTextAnswer(label, ctx);              // tier 2
      if (tmpl !== null) { log("tier2 TMPL  ", desc); return applyAnswer(el, tmpl); }
      const llm = await groqAnswer(label, ctx);             // tier 3
      if (llm !== null) { log("tier3 GROQ  ", desc); return applyAnswer(el, llm); }
    }
    log("tier4 FLAG  ", desc);
    flag(el);                                               // tier 4
  }

  async function autofill() {
    const modal = findModal();
    const root = modal || document;
    const ctx = jobContext();
    const fields = root.querySelectorAll("input, select, textarea");
    log("modal found:", !!modal, "| fields:", fields.length, "| ctx:", ctx.title, "/", ctx.company);
    for (const el of fields) {
      await fillField(el, ctx);
    }
    log("done");
    // Intentionally never clicks Submit/Next — the user reviews and submits.
  }

  // The button must be a CHILD of the modal — LinkedIn's Easy Apply dialog is a
  // focus trap that swallows clicks on outside (body-level) elements. When a modal
  // is open we place it absolutely inside the modal (next to the ✕); otherwise it
  // floats on the page.
  const BTN_IN_MODAL =
    "position:absolute;top:14px;right:60px;z-index:2147483647;padding:8px 12px;" +
    "background:#0a66c2;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;";
  const BTN_ON_PAGE =
    "position:fixed;top:14px;right:20px;z-index:2147483647;padding:8px 12px;" +
    "background:#0a66c2;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;";

  function ensureButton() {
    const modal = findModal();
    const host = modal || document.body;
    let btn = document.getElementById("aa-autofill-btn");
    if (btn && btn.parentElement === host) return;  // already correctly placed
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "aa-autofill-btn";
      btn.type = "button";
      btn.textContent = "⚡ Autofill";
      btn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); autofill(); });
    }
    if (modal) {
      if (getComputedStyle(modal).position === "static") modal.style.position = "relative";
      btn.style.cssText = BTN_IN_MODAL;
    } else {
      btn.style.cssText = BTN_ON_PAGE;
    }
    host.appendChild(btn);  // moving into the modal makes it a focus-trap child
  }

  new MutationObserver(ensureButton).observe(document.body, { childList: true, subtree: true });
  ensureButton();
  log("userscript loaded on", location.href, "| bank entries:", BANK.length);
})();

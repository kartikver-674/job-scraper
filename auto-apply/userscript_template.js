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

Audit of terms counted as false positives, judged against the résumé
text rather than the hand-written answer key. Definition used: "a
capability, technology, tool, methodology, or professional competency
the candidate claims or uses."

Categories: (1) genuine skill  (2) valid alias/variant of a genuine
skill  (3) contextual/product/domain language  (4) spurious match

KAVYA — 31 flagged
(1) genuine, listed or claimed on the page — 22
    acceptance criteria, apex code comprehension, backlog management,
    brd, business analysis, certified administrator, cr management,
    dashboards, data visualization, excel, frd, gap analysis,
    impact analysis, monthly release rollouts, predictive modelling,
    requirements gathering, rest api integrations, sdlc,
    sprint planning, stakeholder management, uat, user stories
(2) valid alias or fragment of an on-page skill — 8
    as is to be mapping   <- "As-Is/To-Be Mapping"
    data analytics        <- "Data & Analytics"
    requirements gathering elicitation <- "Requirements Gathering & Elicitation"
    backlog               <- "backlog management"
    custom metadata       <- "Custom Metadata Types"
    elicitation           <- "requirements elicitation"
    reports               <- "Reports & Dashboards"
    salesforce certified  <- "Salesforce Certified Administrator"
(3) contextual/role — 1
    business analyst      her job title, not a skill she lists
(4) spurious — 0

KANAV — 11 flagged
(1) genuine — 5
    android, ios, mobile architecture, modularization, state management
(2) valid alias — 6
    ai assisted     <- "AI-Assisted Development"
    authentication  <- "Firebase Authentication"
    ci cd           <- "CI/CD"
    cross platform  <- "cross-platform"
    javascript es6+ <- "JavaScript (ES6+)"
    offline first   <- "offline-first"
(3) contextual — 0
(4) spurious — 0

Not flagged, and worth noting: "field sales" — the one genuine piece of
product language on Kanav's page — was rejected by the gate and does
not appear in either list.

PRECISION, measured against the hand-written key vs audited against the page

    résumé              terms  flagged  legitimate   measured  audited
    kavya                  57       31          30       0.46     0.98
    kanav_reactnative      29       11          11       0.62     1.00

The hand-written key in truth_skills.json has NOT been rewritten. The
measured numbers stand as measured; this file is the second opinion, so
the two can be compared rather than one quietly replacing the other.

CONCLUSION: the low measured precision is an incomplete answer key, not
a loose gate. Forty-one of forty-two flagged terms are legitimate; the
one that is not is a job title, harmless downstream and one term of
fifty-seven. No genuine-noise pattern, so no new rule.

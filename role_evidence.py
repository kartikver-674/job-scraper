"""What kind of work does this person actually do? Decided before search.

Profile Engine v2 reads a résumé as a bag of tools. That is enough to rank a
listing and not nearly enough to decide which listings to look for, so a
Salesforce business analyst who names Apex once gets searched as a Salesforce
developer. The audit measured the general shape of that failure: a
developer-tier platform token converts a non-developer into a developer, and
the corpus then confirms the mistake because developer rows are what the corpus
mostly has.

This module answers the missing question deterministically, from the résumé
text that is already in memory, with no extra model call.

    role_evidence.build(resume_text, signals, employment)   -> the record
    role_evidence.filter_queries(record, queries)           -> kept, rejected

Three ideas carry the whole thing.

**A tool is not a role.** Nothing here maps a technology to an identity. There
is no Salesforce rule, no Python rule, no SQL rule. Platform names are
collected into `platforms` and grant no work mode at all. What grants
`development` is a CONSTRUCTION VERB governing a SOFTWARE ARTEFACT in a clause
the document actually asserts: "wrote the Apex trigger" counts, "Apex" does
not, and neither does "Python" sitting in a skills list. The rule is about
sentence structure, so it generalises to platforms nobody has thought of yet.

**Coordination is not ownership.** "Coordinated with the Apex developers" names
a developer artefact inside a clause that hands the work to somebody else. Those
occurrences are read as `delegation`, which is positive evidence of a
coordinating function and negative evidence of a building one. The existing
negation semantics in skill_evidence already handle "does not write Apex"; this
adds the case where the denial is structural rather than lexical.

**Rejecting is the only power it has.** The search still proposes every query it
proposed before. This layer may veto one, and only when the family that query
belongs to requires a kind of work the résumé shows no trace of, while the
résumé strongly shows some other kind. If the record is thin, ambiguous, or the
query's family is unrecognised, nothing is rejected. An unshipped earlier
experiment (R5) improved precision by deleting people's job searches; the
fail-open rules in `filter_queries` exist so that cannot recur, and the last
surviving query is never taken away.

Everything a verdict rests on is kept, so "why was Salesforce Developer
rejected?" has an answer made of quoted résumé spans rather than a score.
"""
import os
import re

import skill_evidence as se

# Off unless asked for. The engine-version contract in skill_concepts accepts
# only v1 and v2, and widening it is a change to a binding the independent
# review already examined; this is the step-flag mechanism that contract
# documents for exactly this case. Default off means v2 behaviour is
# byte-identical when the flag is absent, which is a property the tests assert
# rather than a claim this comment makes.
FLAG = "SWEEP_ROLE_EVIDENCE"


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")

# ---------------------------------------------------------------- work modes
#
# Small on purpose. These are not occupations; they are kinds of work, and a
# person routinely has several. A family is supported later by the modes it
# needs, which is what keeps this list from growing into an ontology.

DEVELOPMENT = "development"
CONFIGURATION = "configuration"
ANALYSIS = "analysis"
ADMINISTRATION = "administration"
CONSULTING = "consulting"
DELIVERY = "delivery"
MANAGEMENT = "management"
SALES = "sales"
MARKETING = "marketing"
FINANCE = "finance"
PEOPLE = "people"
SUPPORT = "support"
DESIGN = "design"
DATA_PIPELINE = "data_pipeline"

MODES = (DEVELOPMENT, DATA_PIPELINE, CONFIGURATION, ANALYSIS, ADMINISTRATION,
         CONSULTING, DELIVERY, MANAGEMENT, SALES, MARKETING, FINANCE, PEOPLE,
         SUPPORT, DESIGN)

# Verbs that mean something was CONSTRUCTED. Narrower than skill_evidence's
# _ACTION on purpose: that set includes "configured", "monitored", "queried",
# which are exactly the verbs a platform administrator or an analyst uses.
_CONSTRUCT = (r"built|build|building|develop(?:ed|ing|s)?|implement(?:ed|ing|s)?|"
              r"cod(?:ed|ing)|program(?:med|ming)?|wrote|writ(?:ten|ing)|"
              r"author(?:ed|ing)?|engineer(?:ed|ing)?|architect(?:ed|ing)?|"
              r"refactor(?:ed|ing)?|rewrote|rewritten|debug(?:ged|ging)?|"
              r"ship(?:ped|ping)?|created?|creating|automat(?:ed|ing)")

# Things that are software when you make one. "report" and "query" are absent
# deliberately: analysts make both, and counting them would rebuild the very
# confusion this module exists to remove.
_ARTEFACT = (r"code|codebase|application|applications|app|apps|api|apis|"
             r"endpoint|endpoints|micro-?service|micro-?services|service|services|"
             r"script|scripts|module|modules|component|components|"
             r"librar(?:y|ies)|package|packages|class|classes|trigger|triggers|"
             r"function|functions|feature|features|algorithm|algorithms|"
             r"integration|integrations|plugin|plugins|extension|extensions|"
             r"back-?end|front-?end|sdk|framework|frameworks|software|"
             r"unit tests?|test suite|stored procedures?|webhooks?|"
             r"web app|mobile app|website|web site|platform")

_PIPELINE = (r"pipeline|pipelines|etl|elt|data warehouse|warehouse|data lake|"
             r"ingestion|data model|schema|airflow|dbt job|streaming job|"
             r"batch job|data pipeline")

# Platform objects a person CONFIGURES rather than programs. The distinction
# the audit asked for -- platform use versus platform development -- lives here
# and in _ARTEFACT, and in nothing else.
_CONFIG_OBJECT = (r"workflow|workflows|flow|flows|validation rule|validation rules|"
                  r"page layout|page layouts|approval process|permission set|"
                  r"permission sets|profile|profiles|custom field|custom fields|"
                  r"custom object|custom objects|dashboard|dashboards|"
                  r"report|reports|template|templates|queue|queues|"
                  r"user account|user accounts|role hierarchy|sharing rule|"
                  r"record type|picklist|form|forms|setting|settings")
_CONFIG_VERB = (r"configur(?:ed|ing|es)?|customi[sz](?:ed|ing|es)?|"
                r"set up|setup|maintain(?:ed|ing|s)?|administer(?:ed|ing|s)?|"
                r"manag(?:ed|ing|es)|creat(?:ed|ing|es)|built|build|"
                r"updat(?:ed|ing|es)|defin(?:ed|ing|es)")

# Each remaining mode is a flat phrase set. Deliberately plain: a reader has to
# be able to audit a verdict by eye, and a clever scorer here would be a second
# unexplainable layer on top of the one the audit already criticised.
_PHRASES = {
    ANALYSIS: (r"requirements? gathering|gather(?:ed|ing) requirements?|"
               r"business requirements?|functional requirements?|user stor(?:y|ies)|"
               r"process mapping|process map|gap analysis|as-?is|to-?be|"
               r"data analysis|analy[sz](?:ed|ing) data|root cause|"
               r"uat|user acceptance test|test cases?|"
               r"reporting|dashboards?|kpi|metrics|forecast(?:ed|ing|s)?|"
               r"trend analysis|insights?|business case|"
               r"documented? (?:the )?(?:process|requirements?|workflow)"),
    ADMINISTRATION: (r"user access|access requests?|provision(?:ed|ing)?|"
                     r"deprovision|licen[sc]es?|permissions?|"
                     r"system administration|sysadmin|"
                     r"backups?|patch(?:ed|ing|es)?|upgrade(?:d|s)?|"
                     r"incident|outage|uptime|monitoring|"
                     r"active directory|group polic|sandbox refresh|"
                     r"deployment|release management|environment"),
    CONSULTING: (r"stakeholders?|client(?:s)?|workshops?|discovery session|"
                 r"advis(?:ed|ing|ory)|recommend(?:ed|ations?)|"
                 r"implementation|roll-?out|go-?live|"
                 r"end users?|train(?:ed|ing) users?|change management|"
                 r"business process|solution design|blueprint"),
    DELIVERY: (r"sprints?|scrum|backlog|kanban|jira|"
               r"project plan|milestones?|timelines?|roadmap|"
               r"scope|deliverables?|status report|steering|"
               r"cross-?functional|program|delivery"),
    MANAGEMENT: (r"managed a team|team of \d+|direct reports?|"
                 r"line manage|people manage|hir(?:ed|ing)|"
                 r"mentor(?:ed|ing|s)?|coach(?:ed|ing)|performance review|"
                 r"budget|p&l|headcount|led a team|leading a team"),
    SALES: (r"quota|pipeline of|prospect(?:ed|ing|s)?|cold call|"
            r"closed? (?:deals?|business)|won (?:deals?|business)|"
            r"account(?:s)? (?:management|manager)|upsell|cross-?sell|"
            r"renewals?|revenue target|bookings|territory|"
            r"lead generation|qualified leads?|demos?"),
    MARKETING: (r"campaigns?|seo|sem|paid media|social media|"
                r"content (?:calendar|strategy|marketing)|copywriting|"
                r"brand|newsletter|email marketing|"
                r"impressions|click-?through|engagement rate|audience"),
    FINANCE: (r"reconcil(?:ed|ing|iation)|journal entr|ledger|"
              r"accounts payable|accounts receivable|invoic(?:e|es|ing)|"
              r"month-?end|year-?end close|audit|variance|"
              r"budgeting|forecasting|p&l|balance sheet|tax"),
    PEOPLE: (r"recruit(?:ed|ing|ment)?|talent acquisition|sourc(?:ed|ing) candidates?|"
             r"onboard(?:ed|ing)?|payroll|benefits|employee relations|"
             r"interviews? (?:scheduled|coordinated)|offer letters?|hris"),
    SUPPORT: (r"tickets?|helpdesk|help desk|service desk|"
              r"troubleshoot(?:ing)?|triage|escalat(?:ed|ion|ions)|"
              r"sla|first response|end-?user support|resolved issues?"),
    DESIGN: (r"wireframes?|prototypes?|mockups?|figma|sketch|"
             r"user research|usability test|design system|"
             r"visual design|interaction design|style guide|"
             r"typography|brand identity"),
}

# Hands the work to somebody else. A developer artefact inside one of these is
# evidence of coordinating, not of building.
_DELEGATED = re.compile(
    r"\b(?:coordinat\w*|liais\w*|partner\w*|collaborat\w*|work\w*|engag\w*|"
    r"align\w*|interfac\w*)\s+(?:closely\s+)?with\b"
    r"|\bhand(?:ed|ing)?\s*(?:-|\s)?(?:off|over)?\s+to\b"
    r"|\bpass(?:ed|ing)?\s+to\b"
    r"|\bprovided\s+to\b|\bdelivered\s+to\b"
    r"|\bsupport(?:ed|ing)?\s+the\s+\w+\s+team\b"
    r"|\bfor\s+the\s+(?:development|engineering|dev|technical)\s+team\b"
    r"|\bto\s+(?:the\s+)?(?:developers?|engineers?|engineering|dev team)\b"
    r"|\bbriefed?\b|\btranslat\w*\s+(?:business\s+)?requirements?\b",
    re.I)

# People who are not the candidate. "Apex developers" is somebody else.
# "<other people> <construction verb>" — the verb's subject is not the
# candidate. Kept tight: the people must immediately precede the verb.
_SOMEONE_ELSE_ACTS = re.compile(
    r"\b(?:developers?|engineers?|engineering|dev team|development team|"
    r"technical team|vendors?|consultants?|architects?|contractors?)\s+"
    r"(?:who\s+|that\s+|to\s+)?(?:%s)\b" % _CONSTRUCT, re.I)

_OTHER_PEOPLE = re.compile(
    r"\b(developers?|engineers?|engineering|dev team|development team|"
    r"technical team|vendor|vendors|offshore|consultants?|architects?)\b", re.I)


# ------------------------------------------------------------- role families
#
# Coarse on purpose. These exist to validate a query, not to describe a career,
# and every family added here is a family the gate could get wrong.
#
# `needs` is the load-bearing field: a family with a non-empty `needs` may be
# REJECTED when the résumé shows none of those work modes. A family with an
# empty `needs` is never rejected by this layer no matter how little evidence
# there is, because for those the cost of a wrong veto is higher than the cost
# of a wrong query. Only the families where a bare technology token is known to
# manufacture a false identity are gated, which is the defect the audit
# measured; widening that set is a data change, not a code change.

FAMILIES = {
    "software_engineering": {"modes": (DEVELOPMENT,), "needs": (DEVELOPMENT,)},
    "data_engineering":     {"modes": (DATA_PIPELINE,),
                             "needs": (DATA_PIPELINE, DEVELOPMENT)},
    "ml_engineering":       {"modes": (DEVELOPMENT, DATA_PIPELINE),
                             "needs": (DEVELOPMENT, DATA_PIPELINE)},
    "data_analytics":       {"modes": (ANALYSIS,), "needs": ()},
    "business_analysis":    {"modes": (ANALYSIS, CONSULTING), "needs": ()},
    "functional_consulting": {"modes": (CONFIGURATION, CONSULTING), "needs": ()},
    "it_administration":    {"modes": (ADMINISTRATION,), "needs": ()},
    "project_delivery":     {"modes": (DELIVERY,), "needs": ()},
    "product":              {"modes": (DELIVERY, ANALYSIS), "needs": ()},
    "management":           {"modes": (MANAGEMENT,), "needs": ()},
    "sales":                {"modes": (SALES,), "needs": ()},
    "marketing":            {"modes": (MARKETING,), "needs": ()},
    "finance":              {"modes": (FINANCE,), "needs": ()},
    "hr_recruiting":        {"modes": (PEOPLE,), "needs": ()},
    "support":              {"modes": (SUPPORT,), "needs": ()},
    "design":               {"modes": (DESIGN,), "needs": ()},
}
GATED = tuple(f for f, spec in FAMILIES.items() if spec["needs"])

# Query -> family. Ordered, first match wins, most specific first. Every rule
# is a plain token test so any verdict can be checked by eye.
_QUERY_RULES = (
    ("data_engineering", r"\b(data enginee?r|etl developer|analytics engineer|"
                         r"big ?data|data platform enginee?r)\b"),
    ("ml_engineering",   r"\b(machine learning|ml|ai/ml|deep learning|nlp)\b"
                         r".{0,20}\b(engineer|developer|scientist)\b"
                         r"|\b(ai|ml) (engineer|developer)\b"),
    ("data_analytics",   r"\b(data analyst|business intelligence|bi analyst|"
                         r"reporting analyst|data scientist|insight analyst|"
                         r"analytics)\b"),
    ("design",           r"\b(ux|ui/ux|user experience|product design|"
                         r"graphic design|visual design|interaction design)\b"),
    ("support",          r"\b(technical support|helpdesk|help desk|service desk|"
                         r"support (engineer|specialist|analyst|associate))\b"),
    ("it_administration", r"\b(system(s)? administrator|sysadmin|it administrator|"
                          r"network administrator|salesforce administrator|"
                          r"servicenow administrator|administrator|admin)\b"),
    ("functional_consulting", r"\b(functional consultant|techno.?functional|"
                              r"implementation consultant|solution consultant|"
                              r"solutions consultant|erp consultant)\b"
                              r"|\bconsultant\b"),
    ("business_analysis", r"\b(business analyst|business systems analyst|"
                          r"systems analyst|requirements analyst|\bba\b)\b"),
    ("product",          r"\b(product manager|product owner|product management)\b"),
    ("project_delivery", r"\b(project manager|programme? manager|delivery manager|"
                         r"scrum master|agile coach|pmo|project lead)\b"),
    ("hr_recruiting",    r"\b(recruit\w*|talent acquisition|sourcer|hr |human resources?|"
                         r"people operations)\b"),
    ("marketing",        r"\b(marketing|seo|sem|paid media|content (manager|writer|"
                         r"strategist)|copywriter|brand)\b"),
    ("finance",          r"\b(financial analyst|finance analyst|fp&a|accountant|"
                         r"accounting|controller|treasury|bookkeep\w*)\b"),
    ("sales",            r"\b(account executive|account manager|sales|"
                         r"business development|customer success|sdr|bdr|"
                         r"inside sales|client success|renewals)\b"),
    ("management",       r"\b(head of|director of|vice president|chief |"
                         r"general manager)\b"),
    # Last resort. Anything still naming a build role is software engineering.
    ("software_engineering",
     r"\b(software (engineer|developer)|developer|engineer|programmer|sde|"
     r"architect|full ?stack|front ?end|back ?end|mobile developer|"
     r"web developer|qa engineer|sdet|devops|sre)\b"),
)
_QUERY_COMPILED = tuple((f, re.compile(rx, re.I)) for f, rx in _QUERY_RULES)


def family_of(query):
    """The coarse family a proposed query belongs to, or None if unrecognised.

    None is a real answer and it means FAIL OPEN: a query this cannot place is
    never rejected.
    """
    text = " " + re.sub(r"\s+", " ", str(query or "").strip().lower()) + " "
    if not text.strip():
        return None
    for family, rx in _QUERY_COMPILED:
        if rx.search(text):
            return family
    return None


# --------------------------------------------------------------- the scanner

QUOTE_CHARS = 140
MAX_HITS = 8
_CONSTRUCT_RX = re.compile(r"\b(?:%s)\b" % _CONSTRUCT, re.I)
_CONFIG_VERB_RX = re.compile(r"\b(?:%s)\b" % _CONFIG_VERB, re.I)
_ARTEFACT_RX = re.compile(r"\b(?:%s)\b" % _ARTEFACT, re.I)
_PIPELINE_RX = re.compile(r"\b(?:%s)\b" % _PIPELINE, re.I)
_CONFIG_OBJECT_RX = re.compile(r"\b(?:%s)\b" % _CONFIG_OBJECT, re.I)
_PHRASE_RX = {mode: re.compile(r"\b(?:%s)\b" % rx, re.I)
              for mode, rx in _PHRASES.items()}

# Platform and product names are collected so a human can see what the résumé
# leaned on. They are recorded and never consulted by the gate.
_PLATFORM_RX = re.compile(
    r"\b(salesforce|sfdc|sap|servicenow|workday|oracle|netsuite|dynamics|"
    r"hubspot|zendesk|jira|confluence|tableau|power ?bi|looker|snowflake|"
    r"databricks|sharepoint|magento|shopify|wordpress|marketo|pardot|"
    r"peoplesoft|epic|cerner|quickbooks|sage|zoho)\b", re.I)


def _quote(text, lo, hi):
    snippet = re.sub(r"\s+", " ", text[max(0, lo):hi]).strip()
    return snippet[:QUOTE_CHARS]


def _hit(text, low, start, end, spans, mode, kind):
    section, entry = se.section_at(start, spans)
    bounds = se._bounds(start, spans, len(text))
    status = se.classify(low, start, end, section, bounds)
    lo, hi = se._governed(low, start, end, bounds)
    return {
        "mode": mode, "kind": kind, "status": status,
        "section": section, "entry": entry or "",
        "quote": _quote(text, lo, hi),
        "clause": (lo, hi),
    }


def _governed_hits(text, low, spans, object_rx, verb_rx, mode):
    """Occurrences where a verb of the right kind governs the right object.

    This is the general rule the audit asked for. It never asks which
    technology is named; it asks whether the sentence says the candidate MADE
    something. A clause that hands the work to other people is recorded as
    delegation instead, so "coordinated with the Apex developers" cannot
    produce development evidence.
    """
    out = []
    for match in object_rx.finditer(low):
        hit = _hit(text, low, match.start(), match.end(), spans, mode,
                   "verb_governs_object")
        lo, hi = hit.pop("clause")
        if not (verb_rx.search(low, lo, match.start())
                or verb_rx.search(low, match.end(), hi)):
            continue
        # Whose verb is it? "the developers build the components" has a
        # construction verb and an artefact and is somebody ELSE's work. A
        # named group of people standing immediately before the verb is the
        # subject of that verb, so the clause grants the candidate nothing.
        owned = not _SOMEONE_ELSE_ACTS.search(low, lo, hi)
        if not owned or (_DELEGATED.search(low, lo, hi)
                         and _OTHER_PEOPLE.search(low, lo, hi)):
            hit["kind"] = "delegated"
            hit["mode"] = CONSULTING
        out.append(hit)
        if len(out) >= MAX_HITS * 3:
            break
    # Two artefact nouns in one clause describe one thing the person did.
    # Counting "wrote a script to pull from the reporting API" twice turned a
    # single personal side-script into strong development evidence.
    seen, unique = set(), []
    for hit in out:
        key = (hit["mode"], hit["kind"], hit["quote"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def _phrase_hits(text, low, spans, mode):
    out = []
    for match in _PHRASE_RX[mode].finditer(low):
        hit = _hit(text, low, match.start(), match.end(), spans, mode, "phrase")
        hit.pop("clause")
        out.append(hit)
        if len(out) >= MAX_HITS * 3:
            break
    return out


def _fold(hits):
    """Turn raw occurrences into a per-mode summary.

    Strength is a count of distinct NAMED entries, the same independence rule
    skill_evidence uses: two bullets in one job are one entry, the same work in
    two jobs is two. It is not a tuned score and there is nothing to calibrate.
    """
    modes = {}
    for hit in hits:
        mode = hit["mode"]
        row = modes.setdefault(mode, {"asserted": 0, "entries": set(),
                                      "learning": 0, "refuted": 0,
                                      "delegated": 0, "evidence": []})
        if hit["kind"] == "delegated":
            row["delegated"] += 1
        if hit["status"] in (se.NEGATED, se.PLANNED):
            row["refuted"] += 1
            if len(row["evidence"]) < MAX_HITS:
                row["evidence"].append(hit)
            continue
        if hit["status"] == se.LEARNING:
            row["learning"] += 1
        else:
            row["asserted"] += 1
            if hit["entry"]:
                row["entries"].add((hit["section"], hit["entry"]))
        if len(row["evidence"]) < MAX_HITS:
            row["evidence"].append(hit)
    for row in modes.values():
        entries = len(row.pop("entries"))
        row["entries"] = entries
        # none / weak / strong. Two independently named entries is the ideal
        # signal, but entry attribution only succeeds on about half of real
        # spans, so a mode asserted twice anywhere counts too. Requiring named
        # entries alone left half the development set with no usable record.
        row["strength"] = ("none" if not row["asserted"] else
                           "strong" if (entries > 1 or row["asserted"] >= 2)
                           else "weak")
    return modes


# ----------------------------------------------------------------- the record

SCHEMA = 1


_ORDER = ("none", "weak", "strong")


def _atleast(got, floor):
    return _ORDER.index(got) >= _ORDER.index(floor)


def _family_strength(modes, family):
    best = "none"
    for mode in FAMILIES[family]["modes"]:
        got = modes.get(mode, {}).get("strength", "none")
        if ("none", "weak", "strong").index(got) > ("none", "weak", "strong").index(best):
            best = got
    return best


def _title_families(titles):
    out = {}
    for title in titles or ():
        fam = family_of(title)
        if fam:
            out.setdefault(fam, []).append(title)
    return out


def build(resume_text, signals=None, employment=()):
    """The deterministic role record, built before a single query exists.

    Nothing here is a prediction of the person's job title. It is a record of
    what the document shows them doing, with the provenance kept so any later
    veto can be explained in the résumé's own words.
    """
    text = str(resume_text or "")
    low = text.lower()
    spans = se.sections(text)
    signals = signals or {}

    hits = []
    hits += _governed_hits(text, low, spans, _ARTEFACT_RX, _CONSTRUCT_RX,
                           DEVELOPMENT)
    hits += _governed_hits(text, low, spans, _PIPELINE_RX, _CONSTRUCT_RX,
                           DATA_PIPELINE)
    hits += _governed_hits(text, low, spans, _CONFIG_OBJECT_RX,
                           _CONFIG_VERB_RX, CONFIGURATION)
    for mode in _PHRASES:
        hits += _phrase_hits(text, low, spans, mode)
    modes = _fold(hits)

    held = list(signals.get("employment_titles") or
                [r.get("title") for r in (employment or ())
                 if isinstance(r, dict) and r.get("title")])
    headline = list(signals.get("titles") or ())
    target_text = str(signals.get("target_field") or "").strip()

    by_title = _title_families(held + headline)
    supports, does_not = {}, {}
    for family in FAMILIES:
        strength = _family_strength(modes, family)
        # A gated family is where one stray mention manufactures a false
        # identity, so for those, and only those, weak evidence is not support.
        # One "built two small triggers" bullet under a business-analyst title
        # is precisely the case the audit measured, and it is weak by design.
        floor = "strong" if family in GATED else "weak"
        if _atleast(strength, floor):
            supports[family] = strength
        elif family in by_title:
            # A held job title is grounded evidence in its own right, even when
            # the bullets underneath it are thin.
            supports[family] = "weak"
    for family in GATED:
        if family in supports:
            continue
        needed = FAMILIES[family]["needs"]
        does_not[family] = {
            "missing": list(needed),
            "strength_seen": _family_strength(modes, family),
            "refuted": sum(modes.get(m, {}).get("refuted", 0) for m in needed),
            "delegated": sum(modes.get(m, {}).get("delegated", 0)
                             for m in needed),
        }

    # Learning-only evidence is what a career change looks like before the job
    # title catches up: a bootcamp, a certification, a side project.
    transition = sorted(m for m, row in modes.items()
                        if row["learning"] and not row["asserted"])
    target_family = family_of(target_text) if target_text else None

    return {
        "schema": SCHEMA,
        "held_titles": held,
        "headline_titles": headline,
        # UNGROUNDED, and labelled as such at every point of use. It may keep a
        # family alive; it may never establish one on its own.
        "stated_target": {"text": target_text, "family": target_family,
                          "grounded": False},
        "title_families": by_title,
        "work_modes": {m: {k: v for k, v in row.items() if k != "evidence"}
                       for m, row in sorted(modes.items())},
        "evidence": {m: row["evidence"] for m, row in sorted(modes.items())},
        "platforms": sorted({p.group(0).lower()
                             for p in _PLATFORM_RX.finditer(low)}),
        "supports": supports,
        "does_not_support": does_not,
        "transition_modes": transition,
        # Thin means the document barely says anything about what the person
        # does. Two asserted occurrences anywhere is a low bar, and it is meant
        # to be: the gate below has its own, stricter, per-family test.
        "thin": sum(row["asserted"] for row in modes.values()) < 2,
        "provenance": "deterministic clause scan of the résumé text; "
                      "no model call, no corpus, no engine output",
    }


# -------------------------------------------------------------- the gate
#
# REJECT-ONLY. This never proposes a query and never reorders one. Every exit
# below that returns the queries untouched is a fail-open path, and they are
# deliberately numerous: the unshipped R5 experiment raised precision by
# removing people's searches entirely, and the cost of one contaminated query
# is far lower than the cost of a candidate with nothing to search for.


def _protected(record, family):
    """Reasons this family may not be vetoed, whatever the work modes say."""
    if family in (record.get("title_families") or {}):
        return ("held title", record["title_families"][family])
    target = record.get("stated_target") or {}
    if target.get("family") == family:
        # Ungrounded on its own. Paired with actual training or project
        # evidence it is a career change in progress, and deleting the search
        # is the one thing that makes a career change impossible.
        overlap = sorted(set(record.get("transition_modes") or ())
                         & set(FAMILIES.get(family, {}).get("needs", ())))
        if overlap:
            return ("stated target plus training evidence", overlap)
    return None


def filter_queries(record, queries):
    """(kept, rejections). Rejections carry their whole reason."""
    queries = [q for q in (queries or [])]
    if not record or not queries:
        return queries, []
    if record.get("thin"):
        return queries, [{"fail_open": "role evidence is thin; nothing rejected"}]

    # "The document clearly shows some OTHER kind of work." Without this a
    # sparse résumé would have its technical queries stripped on the strength
    # of absence alone, which is how a gate starves somebody.
    other = sum(row["asserted"] for mode, row in
                (record.get("work_modes") or {}).items()
                if mode not in (DEVELOPMENT, DATA_PIPELINE))
    anchors = sorted(f for f, s in (record.get("supports") or {}).items()
                     if f not in GATED)
    if not anchors or other < 2:
        return queries, [{"fail_open": "the résumé does not clearly show a "
                                       "different kind of work; nothing rejected"}]

    kept, rejected = [], []
    for query in queries:
        family = family_of(query)
        if family is None or family not in GATED or family in (record.get("supports") or {}):
            kept.append(query)
            continue
        shield = _protected(record, family)
        if shield:
            kept.append(query)
            continue
        gap = (record.get("does_not_support") or {}).get(family)
        if not gap:
            kept.append(query)
            continue
        rejected.append({
            "query": query,
            "query_family": family,
            "candidate_supports": dict(record.get("supports") or {}),
            "missing_work_modes": gap["missing"],
            "refuted_mentions": gap["refuted"],
            "delegated_mentions": gap["delegated"],
            "platforms_present": record.get("platforms") or [],
            "reason": _reason(family, gap, record),
        })

    if not kept:
        # Everything would go. That is the R5 failure, so it does not happen.
        return queries, [{"fail_open": "every query was in a gated family; "
                                       "nothing rejected",
                          "would_have_rejected": [r["query"] for r in rejected]}]
    return kept, rejected


def _reason(family, gap, record):
    missing = " or ".join(gap["missing"])
    if gap.get("strength_seen") == "weak":
        how = ("a single passing mention of building something, which is not "
               "ownership of this kind of work")
    elif gap["delegated"]:
        how = (f"the résumé names this kind of work only where it is handed to "
               f"other people ({gap['delegated']} coordination mention(s))")
    elif gap["refuted"]:
        how = (f"the résumé explicitly denies or defers it "
               f"({gap['refuted']} mention(s))")
    elif record.get("platforms"):
        how = (f"platform evidence exists ({', '.join(record['platforms'][:4])}) "
               f"but no substantive {missing} ownership")
    else:
        how = f"no {missing} evidence anywhere in the document"
    supported = ", ".join(sorted(record.get("supports") or {})) or "nothing"
    return f"{family} needs {missing}; {how}. The document supports: {supported}."


def explain(record, rejections):
    """Human-readable provenance for a debugging log."""
    out = []
    for row in rejections:
        if "fail_open" in row:
            out.append(f"  FAIL OPEN: {row['fail_open']}")
            continue
        out.append(f"  rejected {row['query']!r}\n"
                   f"    query family      : {row['query_family']}\n"
                   f"    candidate supports: {row['candidate_supports']}\n"
                   f"    missing           : {', '.join(row['missing_work_modes'])}\n"
                   f"    platforms         : {', '.join(row['platforms_present']) or '-'}\n"
                   f"    reason            : {row['reason']}")
    return "\n".join(out)


def demo():
    """Self-check. The shapes the audit measured, none of them special-cased."""
    ba = ("Salesforce Business Analyst\n"
          "EXPERIENCE\n"
          "Business Analyst, Clearwater Financial, 2021 - Present\n"
          "- Gathered business requirements from stakeholders and wrote user "
          "stories for the Salesforce roadmap\n"
          "- Configured validation rules and page layouts in Salesforce\n"
          "- Coordinated with the Apex developers who built the triggers\n"
          "- Ran UAT with end users before each go-live\n")
    rec = build(ba, {"employment_titles": ["Business Analyst"],
                     "titles": ["Salesforce Business Analyst"],
                     "target_field": ""})
    assert DEVELOPMENT not in rec["supports"], rec["work_modes"]
    assert "software_engineering" in rec["does_not_support"]
    assert "salesforce" in rec["platforms"]
    kept, rej = filter_queries(rec, ["salesforce business analyst",
                                     "salesforce developer", "business analyst"])
    assert "salesforce developer" not in kept, kept
    assert "salesforce business analyst" in kept and "business analyst" in kept
    assert rej and rej[0]["query_family"] == "software_engineering"

    dev = ("Salesforce Developer\n"
           "EXPERIENCE\n"
           "Salesforce Developer, Acme Cloud, 2022 - Present\n"
           "- Wrote Apex classes and triggers for the billing integration\n"
           "- Built Lightning Web Components used across three business units\n"
           "SKILLS\nApex, SOQL, LWC\n")
    rec2 = build(dev, {"employment_titles": ["Salesforce Developer"],
                       "titles": ["Salesforce Developer"], "target_field": ""})
    assert "software_engineering" in rec2["supports"], rec2["work_modes"]
    kept2, rej2 = filter_queries(rec2, ["salesforce developer", "apex developer"])
    assert kept2 == ["salesforce developer", "apex developer"], kept2
    assert not [r for r in rej2 if "query" in r]

    # A career changer: old title, stated target, training evidence only.
    switch = ("Retail Store Manager moving into software\n"
              "EXPERIENCE\nStore Manager, Northgate Retail, 2018 - 2024\n"
              "- Managed a team of 12 and owned the P&L for the branch\n"
              "EDUCATION\n"
              "Full-stack bootcamp, 2024 - built three web applications\n")
    rec3 = build(switch, {"employment_titles": ["Store Manager"],
                          "titles": ["Store Manager"],
                          "target_field": "software developer"})
    kept3, _r3 = filter_queries(rec3, ["store manager", "software developer"])
    assert "software developer" in kept3, (kept3, rec3["transition_modes"])

    print("role_evidence demo ok")


if __name__ == "__main__":
    demo()

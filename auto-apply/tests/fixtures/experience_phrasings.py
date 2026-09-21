"""Labelled JD phrasings, for measuring experience_guard's extractor.

MEASUREMENT ONLY. No production path imports this; test_experience_guard.py
runs it so the figures quoted in
docs/experience-mismatch-guard-audit.md cannot rot.

Why a hand-built set and not real postings: no JD text is persisted anywhere in
this repo. `to_output` writes 24 columns and `Description` is not one of them,
so all 22,530 stored rows carry the PARSED figure ("6+") and no prose. The
phrasings below are taken from the taxonomy in the brief plus the JD templates
scraper.py's own comments record from live requisitions (Accenture,
Netradyne, Cummins).

Each row is (text, expected_overall_minimum_or_None).
"""
CASES = [
    # --- overall minimum, confirmed -------------------------------------
    ("5+ years of software development experience.", 5),
    ("6+ years of professional experience.", 6),
    ("Minimum 6 years of experience required.", 6),
    ("At least 6 years of relevant experience.", 6),
    ("Minimum 5 Year(s) Of Experience Is Required.", 5),
    ("8+ years of total experience.", 8),
    ("6+ years of hands-on experience as a Business Analyst.", 6),
    ("7+ years of industry experience.", 7),
    ("Experience: 6+ years overall, 3+ years Salesforce.", 6),
    ("5+ years total development experience and 1+ years React.", 5),
    ("6+ years of experience in software engineering.", 6),
    ("10+ years of experience is mandatory.", 10),
    ("4+ years of working experience.", 4),
    ("Candidates must have 6+ years of professional experience.", 6),
    # --- range: the floor, never the ceiling ----------------------------
    ("3-6 years of experience.", 3),
    ("5 to 12 years of hands-on experience.", 5),
    ("10-18+ years of overall IT experience.", 10),
    ("4 to 9 years of experience.", 4),
    # --- skill-specific: not an overall bar -----------------------------
    ("6+ years of experience in Salesforce Sales Cloud.", None),
    ("6+ years of Java development experience.", None),
    ("6+ years of experience with React and Node.", None),
    ("8+ years of experience working with distributed systems.", None),
    ("3+ years of total experience, 6+ years of Salesforce experience.", 3),
    ("2+ years hands-on in AI/ML.", None),
    # --- preferred: never a bar -----------------------------------------
    ("6+ years of experience preferred.", None),
    ("Ideally 5-7 years of experience.", None),
    ("8 years of experience would be a plus.", None),
    ("10+ years of experience is desirable.", None),
    ("Nice to have: 7+ years of experience.", None),
    # --- ceiling: not a floor -------------------------------------------
    ("Up to 6 years of experience.", None),
    ("No more than 8 years of experience.", None),
    # --- ambiguous: uncommitted, fails open -----------------------------
    ("The role involves 6 years of experience.", None),
    ("Our team has 30 years of combined experience.", None),
    # --- not experience at all ------------------------------------------
    ("A 4-year bachelor's degree is required.", None),
    ("Educational Qualification: 15 Years Full Time Education.", None),
    ("Minimum 16 years of formal education.", None),
    ("Founded 6 years ago, we now have 3+ years of experience shipping ML.", 3),
    ("In business 12 years. Seeking 3 years of experience.", 3),
    ("You will manage a team of 8.", None),
    ("Salary $100k per year.", None),
    ("Notice period: 3 years bond.", None),
    ("Contract duration: 2 years.", None),
    # --- the Accenture template both-words case -------------------------
    ("Minimum 3 Year(s) Of Experience Is Required. Educational "
     "Qualification: 15 Years Full Time Education.", 3),
    # --- the Netradyne labelled-field case ------------------------------
    ("Business Systems Group : 10+ years", None),
]

"""Evaluation strata for Profile Engine v2, and the labels they are judged on.

SEPARATE FROM people.py ON PURPOSE
----------------------------------
bench/people.py generates the 52-document regression suite, which is the
gate for extraction, dates, layouts and the router. Adding people to it
would move that baseline, and a regression gate whose baseline moves with
the feature under test is not a gate. So the three strata it lacks live
here, in its own format, and the 52 documents are untouched.

WHO WROTE THESE LABELS — READ THIS BEFORE BELIEVING A NUMBER
------------------------------------------------------------
Three different kinds of truth are mixed below, and they are not equally
trustworthy:

  PRE-EXISTING   people.py's skills, titles, employment and `relevant`
                 flags. Written before Engine v2 existed, for a different
                 question, by someone who was not trying to make v2 look
                 good. The PDFs are generated FROM them, so the document
                 cannot disagree with the answer. This is the strongest
                 evidence available here.

  AUTHORED       the role labels below — `credible`, `wrong`. Written by
                 reading each persona's own definition and asking "what
                 would this person plausibly search for, and what would
                 be a miss?". Written by the same author as the engine,
                 which is a real conflict of interest, and stated as one.
                 They are deliberately COARSE — word stems and whole
                 careers, not exact strings — because a label precise
                 enough to name the expected output would be marking its
                 own homework.

  NOT LABELLED   importance tiers. A tier is "where does this skill
                 appear in the document", which is exactly what
                 skill_evidence computes; a hand-written tier label would
                 be the same rule applied by hand, and agreement would
                 measure nothing. Importance is therefore judged on
                 INVARIANTS (does provenance change it? does rarity
                 reverse it?) rather than on accuracy against a key.

None of this is independent labelling in the sense the evaluation plan
asks for. It is the best available without a second person, and the
report says so in those words.
"""

# --------------------------------------------------------------------------
# The strata people.py does not cover
# --------------------------------------------------------------------------

EXTRA = {
    # ------------------------------------------------------------ priya
    "priya": {
        "difficulty": "mobile specialist — one platform, deep",
        "name": "Priya Raman",
        "email": "priya.raman@example.com",
        "phone": "+91 98400 11223",
        "location": "Chennai, India",
        "headline": "Mobile Engineer",
        "years_experience": 4,
        "skills": ["react native", "typescript", "expo", "redux toolkit",
                   "fastlane", "firebase cloud messaging", "detox"],
        "employment": [
            {"company": "Kadal Apps", "title": "Mobile Engineer",
             "location": "Chennai, India", "start": "2022-06", "end": None,
             "bullets": [
                 "Ships a React Native app on both stores every fortnight.",
                 "Cut cold start from 3.1s to 900ms with Hermes and lazy routes.",
                 "Owns the Detox suite gating every release.",
             ]},
            {"company": "Vellore Mobility", "title": "Android Developer",
             "location": "Chennai, India", "start": "2021-07", "end": "2022-05",
             "bullets": [
                 "Built the driver app in React Native against a REST backend.",
             ]},
        ],
        "education": [
            {"institution": "Anna University", "degree": "BE Computer Science",
             "start": "2017-08", "end": "2021-05"},
        ],
        "projects": [
            {"name": "Kolam", "stack": ["react native", "expo"],
             "blurb": "Offline-first rangoli pattern sketchbook."},
        ],
        "certifications": [],
    },

    # ------------------------------------------------------------ omar
    "omar": {
        "difficulty": "padded skills list — sixty terms, thin evidence",
        "name": "Omar Haddad",
        "email": "omar.haddad@example.com",
        "phone": "+962 7 9000 4455",
        "location": "Amman, Jordan",
        "headline": "Software Engineer",
        "years_experience": 2,
        # Sixty terms, of which the employment bullets evidence four. This
        # is the stratum where "listed under skills" must NOT become "does
        # this for a living".
        "skills": [
            "javascript", "typescript", "python", "java", "c++", "c#", "go",
            "rust", "php", "ruby", "kotlin", "swift", "scala", "perl",
            "react", "angular", "vue", "svelte", "next.js", "nuxt",
            "node.js", "express", "django", "flask", "spring", "rails",
            "laravel", "fastapi", "graphql", "rest api",
            "postgresql", "mysql", "mongodb", "redis", "cassandra",
            "elasticsearch", "dynamodb", "sqlite", "neo4j",
            "docker", "kubernetes", "terraform", "ansible", "jenkins",
            "aws", "azure", "gcp", "linux", "nginx",
            "git", "jira", "agile", "scrum", "ci/cd", "jest", "cypress",
            "webpack", "vite", "figma", "postman",
        ],
        "employment": [
            {"company": "Petra Systems", "title": "Software Engineer",
             "location": "Amman, Jordan", "start": "2024-01", "end": None,
             "bullets": [
                 "Maintains a Django service behind an Nginx proxy.",
                 "Writes the PostgreSQL migrations for the billing schema.",
             ]},
        ],
        "education": [
            {"institution": "University of Jordan",
             "degree": "BSc Computer Science",
             "start": "2019-09", "end": "2023-06"},
        ],
        "projects": [],
        "certifications": [],
    },

    # ------------------------------------------------------------ yuki
    "yuki": {
        "difficulty": "older stack then new stack — the career is the newer half",
        "name": "Yuki Tanaka",
        "email": "yuki.tanaka@example.com",
        "phone": "+81 90 1234 5678",
        "location": "Osaka, Japan",
        "headline": "Software Engineer",
        "years_experience": 9,
        "skills": ["typescript", "react", "node.js", "graphql", "jquery",
                   "php", "mysql", "postgresql"],
        "employment": [
            {"company": "Namba Digital", "title": "Software Engineer",
             "location": "Osaka, Japan", "start": "2021-04", "end": None,
             "bullets": [
                 "Builds React and TypeScript front ends over a GraphQL gateway.",
                 "Moved the checkout to Node.js and PostgreSQL.",
             ]},
            {"company": "Yodogawa Web", "title": "Web Developer",
             "location": "Osaka, Japan", "start": "2016-04", "end": "2021-03",
             "bullets": [
                 "Maintained PHP and jQuery sites on shared MySQL hosting.",
             ]},
        ],
        "education": [
            {"institution": "Osaka Institute of Technology",
             "degree": "BEng Information Science",
             "start": "2012-04", "end": "2016-03"},
        ],
        "projects": [],
        "certifications": [],
    },
}


# --------------------------------------------------------------------------
# Role labels — AUTHORED, see the module docstring
# --------------------------------------------------------------------------
#
# `credible`  word stems that may legitimately appear in this person's
#             searches. A query containing any of them is not a miss.
# `wrong`     whole careers that would be a miss. These are the adjacent
#             professions each persona is most likely to be mistaken for,
#             which is the failure the audit found three times over.
#
# Deliberately coarse. A label naming the exact expected query would be
# marking its own homework; a label naming the PROFESSION is a judgement
# a reader can disagree with, which is the point.

ROLES = {
    "ada": {"stratum": "backend specialist",
            "credible": ["backend", "back end", "python", "django", "platform",
                         "software", "api", "server"],
            "wrong": ["frontend", "front end", "mobile", "android", "ios",
                      "data engineer", "machine learning", "qa", "sales",
                      "salesforce", "designer"]},
    "bhaskar": {"stratum": "student / graduate, no employment",
                "credible": ["software", "developer", "engineer", "graduate",
                             "junior", "associate", "intern", "trainee",
                             "python", "web"],
                "wrong": ["principal", "staff", "head of", "director",
                          "sales", "salesforce", "data scientist"]},
    "chen": {"stratum": "senior engineer, work-heavy",
             "credible": ["platform", "infrastructure", "site reliability",
                          "sre", "devops", "cloud", "kubernetes", "software"],
             "wrong": ["frontend", "mobile", "android", "salesforce", "sales",
                       "qa", "designer", "machine learning"]},
    "dmitri": {"stratum": "unknown / new technologies",
               "credible": ["verification", "systems", "hardware", "firmware",
                            "embedded", "formal", "software", "engineer"],
               "wrong": ["sales", "salesforce", "marketing", "frontend",
                         "mobile", "data scientist"]},
    "esi": {"stratum": "project-heavy full-stack",
            "credible": ["full stack", "fullstack", "full-stack", "web",
                         "react", "node", "javascript", "typescript",
                         "frontend", "front end", "software"],
            "wrong": ["machine learning", "data engineer", "salesforce",
                      "sales", "qa", "embedded", "android"]},
    "farida": {"stratum": "coursework / certification heavy",
               "credible": ["data", "analytics", "etl", "pipeline",
                            "engineer", "warehouse", "bi"],
               "wrong": ["frontend", "mobile", "salesforce", "sales", "qa",
                         "android", "ios"]},
    "gopal": {"stratum": "QA specialist",
              "credible": ["qa", "test", "sdet", "automation", "quality"],
              "wrong": ["frontend", "mobile", "data engineer", "salesforce",
                        "sales", "machine learning"]},
    "hana": {"stratum": "ML professional and career switcher",
             "credible": ["machine learning", "ml", "computer vision", "ai",
                          "data scientist", "research", "engineer"],
             "wrong": ["structural", "civil", "frontend", "salesforce",
                       "sales", "qa", "mobile"]},
    "iris": {"stratum": "frontend specialist, freelance",
             "credible": ["frontend", "front end", "react", "ui", "web",
                          "javascript", "typescript", "full stack"],
             "wrong": ["backend", "data engineer", "machine learning",
                       "salesforce", "sales", "embedded", "android"]},
    "jonas": {"stratum": "employment gaps",
              "credible": ["data", "analytics", "pipeline", "etl", "engineer",
                           "warehouse"],
              "wrong": ["frontend", "mobile", "salesforce", "sales", "qa"]},
    "kwame": {"stratum": "career switcher, unannounced",
              "credible": ["data", "analytics", "engineer", "pipeline",
                           "python", "sql"],
              "wrong": ["teacher", "teaching", "mathematics", "tutor",
                        "lecturer", "salesforce", "sales", "frontend"]},
    "lena": {"stratum": "internship converted to full-time",
             "credible": ["software", "engineer", "developer", "junior",
                          "associate", "backend", "python", "web"],
             "wrong": ["principal", "staff", "director", "salesforce",
                       "sales", "data scientist", "machine learning"]},
    "mateo": {"stratum": "concurrent roles",
              "credible": ["backend", "back end", "software", "engineer",
                           "api", "python", "server"],
              "wrong": ["frontend", "mobile", "salesforce", "sales", "qa",
                        "machine learning"]},
    "priya": {"stratum": "mobile specialist",
              "credible": ["mobile", "react native", "android", "ios", "app",
                           "software", "engineer", "developer"],
              "wrong": ["data engineer", "machine learning", "salesforce",
                        "sales", "qa", "embedded", "devops", "backend"]},
    "omar": {"stratum": "padded skills list",
             "credible": ["software", "engineer", "developer", "backend",
                          "back end", "python", "django", "web", "junior",
                          "associate", "full stack"],
             # The whole point of this persona. Sixty listed terms include
             # kubernetes, terraform, rust and swift; none is evidenced, and
             # none should become a career.
             "wrong": ["devops", "site reliability", "sre", "kubernetes",
                       "cloud architect", "mobile", "android", "ios",
                       "machine learning", "data scientist", "rust",
                       "salesforce", "sales", "qa"]},
    "yuki": {"stratum": "older stack then newer stack",
             "credible": ["software", "engineer", "developer", "full stack",
                          "frontend", "front end", "react", "typescript",
                          "node", "web"],
             # The older half is real history and the wrong search: the
             # career is the newer one.
             "wrong": ["php", "wordpress", "jquery", "salesforce", "sales",
                       "data engineer", "machine learning", "mobile", "qa"]},
}


def all_people():
    """people.py's thirteen plus the three strata it lacks."""
    from bench.people import PEOPLE
    merged = dict(PEOPLE)
    merged.update(EXTRA)
    return merged


def strata():
    """{slug: stratum} for every labelled persona."""
    return {slug: row["stratum"] for slug, row in ROLES.items()}


def demo():
    """Self-check. `python -m bench.eval_people` — offline."""
    people = all_people()
    assert len(people) == 16, len(people)
    # Every labelled persona exists, and every persona is labelled.
    assert set(ROLES) == set(people), set(ROLES) ^ set(people)
    # A label that called the same word both credible and wrong would make
    # every measurement meaningless.
    for slug, row in ROLES.items():
        overlap = set(row["credible"]) & set(row["wrong"])
        assert not overlap, (slug, overlap)
        assert row["credible"] and row["wrong"], slug
    # The new personas carry what render.py needs.
    for slug, person in EXTRA.items():
        for field in ("name", "headline", "skills", "employment", "education",
                      "projects", "certifications", "years_experience"):
            assert field in person, (slug, field)
    # omar is the padding stratum and has to actually be padded.
    assert len(EXTRA["omar"]["skills"]) >= 50, len(EXTRA["omar"]["skills"])
    print(f"eval_people demo ok — {len(people)} people, "
          f"{len(set(r['stratum'] for r in ROLES.values()))} strata")


if __name__ == "__main__":
    demo()

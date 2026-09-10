"""Ground truth for the parser benchmark: eight people, stated as facts.

The résumé PDFs are DERIVED from these dicts (see bench/render.py), which is
the point. Hand-written PDFs plus hand-written answers drift the moment
either is edited, and a benchmark whose answer key is wrong is worse than no
benchmark. Here the document cannot disagree with the truth because the
truth is what generated it.

Each person is chosen for a structural difficulty the plan named:

  ada        normal one-page, the control
  bhaskar    fresher — no employment history at all, only education
  chen       experienced — 11 years, six employers, promotions within one
  dmitri     unusual technologies — nothing a skills taxonomy has heard of
  esi        project-heavy — twelve projects, thin employment
  farida     dates everywhere — certifications, courses, publications, all dated
  gopal      tabular — skills and history that only make sense as a grid
  hana       long — three pages, and a career change midway

The fields go beyond what Sweep's RESPONSE_SCHEMA asks for (it wants
weights and keywords, not education or certifications). That is deliberate:
the question is whether a local model is production-worthy as a résumé
parser, and a model that cannot find an employer's name is not, whatever
Sweep happens to consume today.
"""

PEOPLE = {
    # ---------------------------------------------------------------- ada
    "ada": {
        "difficulty": "normal one-page — the control",
        "name": "Ada Okonkwo",
        "email": "ada.okonkwo@example.com",
        "phone": "+44 7700 900123",
        "location": "Manchester, UK",
        "headline": "Backend Engineer",
        "years_experience": 5,
        "titles": ["Backend Engineer", "Senior Backend Engineer"],
        "skills": ["python", "django", "postgresql", "redis", "docker",
                   "kubernetes", "celery", "graphql"],
        "employment": [
            {"company": "Fettle Health", "title": "Senior Backend Engineer",
             "location": "Manchester, UK", "start": "2023-04", "end": None,
             "bullets": [
                 "Split a Django monolith into four services behind GraphQL.",
                 "Cut p99 checkout latency from 1.9s to 340ms.",
             ]},
            {"company": "Northwind Logistics", "title": "Backend Engineer",
             "location": "Leeds, UK", "start": "2021-01", "end": "2023-03",
             "bullets": [
                 "Built the routing API serving 40k deliveries a day.",
                 "Moved batch jobs from cron to Celery with retries.",
             ]},
        ],
        "education": [
            {"institution": "University of Leeds", "degree": "BSc Computer Science",
             "start": "2017-09", "end": "2020-07"},
        ],
        "projects": [],
        "certifications": [],
    },
    # ------------------------------------------------------------ bhaskar
    "bhaskar": {
        "difficulty": "fresher — no employment history at all",
        "name": "Bhaskar Nair",
        "email": "bhaskar.nair@example.com",
        "phone": "+91 98200 11223",
        "location": "Kochi, India",
        "headline": "Final-year student, Computer Science",
        # The trap: a DOB and a graduation year are both on the page, and
        # neither is a career start. The rules-only parser read a DOB as one
        # and reported 23 years of experience.
        "dob": "2004-03-11",
        "years_experience": 0,
        "titles": ["Software Engineer", "Graduate Trainee"],
        "skills": ["java", "spring boot", "mysql", "git", "html", "css"],
        "employment": [
            {"company": "Zenith Softworks", "title": "Software Engineering Intern",
             "location": "Kochi, India", "start": "2025-05", "end": "2025-07",
             "bullets": ["Wrote CRUD endpoints for an internal leave tracker."]},
        ],
        "education": [
            {"institution": "Cochin University of Science and Technology",
             "degree": "B.Tech Computer Science", "start": "2022-08",
             "end": "2026-05"},
        ],
        "projects": [
            {"name": "Campus Swap", "stack": ["spring boot", "mysql"],
             "blurb": "A marketplace for second-hand textbooks. 300 users."},
        ],
        "certifications": [],
    },
    # -------------------------------------------------------------- chen
    "chen": {
        "difficulty": "experienced — 11 years, six employers, internal promotions",
        "name": "Chen Wei-Lin",
        "email": "wl.chen@example.com",
        "phone": "+65 8123 4567",
        "location": "Singapore",
        "headline": "Staff Platform Engineer",
        "years_experience": 11,
        "titles": ["Staff Platform Engineer", "Site Reliability Engineer",
                   "Infrastructure Engineer"],
        "skills": ["go", "terraform", "kubernetes", "aws", "prometheus",
                   "envoy", "kafka", "postgresql", "bazel"],
        "employment": [
            {"company": "Meridian Pay", "title": "Staff Platform Engineer",
             "location": "Singapore", "start": "2022-06", "end": None,
             "bullets": [
                 "Owns the multi-region control plane; 99.98% over two years.",
                 "Replaced hand-rolled deploys with Terraform and Bazel.",
             ]},
            # A promotion inside one employer: two rows, one company. Parsers
            # routinely report this as two jobs or as one, and both readings
            # change the count.
            {"company": "Meridian Pay", "title": "Senior Platform Engineer",
             "location": "Singapore", "start": "2020-02", "end": "2022-05",
             "bullets": ["Led the Kubernetes migration off bare EC2."]},
            {"company": "Halcyon Media", "title": "Site Reliability Engineer",
             "location": "Taipei, Taiwan", "start": "2017-09", "end": "2020-01",
             "bullets": ["Cut alert volume 70% by rewriting Prometheus rules."]},
            {"company": "Baystone Bank", "title": "Infrastructure Engineer",
             "location": "Taipei, Taiwan", "start": "2015-03", "end": "2017-08",
             "bullets": ["Ran the Kafka estate for settlement messaging."]},
        ],
        "education": [
            {"institution": "National Taiwan University",
             "degree": "MSc Computer Science", "start": "2013-09", "end": "2015-01"},
            {"institution": "National Taiwan University",
             "degree": "BSc Information Engineering", "start": "2009-09",
             "end": "2013-06"},
        ],
        "projects": [],
        "certifications": [
            {"name": "Certified Kubernetes Administrator", "issuer": "CNCF",
             "date": "2021-03"},
            {"name": "AWS Solutions Architect – Professional", "issuer": "AWS",
             "date": "2019-11"},
        ],
    },
    # ------------------------------------------------------------- dmitri
    "dmitri": {
        "difficulty": "unusual technologies — no taxonomy has these",
        "name": "Dmitri Sarkisyan",
        "email": "d.sarkisyan@example.com",
        "phone": "+374 55 123456",
        "location": "Yerevan, Armenia",
        "headline": "Systems Engineer, formal methods",
        "years_experience": 7,
        "titles": ["Systems Engineer", "Verification Engineer"],
        # A closed vocabulary built from scraped job ads has never seen most
        # of these, which is the ceiling on any dictionary parser.
        "skills": ["ocaml", "coq", "tla+", "lean 4", "zig", "seL4", "isabelle",
                   "rust", "spark ada", "frama-c"],
        "employment": [
            {"company": "Kestrel Systems", "title": "Verification Engineer",
             "location": "Yerevan, Armenia", "start": "2021-10", "end": None,
             "bullets": [
                 "Machine-checked proofs of the scheduler in Coq.",
                 "Specified the replication protocol in TLA+; found two races.",
             ]},
            {"company": "Aragats Robotics", "title": "Systems Engineer",
             "location": "Remote", "start": "2019-02", "end": "2021-09",
             "bullets": ["Rewrote the motion planner in Zig for determinism."]},
        ],
        "education": [
            {"institution": "Yerevan State University",
             "degree": "MSc Mathematics", "start": "2016-09", "end": "2018-06"},
        ],
        "projects": [
            {"name": "seL4 capability audit", "stack": ["seL4", "isabelle"],
             "blurb": "Mechanised audit of capability derivation."},
        ],
        "certifications": [],
    },
    # ---------------------------------------------------------------- esi
    "esi": {
        "difficulty": "project-heavy — twelve projects, thin employment",
        "name": "Esi Boateng",
        "email": "esi.boateng@example.com",
        "phone": "+233 24 000 1122",
        "location": "Accra, Ghana",
        "headline": "Full-Stack Developer",
        "years_experience": 3,
        "titles": ["Full Stack Developer", "React Developer"],
        "skills": ["react", "typescript", "node.js", "prisma", "postgresql",
                   "tailwind css", "trpc", "vitest"],
        "employment": [
            {"company": "Kola Digital", "title": "Full Stack Developer",
             "location": "Accra, Ghana", "start": "2023-02", "end": None,
             "bullets": ["Builds client dashboards on a shared Next.js base."]},
        ],
        "education": [
            {"institution": "Kwame Nkrumah University of Science and Technology",
             "degree": "BSc Computer Engineering", "start": "2018-09",
             "end": "2022-06"},
        ],
        "projects": [
            {"name": "Tro Tracker", "stack": ["react", "postgresql"],
             "blurb": "Live minibus positions for Accra commuters."},
            {"name": "Chophouse", "stack": ["node.js", "prisma"],
             "blurb": "Menu and order API used by nine restaurants."},
            {"name": "Adinkra UI", "stack": ["react", "tailwind css"],
             "blurb": "Component library on Ghanaian symbol motifs."},
            {"name": "Sika Split", "stack": ["typescript", "trpc"],
             "blurb": "Group expense settling with mobile money."},
            {"name": "Kente Weave", "stack": ["react"],
             "blurb": "Pattern generator; 4k monthly visitors."},
            {"name": "Farm Gate", "stack": ["node.js", "postgresql"],
             "blurb": "Price board for produce markets."},
            {"name": "Clinic Queue", "stack": ["react", "node.js"],
             "blurb": "SMS queue tickets for a district clinic."},
            {"name": "Exam Drill", "stack": ["typescript", "vitest"],
             "blurb": "WASSCE practice with spaced repetition."},
            {"name": "Solar Ledger", "stack": ["node.js"],
             "blurb": "Pay-as-you-go metering for home solar."},
            {"name": "Trotro Fare API", "stack": ["node.js"],
             "blurb": "Open fare data, 1.2m requests served."},
            {"name": "Bantaba", "stack": ["react", "trpc"],
             "blurb": "Threaded discussion for student unions."},
            {"name": "Papaye Bot", "stack": ["typescript"],
             "blurb": "WhatsApp ordering for a food chain."},
        ],
        "certifications": [],
    },
    # ------------------------------------------------------------- farida
    "farida": {
        "difficulty": "dates everywhere — courses, publications, certifications",
        "name": "Farida Haddad",
        "email": "f.haddad@example.com",
        "phone": "+212 6 12 34 56 78",
        "location": "Casablanca, Morocco",
        "headline": "Data Engineer",
        "years_experience": 6,
        "titles": ["Data Engineer", "Analytics Engineer"],
        "skills": ["python", "spark", "airflow", "dbt", "snowflake", "sql",
                   "kafka", "great expectations"],
        "employment": [
            {"company": "Atlas Retail Group", "title": "Data Engineer",
             "location": "Casablanca, Morocco", "start": "2022-01", "end": None,
             "bullets": ["Owns 200 dbt models and the Airflow estate."]},
            {"company": "Marchica Analytics", "title": "Analytics Engineer",
             "location": "Rabat, Morocco", "start": "2020-03", "end": "2021-12",
             "bullets": ["Built the Spark pipeline behind weekly forecasts."]},
        ],
        "education": [
            {"institution": "Université Hassan II", "degree": "MSc Statistics",
             "start": "2017-09", "end": "2019-07"},
        ],
        "projects": [],
        # Nine dated non-employment entries. A parser that treats every date
        # range as a job reports fifteen years of experience.
        "certifications": [
            {"name": "SnowPro Core", "issuer": "Snowflake", "date": "2023-06"},
            {"name": "dbt Analytics Engineering", "issuer": "dbt Labs",
             "date": "2022-09"},
            {"name": "Databricks Spark Developer", "issuer": "Databricks",
             "date": "2021-05"},
            {"name": "Airflow Fundamentals", "issuer": "Astronomer",
             "date": "2021-02"},
            {"name": "Deep Learning Specialisation", "issuer": "Coursera",
             "date": "2020-08"},
        ],
        "publications": [
            {"title": "Seasonal demand forecasting for Moroccan grocery retail",
             "venue": "JADS", "date": "2023-11"},
            {"title": "A note on data quality gates in dbt", "venue": "blog",
             "date": "2022-04"},
        ],
    },
    # -------------------------------------------------------------- gopal
    "gopal": {
        "difficulty": "tabular — skills and history only make sense as a grid",
        "name": "Gopal Krishnan",
        "email": "gopal.k@example.com",
        "phone": "+91 99400 55667",
        "location": "Chennai, India",
        "headline": "QA Automation Lead",
        "years_experience": 9,
        "titles": ["QA Automation Lead", "SDET", "Test Engineer"],
        "skills": ["selenium", "playwright", "pytest", "java", "testng",
                   "appium", "jenkins", "rest assured", "jmeter"],
        "employment": [
            {"company": "Tessellate Systems", "title": "QA Automation Lead",
             "location": "Chennai, India", "start": "2021-07", "end": None,
             "bullets": ["Runs a suite of 4,200 Playwright specs on Jenkins."]},
            {"company": "Vaigai Infotech", "title": "SDET",
             "location": "Coimbatore, India", "start": "2018-04", "end": "2021-06",
             "bullets": ["Introduced Appium coverage for the mobile app."]},
            {"company": "Kaveri Software", "title": "Test Engineer",
             "location": "Chennai, India", "start": "2016-08", "end": "2018-03",
             "bullets": ["Manual to Selenium migration for the billing suite."]},
        ],
        "education": [
            {"institution": "Anna University", "degree": "BE Information Technology",
             "start": "2012-08", "end": "2016-05"},
        ],
        "projects": [],
        "certifications": [
            {"name": "ISTQB Advanced Test Automation Engineer", "issuer": "ISTQB",
             "date": "2020-10"},
        ],
    },
    # --------------------------------------------------------------- hana
    "hana": {
        "difficulty": "long — multi-page, and a career change midway",
        "name": "Hana Fujimoto",
        "email": "hana.fujimoto@example.com",
        "phone": "+81 90 1234 5678",
        "location": "Osaka, Japan",
        "headline": "Machine Learning Engineer (formerly civil engineering)",
        # The career change is the difficulty: eight years of bridges before
        # four of ML. "Years of experience" has two defensible answers and a
        # parser should give the one matching the headline.
        "years_experience": 4,
        "titles": ["Machine Learning Engineer", "Computer Vision Engineer"],
        "skills": ["python", "pytorch", "onnx", "opencv", "cuda", "numpy",
                   "ray", "mlflow", "polars"],
        "employment": [
            {"company": "Sakura Vision", "title": "Machine Learning Engineer",
             "location": "Osaka, Japan", "start": "2023-04", "end": None,
             "bullets": [
                 "Ships defect-detection models to 40 factory edge boxes "
                 "across seven client sites in Kansai and Chubu.",
                 "Cut inference cost 60% by exporting to ONNX and "
                 "quantising to INT8, with a calibration set per line.",
                 "Owns the retraining loop: Ray for sweeps, MLflow for the "
                 "registry, and a shadow-deploy gate before promotion.",
                 "Rewrote the data layer on Polars; nightly aggregation "
                 "went from 50 minutes to under four.",
                 "Wrote the drift monitor that catches lens fouling before "
                 "the false-positive rate reaches the operators.",
             ]},
            {"company": "Rinku Robotics", "title": "Computer Vision Engineer",
             "location": "Osaka, Japan", "start": "2022-01", "end": "2023-03",
             "bullets": ["Built the bin-picking pose estimator in PyTorch."]},
            {"company": "Naniwa Data Lab", "title": "Machine Learning Intern",
             "location": "Osaka, Japan", "start": "2021-07", "end": "2021-12",
             "bullets": [
                 "Six months part-time alongside the bridge role, which is "
                 "how the transition was funded.",
                 "Built the baseline segmentation model later open-sourced "
                 "as the bridge crack dataset.",
                 "Presented weekly to a reading group of nine engineers on "
                 "papers from CVPR and MVA.",
             ]},
            {"company": "Kansai Bridge & Structure",
             "title": "Structural Engineer", "location": "Kobe, Japan",
             "start": "2014-04", "end": "2021-12",
             "bullets": [
                 "Load assessment for 60 prefectural road bridges under the "
                 "2016 revision of the Specifications for Highway Bridges.",
                 "Led the seismic retrofit design for the Muko River "
                 "crossing, a 340m continuous steel box girder.",
                 "Ran finite-element models in Abaqus for fatigue life on "
                 "orthotropic decks; published the calibration set.",
                 "Supervised four junior engineers and the drawing office "
                 "through two prefectural audits.",
                 "Wrote the Python tooling that started the career change: "
                 "a crack-width measurement pipeline over inspection photos "
                 "that replaced two days of manual tracing per bridge.",
                 "Standardised the inspection photo archive, 240k images, "
                 "which later became the training set at Rinku.",
                 "Represented the firm on the prefectural committee for "
                 "bridge asset management, 2019-2021.",
             ]},
        ],
        "education": [
            {"institution": "Kyoto University", "degree": "MEng Civil Engineering",
             "start": "2012-04", "end": "2014-03"},
            {"institution": "Osaka Institute of Technology",
             "degree": "Certificate in Applied Statistics",
             "start": "2021-04", "end": "2021-12"},
            {"institution": "Kyoto University", "degree": "BEng Civil Engineering",
             "start": "2008-04", "end": "2012-03"},
        ],
        "projects": [
            {"name": "Bridge crack segmentation", "stack": ["pytorch", "opencv"],
             "blurb": "Open dataset and baseline; 1.1k GitHub stars."},
        ],
        "certifications": [
            {"name": "Professional Engineer, Civil (Japan)", "issuer": "JSCE",
             "date": "2018-03"},
            {"name": "Deep Learning Specialisation", "issuer": "Coursera",
             "date": "2021-09"},
            {"name": "JDLA Deep Learning for Engineers", "issuer": "JDLA",
             "date": "2022-07"},
        ],
        "publications": [
            {"title": "Crack width estimation on orthotropic steel decks "
                      "from inspection photography",
             "venue": "Journal of Bridge Engineering", "date": "2021-06"},
            {"title": "Transfer learning from civil inspection imagery to "
                      "industrial defect detection",
             "venue": "MVA", "date": "2023-07"},
            {"title": "A calibration set for INT8 export of small "
                      "segmentation models",
             "venue": "workshop paper", "date": "2024-11"},
        ],
    },
}


def companies(person):
    """Distinct employers, in résumé order. Two rows at one company are one
    employer — the promotion case chen exists to test."""
    seen, out = set(), []
    for job in person["employment"]:
        if job["company"] not in seen:
            seen.add(job["company"])
            out.append(job["company"])
    return out


def truth(slug):
    """The answer key for one person, as a parser would be asked for it."""
    p = PEOPLE[slug]
    return {
        "name": p["name"],
        "email": p["email"],
        "location": p["location"],
        "years_experience": p["years_experience"],
        "titles": p["titles"],
        "skills": sorted(p["skills"]),
        "companies": companies(p),
        "employment_count": len(p["employment"]),
        "education": [e["degree"] for e in p["education"]],
        "institutions": [e["institution"] for e in p["education"]],
        "projects": [x["name"] for x in p["projects"]],
        "certifications": [c["name"] for c in p["certifications"]],
        "date_ranges": [(j["start"], j["end"]) for j in p["employment"]],
    }


def demo():
    assert set(PEOPLE) == {"ada", "bhaskar", "chen", "dmitri", "esi",
                           "farida", "gopal", "hana"}
    for slug, p in PEOPLE.items():
        t = truth(slug)
        assert t["name"] and t["skills"], slug
        assert p["years_experience"] >= 0, slug
        for job in p["employment"]:
            assert job["start"] < (job["end"] or "9999"), f"{slug} {job}"
        # Every difficulty is labelled, so a result table can say WHICH
        # structure a parser failed on rather than just that it failed.
        assert p["difficulty"], slug

    # The promotion case: four rows, three employers.
    assert len(PEOPLE["chen"]["employment"]) == 4
    assert len(companies(PEOPLE["chen"])) == 3, "one employer, two titles"
    # The fresher carries a DOB and no career start.
    assert PEOPLE["bhaskar"]["dob"] and truth("bhaskar")["years_experience"] == 0
    # The date trap: more dated non-jobs than jobs.
    f = PEOPLE["farida"]
    assert len(f["certifications"]) + len(f["publications"]) > len(f["employment"])
    print(f"people demo ok — {len(PEOPLE)} people, "
          f"{sum(len(p['employment']) for p in PEOPLE.values())} jobs, "
          f"{sum(len(p['projects']) for p in PEOPLE.values())} projects")


if __name__ == "__main__":
    demo()

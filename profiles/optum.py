"""Optum only — a referral-driven sweep of one employer's careers site.

    python scraper.py --profile optum --site optum      # -> output/optum/

Every other source is switched OFF ({} clears a section, see config._overlay),
so this run costs nothing and touches nothing but careers.unitedhealthgroup.com.

SCOPE: every software-engineering role Optum India posts that this résumé could
win, not just the full-stack ones. The reachable set is decided by the TITLE
gates below and ranked by SCORING; an off-stack stack (Java, .NET, Angular) is
a learning curve, so it costs a point or two, not a place in the list. What is
excluded is a different *career*, not a different framework: data engineering /
science / analytics, SRE, DevOps-as-a-job, cloud infra, security, networking,
QA/test automation, and IT ops all want a background this résumé doesn't have.
Measured on the live index (2026-07-30): 320 India cards, of which the old
full-stack-only gate kept 94 and this one keeps ~115.

Filter choices differ from the international-remote profiles on purpose, because
a referral changes what "reachable" means:

  remote_scopes = []   OFF. The default ["worldwide", "remote"] exists to throw
      out roles that are geo-locked away from India. Here the target IS Optum
      India (Noida / Gurgaon / Hyderabad / Chennai / Bengaluru / Pune), which is
      onsite or hybrid — leaving the filter on drops precisely the jobs a
      referral makes reachable.

  drop_excluded = False   Nothing is silently deleted; over-senior titles and
      over-experience demands take drop_penalty and sink instead. With a
      referral a "3-5 years" req is a stretch worth seeing, not a wall, so the
      decision belongs to the reader — the score already ranks it last. This is
      also why seniority is NOT in ATS_TITLE_EXCLUDE: an excluded title is gone,
      a penalized one is merely last.

  min_comp_usd = None   Optum's India requisitions never disclose pay, and this
      is a single-employer run, so there is nothing to compare against.
"""

# Paid actors, other company boards, and public feeds: all off.
SITES = {}
ATS_BOARDS = {}
FEEDS = {}

OPTUM = {"enabled": True}

# India, because that is where this candidate can work without sponsorship.
# A job whose location is blank is always kept (see scraper.location_allowed),
# and hints match on word boundaries — "india" as a substring also matches
# "Indianapolis, Indiana", which is a quarter of Optum's "India" cards.
LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "chennai", "pune", "mumbai", "kolkata",
    "ahmedabad", "coimbatore", "thiruvananthapuram", "trivandrum", "mohali",
]

# The whole-index sweep (config.OPTUM["keywords"] = [""]) returns every Optum
# job, so these two lists ARE the search. Substring-matched, lowercased.
#
# Vocabulary taken from the live index rather than invented: Optum India titles
# its AI work "AI/ML Engineer", "AI ML Engineer", "AI or ML Engineer" and
# "Associate AI/ML Engineer", and all four contain "ml engineer" — so one hint
# covers the family (43 rows on the 2026-07-30 sweep, every one of which the
# old gate missed). Likewise "software engineer" already covers "Software
# Engineering Lead", and "developer" covers "Application Developer".
ATS_TITLE_HINTS = [
    # Core software engineering
    "software engineer", "software development", "developer", "sde",
    "full stack", "fullstack", "full-stack",
    "frontend", "front end", "front-end", "backend", "back end", "back-end",
    "web developer", "mobile developer", "application developer",
    "api developer", "react", "node", "javascript", "typescript", "mern",
    # AI / ML — a CS degree specialised in AI/ML plus a trained-GAN project is a
    # real claim on these, and Optum India posts more of them than full-stack.
    "ml engineer", "ai engineer", "machine learning", "applied ai",
    "genai", "gen ai", "generative ai",
    # Platform / automation / developer productivity. Thin at Optum India today,
    # kept because they're the adjacent roles worth catching when they appear.
    "platform engineer", "automation engineer", "integration engineer",
    "developer productivity", "developer experience",
    "build engineer", "release engineer",
]

# Wins over the hints above. Every entry is a DIFFERENT career track, and every
# one was observed borrowing a software title on the live index — "Senior
# Software Engineer - Data Engineer, Python, snowflake, SQL, AWS", "Senior
# Software Engineer I - AWS Automation Testing", "Senior Software Engineering
# Lead - Servicenow developer". Deliberately tight: an over-broad entry deletes
# a good role invisibly, whereas a role that slips through is merely ranked
# low and still on the page. So "devops engineer" is here but bare "devops" is
# not — "Software Engineering Lead - .Net, Angular, SQL Server, Devops,
# Kubernetes" is a full-stack lead role that happens to name a pipeline.
ATS_TITLE_EXCLUDE = [
    # Data engineering / science / analytics
    "data engineer", "data engineering", "data scientist", "data science",
    "data analyst", "data analytics", "business intelligence", "power bi",
    "etl ", "database administrator", "dba",
    # SRE / DevOps-as-a-job / cloud infrastructure
    "site reliability", "sre", "devops engineer", "cloud engineer",
    "cloud architect", "infrastructure engineer", "i o engineer",
    # Security / networking / IT operations
    "information security", "security engineer", "cybersecurity", "cyber security",
    "network engineer", "system administrator", "systems management",
    "service desk", "help desk", "helpdesk", "technical support",
    "desktop support", "servicenow",
    # QA / test
    "quality engineer", "quality analyst", "quality assurance", "sdet",
    "test engineer", "test automation", "automation testing", "tester",
]

# Only the two keys that change; the rest of config.SCORING (frontend_terms,
# backend_terms, fullstack_bonus, seniority tiers, timezone penalty) is
# inherited. Both keys REPLACE their default wholesale — see config._overlay.
SCORING = {
    # Weight = how much of this résumé the term is, so the ranking still puts a
    # React/Node role first. Adjacent skills EARN points instead of the
    # off-stack ones LOSING them, which is what keeps a broad net ranked.
    "skill_weights": {
        # -- Résumé core: shipped in production, highest signal ---------------
        "node": 5, "node.js": 5, "express": 5,
        "react": 5, "react native": 5, "react.js": 5,
        "typescript": 5, "mongodb": 5,
        # -- Strong supporting skills ----------------------------------------
        "javascript": 3, "redis": 3, "socket.io": 3, "websocket": 3,
        "websockets": 3, "jwt": 3, "oauth": 3, "rest api": 3, "restful": 3,
        "mongoose": 3, "mysql": 3,
        # Python is on the résumé via the GAN project and is the language every
        # AI/ML posting here asks for, so it rates with the support tier.
        "python": 3,
        # -- Stated résumé strengths -----------------------------------------
        "firebase": 2, "fcm": 2, "concurrency": 2, "authentication": 2,
        "redux": 2, "expo": 2, "tailwind": 2, "next.js": 2, "jest": 2,
        "azure devops": 2, "ci/cd": 2,
        # -- Adjacent, learnable, and genuinely close to the résumé ----------
        # An API/service background transfers to these directly; they are worth
        # points because they mark a role as reachable, not because they're mine.
        "microservices": 2, "graphql": 2, "docker": 2, "api design": 2,
        "unit testing": 2, "sql": 1, "postgresql": 1, "kafka": 1,
        "kubernetes": 1, "aws": 1, "azure": 1, "gcp": 1, "git": 1,
        # -- AI / ML: degree specialisation + a trained GAN, so these are a
        # -- learning curve on top of a real base, not a career change.
        "machine learning": 2, "deep learning": 2, "tensorflow": 2,
        "pytorch": 2, "opencv": 1, "scikit-learn": 1,
        "llm": 2, "llms": 2, "genai": 2, "gen ai": 2, "generative ai": 2,
        "rag": 2, "langchain": 2, "langgraph": 2, "agentic": 2,
        "prompt engineering": 2, "openai": 1, "embeddings": 1,
        "vector database": 1, "fastapi": 1, "mcp": 1,
        # -- Baseline ---------------------------------------------------------
        "zod": 1, "react hook form": 1, "html": 1, "css": 1, "es6": 1,
        "agile": 1,
    },

    # Two different things live here, and only one of them is softened.
    #
    # A different STACK is a learning curve: Optum India runs on Java and .NET,
    # and a Java-plus-React requisition is a job this résumé can win, so those
    # terms cost a point or two — enough to rank below an equivalent Node role,
    # not enough to bury. (Java is absent entirely: neutral, neither rewarded
    # nor punished.) The default profile's -5/-6 on java/.net/spring existed to
    # keep an open-market sweep on-stack; inside one employer it would have
    # deleted most of the reachable list.
    #
    # A different DOMAIN is not a learning curve, and the title gate can't catch
    # it when the giveaway is in the JD instead of the title, so those terms keep
    # a real penalty. Salesforce/CRM stays hard-penalized even though it's on the
    # résumé: it's the work this search exists to leave.
    "penalty_terms": {
        # Off-stack, still applyable
        ".net": -2, "asp.net": -2, "c#": -2,
        "spring boot": -1, "spring mvc": -1, "angular": -1, "angularjs": -1,
        "php": -4, "laravel": -4,
        # Salesforce / CRM — hard down-rank, on purpose
        "salesforce": -12, "apex": -12, "lwc": -12,
        "lightning web component": -12, "crm developer": -12, "crm": -6,
        # Excluded domains leaking in through the JD of a software-titled role
        "etl": -6, "informatica": -6, "spark": -5, "pyspark": -5, "hadoop": -5,
        "snowflake": -5, "databricks": -4, "tableau": -5, "power bi": -5,
        "data warehouse": -4, "data pipeline": -3,
        "terraform": -3, "sccm": -8, "intune": -8, "active directory": -6,
        "penetration testing": -8, "siem": -8, "vulnerability management": -6,
        "selenium": -6, "cucumber": -5, "manual testing": -8,
        "service desk": -8, "itil": -6, "cisco": -8, "firewall": -6,
    },
}

SETTINGS = {
    "remote_scopes": [],          # see docstring — must be off for an India sweep
    "drop_excluded": False,       # penalize seniority/experience, never delete
    "max_experience_years": 3,
    # Every Optum requisition states a total AND a per-skill figure, so the
    # default "min" read "8+ years of total software engineering experience,
    # including 2+ years hands-on in AI/ML" as a 2-year job and ranked it #1 of
    # 63. See scraper._required_experience_floor.
    "experience_aggregate": "max",
    "min_comp_usd": None,
    # Kept at None deliberately: with drop_excluded=False a floor here would
    # silently delete exactly the over-senior / over-experience rows the profile
    # promises to merely rank last. The CSV is the full ranked list; the console
    # top-N below is the "strongest matches" view.
    "min_score": None,
    "max_age_days": 30,           # liveness is verified per JD; this bounds staleness
    "drop_undated": False,
    "top_n_console": 30,
}

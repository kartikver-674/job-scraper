"""Ten household-name employers at peak hiring, India, ranked by résumé fit.

    python scraper.py --profile bigtech --site free      # -> output/bigtech/

Free and stdlib-only: every source here is the employer's own careers platform,
so this run costs nothing and touches no Apify actor. Five of the ten asked-for
employers are reachable that way; the other five are not, and the reasons are
recorded in sources/enterprise.py rather than quietly dropped:

    reachable   Amazon, JPMorgan Chase, Oracle, Accenture, SAP
    not free    Microsoft (Eightfold, 403), IBM (client-side fetch),
                Capgemini (API paths 404), Siemens (JS-rendered),
                Deloitte (US-only portal)

SCOPE: software engineering roles this résumé could actually win. Same principle
as profiles/optum.py — an off-stack STACK (Java, .NET, Angular) is a learning
curve and costs a point or two; a different CAREER is excluded outright.

What differs from the Optum profile, and why it has to:

  ENTERPRISE-PACKAGE EXCLUSIONS. Accenture, SAP, Oracle and Capgemini post an
      enormous volume of ERP/package work under software titles — "SAP ABAP
      Developer", "Siebel Developer", "Pega Developer", "Guidewire Developer",
      "Mainframe COBOL Developer". Each is a different career with its own
      labour market, and none of them is reachable from React/Node in a
      reasonable learning curve. The Optum list never needed these because a
      health insurer doesn't post them; here they would dominate the results.

  AI/ML VOCABULARY. The Optum sweep exposed a real scoring blind spot: a
      grade-25 AI/ML requisition scored 7 because its JD named Hugging Face,
      transformers and NLP — none of which the weights table knew — while a
      conventional role naming React scored 40+. Those terms are added below, so
      an AI/ML role is now ranked on what it actually asks for. This matters more
      here than at Optum: Amazon and JPMorgan post far more of them.

  min_score = 5. Unlike the Optum run, this is not a single-employer list worth
      reading end to end — five boards return thousands of cards. A floor keeps
      the CSV to roles with at least some résumé overlap. It is low on purpose:
      ranking, not deletion, is still doing the work.
"""

# Paid actors, rented ATS boards, public feeds: all off.
SITES = {}
ATS_BOARDS = {}
FEEDS = {}
# Inert unless the branch that carries the Optum source is merged in — OPTUM is
# not a config section on main, and _overlay only walks OVERLAYABLE. Kept so
# this profile stays a free, enterprise-only sweep wherever it is run.
OPTUM = {"enabled": False}

ENTERPRISE = {
    "enabled": True,
    "employers": ["amazon", "jpmorgan", "oracle", "accenture", "sap"],
    # One empty query sweeps a whole board and lets the title/location gates
    # narrow it — cheaper AND more complete than guessing keywords (the lesson
    # from Optum, where "developer" matched 5,787 of 5,872 jobs).
    #
    # The exception is Oracle Recruiting Cloud: JPMorgan's board is 7,379 reqs
    # and pages 200 at a time, so its server-side keyword filter is the one that
    # earns its keep (7,379 -> 1,535). Keywords apply to every employer, and for
    # the other three the cost of a second query is a few extra listing pages.
    "keywords": ["", "software engineer", "full stack"],
    "max_pages": 8,
    "verify_live": True,
}

# India, because that is where this candidate can work without sponsorship.
# Word-boundary matched in scraper.location_allowed, which is what stops "india"
# from also matching "Indianapolis, Indiana".
LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "chennai", "pune", "mumbai", "kolkata",
    "ahmedabad", "coimbatore", "thiruvananthapuram", "trivandrum", "mohali",
    "jaipur", "indore", "nagpur", "kochi", "cochin",
    # Amazon normalizes to a 3-letter country code ("Hyderabad, Telangana, IND")
    # and occasionally posts "Virtual, IND" with no city. Safe only because the
    # match is word-boundary anchored: "ind" does NOT match "Indianapolis".
    "ind",
]

ATS_TITLE_HINTS = [
    # Core software engineering
    "software engineer", "software development", "developer", "sde",
    "software dev engineer", "member of technical staff",
    "full stack", "fullstack", "full-stack",
    "frontend", "front end", "front-end", "backend", "back end", "back-end",
    "web developer", "mobile developer", "application developer",
    "api developer", "react", "node", "javascript", "typescript", "mern",
    # AI / ML — a CS degree specialised in AI/ML plus a trained GAN is a real
    # claim on these, and all five employers are hiring them hard right now.
    "ml engineer", "ai engineer", "machine learning", "applied ai",
    "genai", "gen ai", "generative ai",
    # Platform / automation / developer productivity
    "platform engineer", "automation engineer", "integration engineer",
    "developer productivity", "developer experience",
    "build engineer", "release engineer",
]

# Wins over the hints above (scraper.is_dev_title checks this first). Every
# entry is a DIFFERENT career, not a different framework.
ATS_TITLE_EXCLUDE = [
    # Data engineering / science / analytics
    "data engineer", "data engineering", "data scientist", "data science",
    "data analyst", "data analytics", "business intelligence", "power bi",
    "etl ", "database administrator", "dba",
    # Amazon/JPMC post these under science titles; they want a PhD and research
    # publications, which is a different track from applied AI engineering.
    "applied scientist", "research scientist", "research engineer",
    "quantitative research", "quant developer",
    # SRE / DevOps-as-a-job / cloud infrastructure
    "site reliability", "sre", "devops engineer", "cloud engineer",
    "cloud architect", "infrastructure engineer", "network engineer",
    # Security / IT operations
    "information security", "security engineer", "cybersecurity",
    "cyber security", "system administrator", "systems management",
    "service desk", "help desk", "helpdesk", "technical support",
    "desktop support", "servicenow",
    # QA / test
    "quality engineer", "quality analyst", "quality assurance", "sdet",
    "test engineer", "test automation", "automation testing", "tester",
    # Hardware / embedded / silicon — Amazon posts a lot of these
    "hardware engineer", "asic", "silicon", "firmware", "embedded",
    "rf engineer", "validation engineer",
    # Enterprise packages. The reason this list is longer than Optum's: these
    # five employers are where ERP work lives, and it borrows software titles
    # constantly ("SAP ABAP Developer", "Pega Developer", "Siebel Developer").
    "abap", "sap bw", "sap basis", "sap fico", "sap mm", "sap sd", "s/4hana",
    "peoplesoft", "siebel", "pega", "guidewire", "duck creek", "murex",
    "mainframe", "cobol", "as/400", "sharepoint", "salesforce",
    "oracle apps", "oracle ebs", "oracle fusion", "workday", "informatica",
    "netsuite", "microstrategy", "cognos", "tibco", "webmethods",
]

SCORING = {
    # Weight = how much of this résumé the term is. Adjacent skills EARN points
    # instead of off-stack ones LOSING them, which is what keeps a broad net
    # ranked rather than merely wide.
    "skill_weights": {
        # -- Résumé core: shipped in production, highest signal ---------------
        "node": 5, "node.js": 5, "express": 5,
        "react": 5, "react native": 5, "react.js": 5,
        "typescript": 5, "mongodb": 5,
        # -- Strong supporting skills ----------------------------------------
        "javascript": 3, "redis": 3, "socket.io": 3, "websocket": 3,
        "websockets": 3, "jwt": 3, "oauth": 3, "rest api": 3, "restful": 3,
        "mongoose": 3, "mysql": 3, "python": 3,
        # -- Stated résumé strengths -----------------------------------------
        "firebase": 2, "fcm": 2, "concurrency": 2, "authentication": 2,
        "redux": 2, "expo": 2, "tailwind": 2, "next.js": 2, "jest": 2,
        "azure devops": 2, "ci/cd": 2, "cloudinary": 1, "postman": 1,
        # -- Adjacent, learnable, genuinely close to the résumé --------------
        "microservices": 2, "graphql": 2, "docker": 2, "api design": 2,
        "unit testing": 2, "sql": 1, "postgresql": 1, "kafka": 1,
        "kubernetes": 1, "aws": 1, "azure": 1, "gcp": 1, "git": 1,
        "agile": 1, "scrum": 1,
        # -- AI / ML. Degree specialisation + a trained GAN, so a learning
        # -- curve on a real base. The second row is the blind spot the Optum
        # -- sweep exposed: these are what an LLM job description actually
        # -- says, and none of them used to score at all.
        "machine learning": 2, "deep learning": 2, "tensorflow": 2,
        "pytorch": 2, "opencv": 1, "scikit-learn": 1, "keras": 1,
        "llm": 2, "llms": 2, "genai": 2, "gen ai": 2, "generative ai": 2,
        "rag": 2, "langchain": 2, "langgraph": 2, "agentic": 2,
        "prompt engineering": 2, "openai": 1, "embeddings": 1,
        "vector database": 1, "fastapi": 1, "mcp": 1,
        "hugging face": 2, "huggingface": 2, "transformers": 2, "nlp": 2,
        "natural language processing": 2, "fine-tuning": 1, "fine tuning": 1,
        "ai agent": 2, "ai agents": 2, "llamaindex": 1, "semantic kernel": 1,
        # -- Baseline ---------------------------------------------------------
        "zod": 1, "react hook form": 1, "html": 1, "css": 1, "es6": 1,
    },

    # A different STACK is a learning curve and costs a point or two; a
    # different DOMAIN leaking in through the JD of a software-titled role keeps
    # a real penalty. Java is deliberately absent — at these five employers it
    # is the house language, and penalizing it (as the default profile does at
    # -5) would bury most of the reachable list.
    "penalty_terms": {
        # Off-stack, still applyable
        ".net": -2, "asp.net": -2, "c#": -2,
        "spring boot": -1, "spring mvc": -1, "angular": -1, "angularjs": -1,
        "c++": -2, "golang": -1, "scala": -2, "kotlin": -1, "ruby": -3,
        "php": -4, "laravel": -4, "perl": -4,
        # Salesforce / CRM — on the résumé, and precisely the work this search
        # exists to leave. Hard down-rank, on purpose.
        "salesforce": -12, "apex": -12, "lwc": -12,
        "lightning web component": -12, "crm developer": -12, "crm": -6,
        # Excluded domains leaking in through a software-titled JD
        "etl": -6, "informatica": -6, "spark": -5, "pyspark": -5, "hadoop": -5,
        "snowflake": -5, "databricks": -4, "tableau": -5, "power bi": -5,
        "data warehouse": -4, "data pipeline": -3,
        "terraform": -3, "ansible": -3, "puppet": -4, "chef": -3,
        "penetration testing": -8, "siem": -8, "vulnerability management": -6,
        "selenium": -6, "cucumber": -5, "manual testing": -8,
        "service desk": -8, "itil": -6, "cisco": -8, "firewall": -6,
        # Enterprise packages, in case one slips past the title gate
        "abap": -10, "peoplesoft": -10, "siebel": -10, "pega": -8,
        "guidewire": -10, "mainframe": -10, "cobol": -10,
    },
}

SETTINGS = {
    "remote_scopes": [],          # India onsite/hybrid is the target, not remote
    "drop_excluded": False,       # penalize seniority/over-experience, never delete
    "max_experience_years": 3,
    # These JDs state a total AND a per-skill figure, so "min" reads "8+ years
    # total, including 2+ years in AI/ML" as a 2-year job. See
    # scraper._required_experience_floor.
    "experience_aggregate": "max",
    "min_comp_usd": None,         # India requisitions don't disclose pay
    "min_score": 5,               # see docstring — a floor, not a filter
    "max_age_days": 30,
    "drop_undated": False,
    "top_n_console": 40,
}

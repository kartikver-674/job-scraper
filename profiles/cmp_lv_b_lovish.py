"""cmp_lv_b_lovish — generated from a résumé by auto-apply/make_profile.py.

software engineering

    python scraper.py --profile cmp_lv_b_lovish --dry-run   # cost check, free
    python scraper.py --profile cmp_lv_b_lovish --yes

HOW THE MODEL READ THIS RÉSUMÉ
local pipeline; title_exclude intentionally empty

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

Spending stops at $0.25: max_spend_usd below is re-checked against the account after every search, and the sweep stops there even with searches left. This file sets no SITES, so it inherits config.py's — LinkedIn and Indeed on, Naukri off (it has never returned a row and costs ~$0.50 per run minimum). Run --dry-run first and read the run count. To narrow it, copy the SITES block from profiles/kartik_reachable.py. LinkedIn searches the geoIds in SITES, NOT the locations above, so city-level LinkedIn needs that block too.

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

FEEDS = {
    "himalayas": {
        "enabled": True,
        "pages": 10,
        "queries": [
            'full stack flutter developer',
            'salesforce developer',
            'full stack ai engineer',
            'java fullstack',
            'software engineer iii-java/ci-cd',
            'typescript developer',
            'sde ii, amazon now',
            'back end engineer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'full stack flutter developer',
        'salesforce developer',
        'full stack ai engineer',
        'java fullstack',
        'software engineer iii-java/ci-cd',
        'typescript developer',
        'sde ii, amazon now',
        'back end engineer',
    ],
    "experience_years": 2,
    "locations": [
        'Bengaluru',
    ],
    "salary_min": None,
    "max_results": 10,
}

SETTINGS = {
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": 5,
    "min_comp_usd": 10000,
    "max_spend_usd": 0.25,
    "max_age_days": 14,
    "remote_scopes": [],
}

SCORING = {
    "skill_weights": {
        '.net worker services': 3,
        'android studio': 3,
        'apex': 3,
        'aura': 3,
        'batch apex': 3,
        'c#': 3,
        'dart': 3,
        'docker': 2,
        'eclipse': 3,
        'firebase': 3,
        'flutter': 3,
        'git': 2,
        'java': 2,
        'javascript': 2,
        'lightning web components': 3,
        'lwc': 2,
        'mysql': 3,
        'node.js': 2,
        'postgresql': 3,
        'postman': 3,
        'python': 2,
        'queueable apex': 3,
        'react': 2,
        'rest': 3,
        'rest apis': 2,
        'salesforce': 2,
        'schedulable apex': 3,
        'soql': 3,
        'sosl': 3,
        'sql': 2,
        'visualforce': 3,
        'vs code': 3,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {},

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [],
    "backend_terms": [],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [],
    "fullstack_bonus": 0,

    # From --exclude-levels. soft_drop_terms is left to config.py on purpose:
    # "senior" routinely means 3-4 years, so it down-ranks instead of dropping.
    "hard_drop_terms": [
        'intern',
        'fresher',
        'junior',
    ],
}

# The gate on every FREE source: a company board or feed returns its whole
# catalogue and scraper.is_dev_title() drops any title matching none of these
# BEFORE scoring, so a fragment missing here is inventory nobody sees. This is
# the résumé's own vocabulary UNIONED with config.py's generic software floor,
# so it can only ever widen the search.
ATS_TITLE_HINTS = [
    '.net',
    'ai data',
    'ai engineer',
    'ai native stack',
    'ai stack',
    'android',
    'api developer',
    'api engineer',
    'app developer',
    'application developer',
    'application security',
    'applied ai',
    'associate cloud integration engineer',
    'associate- ai data engineer(retail)',
    'automation engineer',
    'back end',
    'back end developer',
    'back end engineer',
    'back-end',
    'backend',
    'backend developer node.js',
    'build engineer',
    'c#',
    'cloud engineer',
    'developer',
    'developer experience',
    'developer productivity',
    'developer stack',
    'development engineer',
    'devops',
    'django',
    'engineer fullstack',
    'engineer java',
    'engineer node.js',
    'engineering manager',
    'flutter',
    'front end',
    'front-end',
    'frontend',
    'full stack',
    'full stack ai engineer',
    'full stack web developer',
    'full-stack',
    'fullstack',
    'gen ai',
    'genai',
    'generative ai',
    'golang',
    'infrastructure engineer',
    'integration engineer',
    'ios ',
    'ios developer',
    'ios engineer',
    'java ',
    'java fullstack',
    'java stack',
    'javascript',
    'js developer',
    'llm engineer',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'mid level',
    'ml engineer',
    'mobile app developer',
    'mobile developer',
    'mobile engineer',
    'node',
    'node js',
    'node.js developer',
    'php',
    'platform engineer',
    'principal engineer',
    'programmer',
    'python',
    'python ai',
    'python ai engineer',
    'qa engineer',
    'quality engineer',
    'rails',
    'react',
    'react native',
    'react node.js',
    'release engineer',
    'ruby',
    'salesforce cpq',
    'salesforce cpq engineer 3',
    'salesforce developer',
    'sde',
    'sde ii',
    'sde ii, amazon now',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software engineer',
    'software engineer grad',
    'software engineer iii',
    'software engineer iii-java/ci-cd',
    'solutions architect',
    'spring boot',
    'sre ',
    'stack ai',
    'stack developer ai',
    'stack developer node.js',
    'stack engineer',
    'stack web developer',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'test engineer',
    'typescript',
    'typescript developer',
    'web developer',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = []

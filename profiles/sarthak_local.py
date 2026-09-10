"""sarthak_local — generated from a résumé by auto-apply/make_profile.py.

software engineering

    python scraper.py --profile sarthak_local --dry-run   # cost check, free
    python scraper.py --profile sarthak_local --yes

HOW THE MODEL READ THIS RÉSUMÉ
generated locally; title_exclude intentionally empty

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

Spending stops at $1.00: max_spend_usd below is re-checked against the account after every search, and the sweep stops there even with searches left. This file sets no SITES, so it inherits config.py's — LinkedIn and Indeed on, Naukri off (it has never returned a row and costs ~$0.50 per run minimum). Run --dry-run first and read the run count. To narrow it, copy the SITES block from profiles/kartik_reachable.py. LinkedIn searches the geoIds in SITES, NOT the locations above, so city-level LinkedIn needs that block too.

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

FEEDS = {
    "himalayas": {
        "enabled": True,
        "pages": 10,
        "queries": [
            'software engineer onsite',
            'mern stack developer',
            'react native engineer',
            'react js developer',
            'react native developer',
            'node.js developer',
            'full stack ai engineer',
            'application engineer',
            'frontend developer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'software engineer onsite',
        'mern stack developer',
        'react native engineer',
        'react js developer',
        'react native developer',
        'node.js developer',
        'full stack ai engineer',
        'application engineer',
        'frontend developer',
    ],
    "experience_years": 1,
    "locations": [
        'Bengaluru',
        'Delhi',
    ],
    "salary_min": None,
    "max_results": 10,
}

SETTINGS = {
    "remote_scopes": [],
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": 4,
    "min_comp_usd": 10000,
    "max_spend_usd": 1.0,
    "max_age_days": 14,
}

SCORING = {
    "skill_weights": {
        'aws': 2,
        'axios': 4,
        'azure': 2,
        'azure data studio': 3,
        'bazel': 4,
        'context api': 3,
        'css3': 3,
        'docker': 2,
        'git': 2,
        'github': 3,
        'gradle': 4,
        'hibernate (jpa)': 3,
        'html5': 3,
        'java': 2,
        'javascript': 2,
        'maven': 4,
        'mysql': 3,
        'postgresql': 3,
        'postman': 3,
        'react': 2,
        'react native': 3,
        'react navigation': 4,
        'react.js': 3,
        'redux': 3,
        'redux thunk': 3,
        'redux toolkit': 3,
        'restful apis': 3,
        'rtk query': 3,
        'salesforce': 2,
        'spring boot': 3,
        'spring data jpa': 4,
        'spring framework': 3,
        'spring security': 4,
        'sql': 2,
        'tailwind css': 4,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {},

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [
        'react',
        'redux',
        'javascript',
        'typescript',
        'restful apis',
        'axios',
        'postman',
        'html5',
    ],
    "backend_terms": [
        'spring boot',
        'hibernate',
        'jpa',
        'spring data jpa',
        'spring security',
        'java',
        'mysql',
        'postgresql',
    ],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [
        'software engineer onsite',
        'mern stack developer',
        'react native engineer',
        'react js developer',
        'react native developer',
        'node.js developer',
        'full stack ai engineer',
        'application engineer',
        'frontend developer',
    ],
    "fullstack_bonus": 6,

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
    'ai engineer',
    'ai stack',
    'android',
    'api developer',
    'api engineer',
    'app developer',
    'application developer',
    'application engineer',
    'application security',
    'applied ai',
    'associate stack',
    'automation engineer',
    'back end',
    'back-end',
    'backend',
    'build engineer',
    'c#',
    'cloud engineer',
    'developer',
    'developer experience',
    'developer productivity',
    'developer react native',
    'development engineer',
    'devops',
    'django',
    'engineer front end',
    'engineer fullstack',
    'engineer iii',
    'engineer mobile',
    'engineer react native',
    'engineering manager',
    'flutter',
    'front end',
    'front end developer',
    'front end engineer',
    'front-end',
    'frontend',
    'frontend developer',
    'frontend engineer',
    'full stack',
    'full stack ai engineer',
    'full stack web developer',
    'full-stack',
    'fullstack',
    'fullstack developer',
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
    'javascript',
    'js developer',
    'llm engineer',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'mern stack developer',
    'ml engineer',
    'mobile app developer',
    'mobile application developer',
    'mobile developer',
    'mobile engineer',
    'native developer',
    'node',
    'node js',
    'node.js developer',
    'php',
    'platform engineer',
    'principal engineer',
    'programmer',
    'python',
    'qa engineer',
    'quality engineer',
    'rails',
    'react',
    'react developer',
    'react js',
    'react js developer',
    'react native',
    'react native developer',
    'react native engineer',
    'react react',
    'release engineer',
    'ruby',
    'sde',
    'sde ii',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software development engineer iii',
    'software engineer',
    'software engineer frontend',
    'software engineer web',
    'solutions architect',
    'spring boot',
    'sre ',
    'stack ai',
    'stack developer ai',
    'stack developer node.js',
    'stack web developer',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'test engineer',
    'typescript',
    'web developer',
    'web mobile',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = []

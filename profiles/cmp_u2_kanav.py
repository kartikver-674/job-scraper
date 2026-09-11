"""cmp_u2_kanav — generated from a résumé by auto-apply/make_profile.py.

software engineering

    python scraper.py --profile cmp_u2_kanav --dry-run   # cost check, free
    python scraper.py --profile cmp_u2_kanav --yes

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
            'software developer',
            'solutions developer',
            'react native developer',
            'react native engineer',
            'mern stack developer',
            'software engineer iii-java/ci-cd',
            'node.js developer',
            'node js',
            'software development engineer ii',
            'sde ii, amazon now',
            'applied ai backend engineer',
            'typescript developer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'software developer',
        'solutions developer',
        'react native developer',
        'react native engineer',
        'mern stack developer',
        'software engineer iii-java/ci-cd',
        'node.js developer',
        'node js',
        'software development engineer ii',
        'sde ii, amazon now',
        'applied ai backend engineer',
        'typescript developer',
    ],
    "experience_years": 4,
    "locations": [
        'Bengaluru',
    ],
    "salary_min": None,
    "max_results": 10,
}

SETTINGS = {
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": 7,
    "min_comp_usd": 10000,
    "max_spend_usd": 0.25,
    "max_age_days": 14,
    "remote_scopes": [],
}

SCORING = {
    "skill_weights": {
        'ai assisted': 4,
        'ai-assisted': 2,
        'android': 3,
        'android studio': 3,
        'app center': 4,
        'authentication': 3,
        'bundle size': 3,
        'c++': 2,
        'ci/cd': 2,
        'claude code': 3,
        'crm': 2,
        'cross platform': 4,
        'cross-platform': 3,
        'docker': 2,
        'express': 3,
        'field sales': 4,
        'firebase': 3,
        'firebase authentication': 3,
        'flatlist': 4,
        'git': 2,
        'github': 3,
        'github copilot': 3,
        'google play console': 4,
        'ios': 3,
        'javascript (es6+)': 3,
        'llm': 2,
        'llms': 2,
        'mobile architecture': 3,
        'modularization': 3,
        'mongodb': 2,
        'node.js': 2,
        'offline-first': 3,
        'performance optimization': 2,
        'react native': 3,
        'react-native': 4,
        'react.js': 3,
        'rest api': 3,
        'rest apis': 2,
        'reusable components': 3,
        'state management': 3,
        'virtualization': 4,
        'xcode': 3,
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
    'applied ai backend engineer',
    'associate- ai data engineer(retail)',
    'automation engineer',
    'back end',
    'back end developer',
    'back-end',
    'backend',
    'backend engineer node.js',
    'build engineer',
    'c#',
    'cloud engineer',
    'developer',
    'developer experience',
    'developer node.js',
    'developer productivity',
    'developer react native',
    'development engineer',
    'development engineer i',
    'devops',
    'django',
    'engineer fullstack',
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
    'i backend',
    'ii java',
    'infrastructure engineer',
    'integration engineer',
    'ios ',
    'ios developer',
    'ios engineer',
    'java ',
    'java stack',
    'javascript',
    'javascript developer',
    'llm engineer',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'mern stack developer',
    'ml engineer',
    'mobile app developer',
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
    'react native',
    'react native developer',
    'react native engineer',
    'release engineer',
    'ruby',
    'sde',
    'sde ii',
    'sde ii, amazon now',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software development engineer ii',
    'software engineer',
    'software engineer grad',
    'software engineer iii',
    'software engineer iii-java/ci-cd',
    'solutions architect',
    'spring boot',
    'sre ',
    'stack ai engineer',
    'stack developer ai',
    'stack product',
    'stack web developer',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'test engineer',
    'typescript',
    'typescript developer',
    'ui developer',
    'ui developer_offshore',
    'web developer',
    'web mobile',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = []

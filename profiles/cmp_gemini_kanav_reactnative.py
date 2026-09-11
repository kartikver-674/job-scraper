"""cmp_gemini_kanav_reactnative — generated from a résumé by auto-apply/make_profile.py.

React Native and cross-platform mobile engineer with 4+ years of experience building iOS and Android applications, optimizing performance, and utilizing Node.js/MongoDB backends.

    python scraper.py --profile cmp_gemini_kanav_reactnative --dry-run   # cost check, free
    python scraper.py --profile cmp_gemini_kanav_reactnative --yes

HOW THE MODEL READ THIS RÉSUMÉ
Domain split configured between Mobile platform/ecosystem (half A) and Web/React core tech stack (half B). Highly specific mobile dev tooling like App Center, FlatList, Xcode, and Android Studio received maximum discriminative weights (5), whereas general craft concepts received lower weights (1).

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
            'React Native Developer',
            'React Native Engineer',
            'Mobile Developer',
            'Mobile Engineer',
            'Cross-Platform Mobile Developer',
            'iOS React Native Developer',
            'Android React Native Engineer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'React Native Developer',
        'React Native Engineer',
        'Mobile Developer',
        'Mobile Engineer',
        'Cross-Platform Mobile Developer',
        'iOS React Native Developer',
        'Android React Native Engineer',
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
        'react native': 4,
        'react-native': 5,
        'reactnative': 5,
        'app center': 5,
        'appcenter': 5,
        'android studio': 4,
        'xcode': 4,
        'google play console': 5,
        'flatlist': 5,
        'offline-first': 4,
        'offline first': 4,
        'ios development': 4,
        'android development': 4,
        'cross-platform': 3,
        'firebase auth': 4,
        'firebase authentication': 4,
        'firebase': 3,
        'react.js': 3,
        'react': 2,
        'reactjs': 3,
        'node.js': 2,
        'node': 2,
        'nodejs': 3,
        'express': 3,
        'express.js': 3,
        'expressjs': 3,
        'mongodb': 2,
        'mongo': 3,
        'cloudinary': 3,
        'claude code': 3,
        'github copilot': 3,
        'copilot': 3,
        'mobile architecture': 3,
        'javascript': 1,
        'es6': 3,
        'es6+': 2,
        'c++': 2,
        'cpp': 2,
        'rest api': 3,
        'rest apis': 2,
        'docker': 2,
        'git': 2,
        'github': 2,
        'state management': 1,
        'modularization': 1,
        'performance optimization': 1,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {
        'internship': -5,
        'angular': -3,
        'vue': -3,
        'php': -3,
        'laravel': -3,
        'java enterprise': -3,
        'spring boot': -3,
    },

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [
        'react native',
        'react-native',
        'mobile app',
        'ios',
        'android',
        'cross-platform',
    ],
    "backend_terms": [
        'javascript',
        'typescript',
        'react',
        'node',
        'express',
        'mobile architecture',
        'performance optimization',
    ],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [
        'react native developer',
        'react native engineer',
        'react native lead',
        'react native architect',
        'mobile react native developer',
        'mobile react native engineer',
        'cross-platform mobile developer',
        'cross-platform mobile engineer',
    ],
    "fullstack_bonus": 5,

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
    'android',
    'android developer',
    'android engineer',
    'api developer',
    'api engineer',
    'app developer',
    'app engineer',
    'application developer',
    'application security',
    'applied ai',
    'automation engineer',
    'back end',
    'back-end',
    'backend',
    'build engineer',
    'c#',
    'cloud engineer',
    'cross platform',
    'cross-platform',
    'developer',
    'developer experience',
    'developer productivity',
    'development engineer',
    'devops',
    'django',
    'engineering manager',
    'flutter',
    'front end',
    'front-end',
    'frontend',
    'frontend developer',
    'frontend engineer',
    'full stack',
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
    'javascript',
    'llm engineer',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'ml engineer',
    'mobile',
    'mobile app developer',
    'mobile developer',
    'mobile engineer',
    'node',
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
    'react engineer',
    'react native',
    'react-native',
    'release engineer',
    'ruby',
    'sde',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software developer',
    'software development',
    'software engineer',
    'solutions architect',
    'solutions developer',
    'spring boot',
    'sre ',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'test engineer',
    'typescript',
    'web developer',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = [
    'designer',
    'fresher',
    'intern',
    'internship',
    'junior',
    'qa',
    'recruiter',
    'sales',
    'tester',
]

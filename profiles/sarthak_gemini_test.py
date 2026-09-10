"""sarthak_gemini_test — generated from a résumé by auto-apply/make_profile.py.

Mobile & Full-Stack Engineer with hands-on experience in React Native, React.js, Java Spring Boot, and Salesforce Mobile SDK integration.

    python scraper.py --profile sarthak_verma --dry-run   # cost check, free
    python scraper.py --profile sarthak_verma --yes

HOW THE MODEL READ THIS RÉSUMÉ
Configured for Sarthak Verma, targeting Mobile (React Native) and Full-Stack (Java/Spring Boot/React) developer roles, excluding junior and intern titles per preferences.

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

Spending stops at $7.72: max_spend_usd below is re-checked against the account after every search, and the sweep stops there even with searches left. This file DOES set SITES (below) — Configure chose it. LinkedIn searches only the geoIds listed there, not SEARCH.locations above; any site absent from that block follows config.py's default. Run --dry-run first and read the cost.

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

SITES = {
    "indeed": {
        "actor": 'misceres/indeed-scraper',
        "enabled": True,
    },
    "linkedin": {
        "actor": 'curious_coder/linkedin-jobs-scraper',
        "enabled": True,
        "locations": [
            'Delhi',
            'Gurgaon',
            'Bengaluru',
            'Hyderabad',
            'Pune',
            'Mumbai',
        ],
        "remote_geo": 'India',
        "remote_only": False,
    },
    "naukri": {
        "actor": 'muhammetakkurtt/naukri-job-scraper',
        "enabled": False,
        "locations": [
            'Delhi / NCR',
            'Remote',
        ],
        "results_per_run": 50,
    },
}

FEEDS = {
    "himalayas": {
        "enabled": True,
        "pages": 10,
        "queries": [
            'React Native Developer',
            'Mobile Application Engineer',
            'Full Stack Engineer',
            'Java Software Engineer',
            'React Developer',
            'Salesforce Developer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'React Native Developer',
        'Mobile Application Engineer',
        'Full Stack Engineer',
        'Java Software Engineer',
        'React Developer',
        'Salesforce Developer',
    ],
    "experience_years": 1,
    "locations": ['Bengaluru', 'Delhi'],
    "salary_min": None,
    "max_results": 10,
}

SETTINGS = {
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": 4,
    "min_comp_usd": 10000,
    "max_spend_usd": 1.0,
    "max_age_days": 14,
    "remote_scopes": [],
}

SCORING = {
    "skill_weights": {
        'react native': 5,
        'react-native': 5,
        'salesforce mobile sdk': 5,
        'smartstore': 5,
        'smartsync': 5,
        'apex': 5,
        'soql': 5,
        'spring boot': 5,
        'springboot': 5,
        'spring data jpa': 5,
        'spring security': 5,
        'spring ai': 5,
        'rtk query': 5,
        'redux toolkit': 5,
        'salesforce': 4,
        'react': 4,
        'react.js': 4,
        'redux thunk': 4,
        'redux': 4,
        'context api': 4,
        'hibernate': 4,
        'tailwind css': 4,
        'tailwindcss': 4,
        'bazel': 4,
        'gradle': 4,
        'maven': 4,
        'react navigation': 4,
        'azure data studio': 4,
        'java': 3,
        'postgresql': 3,
        'postgres': 3,
        'mysql': 3,
        'docker': 3,
        'axios': 3,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {},

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [
        'react native',
        'react',
        'frontend',
        'mobile',
        'android',
        'ios',
        'redux',
    ],
    "backend_terms": [
        'spring boot',
        'java',
        'backend',
        'salesforce',
        'postgresql',
        'rest api',
    ],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [
        'react native developer',
        'react native engineer',
        'salesforce developer',
        'java full stack developer',
        'full stack engineer',
        'mobile application engineer',
        'mobile developer',
        'java software engineer',
        'java developer',
    ],
    "fullstack_bonus": 5,

    # From --exclude-levels. soft_drop_terms is left to config.py on purpose:
    # "senior" routinely means 3-4 years, so it down-ranks instead of dropping.
    "hard_drop_terms": [
        'intern',
        'internship',
        'fresher',
        'trainee',
        'new grad',
        'junior',
        'jr',
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
    'api developer',
    'api engineer',
    'application developer',
    'application engineer',
    'application security',
    'applied ai',
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
    'development engineer',
    'django',
    'engineer',
    'engineering manager',
    'flutter',
    'front end',
    'front-end',
    'frontend',
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
    'java',
    'java ',
    'javascript',
    'llm engineer',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'ml engineer',
    'mobile',
    'mobile developer',
    'mobile engineer',
    'node',
    'php',
    'platform engineer',
    'principal engineer',
    'programmer',
    'python',
    'quality engineer',
    'rails',
    'react',
    'react native',
    'release engineer',
    'ruby',
    'salesforce',
    'sde',
    'sde 1',
    'sde i',
    'sde1',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software engineer',
    'solutions architect',
    'spring boot',
    'sre ',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'typescript',
    'web developer',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = [
    'data analyst',
    'data engineer',
    'devops',
    'fresher',
    'intern',
    'internship',
    'jr',
    'junior',
    'new grad',
    'qa engineer',
    'test engineer',
    'trainee',
]

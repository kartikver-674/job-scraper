"""cmp2_gemini_lovish — generated from a résumé by auto-apply/make_profile.py.

Salesforce Engineer & Cross-Platform Flutter Developer with experience in Apex, LWC, SOQL, Batch processing, and mobile application development.

    python scraper.py --profile cmp2_gemini_lovish --dry-run   # cost check, free
    python scraper.py --profile cmp2_gemini_lovish --yes

HOW THE MODEL READ THIS RÉSUMÉ
Domain half A focuses on candidate's core Salesforce platform expertise (Apex, LWC, SOQL), while half B covers their secondary stack in mobile and backend API development (Flutter, Dart, Node.js). Weights strictly follow rule 1, prioritizing platform-specific technologies over generic tools.

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
            'Salesforce Developer',
            'Salesforce Engineer',
            'Flutter Developer',
            'Salesforce LWC Developer',
            'Full Stack Flutter Developer',
            'Salesforce Apex Developer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'Salesforce Developer',
        'Salesforce Engineer',
        'Flutter Developer',
        'Salesforce LWC Developer',
        'Full Stack Flutter Developer',
        'Salesforce Apex Developer',
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
    "max_spend_usd": 0.17,
    "max_age_days": 14,
    "remote_scopes": [],
}

SCORING = {
    "skill_weights": {
        'salesforce': 2,
        'apex': 4,
        'lwc': 4,
        'lightning web components': 5,
        'lightning web component': 5,
        'soql': 4,
        'sosl': 5,
        'batch apex': 5,
        'queueable apex': 5,
        'schedulable apex': 5,
        'visualforce': 4,
        'aura': 4,
        'flutter': 5,
        'dart': 4,
        'contentversion': 4,
        'firebase': 3,
        '.net worker services': 2,
        'node.js': 1,
        'nodejs': 2,
        'node': 1,
        'react': 1,
        'react.js': 2,
        'reactjs': 1,
        'mysql': 2,
        'postgresql': 2,
        'postgres': 3,
        'javascript': 1,
        'js': 1,
        'java': 1,
        'python': 1,
        'c#': 1,
        'csharp': 1,
        'sql': 1,
        'rest api': 3,
        'rest apis': 2,
        'rest': 1,
        'docker': 1,
        'git': 1,
        'postman': 2,
        'figma': 1,
        'computer vision': 2,
        'machine learning': 1,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {
        'internship': -3,
        'trainee': -3,
    },

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [
        'salesforce',
        'apex',
        'lwc',
        'lightning web components',
        'soql',
        'sosl',
        'visualforce',
        'aura',
    ],
    "backend_terms": [
        'flutter',
        'dart',
        'mobile',
        'node.js',
        'rest apis',
    ],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [
        'salesforce developer',
        'salesforce engineer',
        'salesforce apex developer',
        'salesforce lwc developer',
        'salesforce software engineer',
        'flutter developer',
        'flutter engineer',
        'salesforce technical consultant',
    ],
    "fullstack_bonus": 3,

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
    'apex',
    'api developer',
    'api engineer',
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
    'crm developer',
    'crm engineer',
    'dart',
    'developer',
    'developer experience',
    'developer productivity',
    'development engineer',
    'devops',
    'django',
    'engineering manager',
    'flutter',
    'flutter developer',
    'flutter engineer',
    'front end',
    'front-end',
    'frontend',
    'full stack',
    'full stack flutter',
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
    'lightning',
    'llm engineer',
    'lwc',
    'machine learning engineer',
    'mean stack',
    'member of technical staff',
    'mern',
    'ml engineer',
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
    'react native',
    'release engineer',
    'ruby',
    'salesforce',
    'salesforce analyst',
    'salesforce architect',
    'salesforce consultant',
    'salesforce developer',
    'salesforce engineer',
    'sde',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software engineer',
    'solutions architect',
    'soql',
    'spring boot',
    'sre ',
    'staff engineer',
    'systems engineer',
    'tech lead',
    'technical lead',
    'test engineer',
    'typescript',
    'visualforce',
    'web developer',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = [
    'account executive',
    'inside sales',
    'sales director',
    'sales executive',
    'sales manager',
    'sales representative',
]

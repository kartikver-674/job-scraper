"""cmp4_narrow_kavya — generated from a résumé by auto-apply/make_profile.py.

business analysis

    python scraper.py --profile cmp4_narrow_kavya --dry-run   # cost check, free
    python scraper.py --profile cmp4_narrow_kavya --yes

HOW THE MODEL READ THIS RÉSUMÉ
local pipeline Also found 13 skill(s) written in the résumé and named by the market — apex, backlog, business analyst and 10 more.

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

Spending stops at $0.17: max_spend_usd below is re-checked against the account after every search, and the sweep stops there even with searches left. This file sets no SITES, so it inherits config.py's — LinkedIn and Indeed on, Naukri off (it has never returned a row and costs ~$0.50 per run minimum). Run --dry-run first and read the run count. To narrow it, copy the SITES block from profiles/kartik_reachable.py. LinkedIn searches the geoIds in SITES, NOT the locations above, so city-level LinkedIn needs that block too.

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

FEEDS = {
    "himalayas": {
        "enabled": True,
        "pages": 10,
        "queries": [
            'salesforce functional consultant',
            'salesforce techno functional consultant',
            'salesforce administrator',
            'salesforce cpq engineer 3',
            'software engineer, business systems',
            'revenue operations systems analyst',
            'salesforce developer',
            'sales operations analyst',
            'business analyst',
            'associate salesforce developer',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'salesforce functional consultant',
        'salesforce techno functional consultant',
        'salesforce administrator',
        'salesforce cpq engineer 3',
        'software engineer, business systems',
        'revenue operations systems analyst',
        'salesforce developer',
        'sales operations analyst',
        'business analyst',
        'associate salesforce developer',
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
    # Found in the résumé and named by the market, added to what
    # the model reported. Each line says why it counted as a claim.
    #   apex                         listed under skills
    #   backlog                      listed under skills
    #   business analyst             named more than once
    #   certified administrator      named more than once
    #   crm                          named more than once
    #   custom metadata              listed under skills
    #   dashboards                   listed under skills
    #   elicitation                  listed under skills
    #   excel                        listed under skills
    #   reports                      listed under skills
    #   requirements gathering       listed under skills
    #   rest api                     named more than once
    #   salesforce certified         named more than once
    "skill_weights": {
        'acceptance criteria': 4,
        'agile': 2,
        'apex': 4,
        'apex code comprehension': 3,
        'approval processes': 4,
        'as-is/to-be mapping': 3,
        'backlog': 4,
        'backlog management': 5,
        'brd': 4,
        'business analysis': 3,
        'business analyst': 2,
        'certified administrator': 3,
        'cr management': 3,
        'crm': 2,
        'custom metadata': 3,
        'custom metadata types': 3,
        'dashboards': 3,
        'data & analytics': 3,
        'data visualization': 3,
        'dms': 4,
        'elicitation': 4,
        'excel': 2,
        'experience cloud': 3,
        'flows': 5,
        'frd': 4,
        'gap analysis': 4,
        'ibm cognos': 3,
        'impact analysis': 3,
        'lightning app builder': 4,
        'monthly release rollouts': 4,
        'ms excel': 3,
        'permission sets': 3,
        'power bi': 3,
        'predictive modelling': 3,
        'profiles': 5,
        'reports': 3,
        'reports & dashboards': 3,
        'requirements gathering': 4,
        'requirements gathering & elicitation': 3,
        'rest api': 3,
        'rest api integrations': 3,
        'sales cloud': 2,
        'salesforce': 2,
        'salesforce certified': 3,
        'salesforce inspector': 5,
        'scrum': 4,
        'sdlc': 3,
        'service cloud': 2,
        'sfa': 5,
        'soql': 4,
        'sprint planning': 4,
        'spss modeller': 3,
        'sql': 2,
        'stakeholder management': 2,
        'uat': 3,
        'user stories': 3,
        'validation rules': 4,
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
    'ai engineer',
    'analyst salesforce',
    'android',
    'api developer',
    'api engineer',
    'application developer',
    'application development',
    'application engineer',
    'application security',
    'application support engineer',
    'applied ai',
    'associate salesforce',
    'associate salesforce developer',
    'automation engineer',
    'back end',
    'back-end',
    'backend',
    'build engineer',
    'business analyst',
    'business systems',
    'c#',
    'cloud engineer',
    'data analyst',
    'data analyst-veitnam-commercial excellence',
    'developer',
    'developer associate',
    'developer experience',
    'developer productivity',
    'development engineer',
    'devops',
    'django',
    'engineer fullstack',
    'engineer salesforce',
    'engineering manager',
    'flutter',
    'front end',
    'front-end',
    'frontend',
    'full stack',
    'full-stack',
    'fullstack',
    'functional consultant',
    'gen ai',
    'genai',
    'generative ai',
    'golang',
    'ii backend',
    'infrastructure engineer',
    'integration engineer',
    'ios ',
    'ios developer',
    'ios engineer',
    'java ',
    'java developer',
    'javascript',
    'llm engineer',
    'machine learning engineer',
    'manager-application development-full stack developer',
    'mean stack',
    'member of technical staff',
    'mern',
    'ml engineer',
    'mobile developer',
    'mobile engineer',
    'node',
    'node js',
    'operations analyst',
    'php',
    'platform engineer',
    'principal engineer',
    'product owner',
    'programmer',
    'python',
    'qa engineer',
    'quality engineer',
    'rails',
    'react',
    'react native',
    'release engineer',
    'revenue operations',
    'revenue operations systems analyst',
    'ruby',
    'sales consultant',
    'sales operations',
    'sales operations analyst',
    'salesforce administrator',
    'salesforce cpq',
    'salesforce cpq engineer 3',
    'salesforce developer',
    'salesforce engineer',
    'salesforce techno functional consultant',
    'sde',
    'sdet',
    'security engineer',
    'site reliability',
    'software architect',
    'software dev',
    'software development',
    'software engineer',
    'software engineer, business systems',
    'solutions architect',
    'spring boot',
    'sr. software engineer',
    'sr. software engineer- backend',
    'sre ',
    'staff engineer',
    'support engineer',
    'systems analyst',
    'systems engineer',
    'tech lead',
    'technical consultant',
    'technical consultant-ai integration',
    'technical lead',
    'technical product owner',
    'test engineer',
    'typescript',
    'web developer',
    'web developer associate',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = []

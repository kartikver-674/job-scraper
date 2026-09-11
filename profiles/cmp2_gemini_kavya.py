"""cmp2_gemini_kavya — generated from a résumé by auto-apply/make_profile.py.

Salesforce Functional Consultant and Business Analyst with 1.5+ years of experience in Salesforce CRM, SFA implementations, requirements gathering, gap analysis, Agile delivery, and Salesforce administration.

    python scraper.py --profile cmp2_gemini_kavya --dry-run   # cost check, free
    python scraper.py --profile cmp2_gemini_kavya --yes

HOW THE MODEL READ THIS RÉSUMÉ
Discriminative weights are concentrated on Salesforce ecosystem components (Sales Cloud, Service Cloud, Experience Cloud, SFA, Lightning App Builder, SOQL, Salesforce Inspector). Transferable craft skills like BRD, FRD, UAT, and Agile are weighted at 1 to prevent non-Salesforce BA roles from matching. Domain title terms are constrained to platform-qualified roles.

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
            'Salesforce Business Analyst',
            'Salesforce Functional Consultant',
            'Salesforce Administrator',
            'CRM Business Analyst',
            'SFA Business Analyst',
            'Salesforce Consultant',
        ],
    },
}

SEARCH = {
    "role_keywords": [
        'Salesforce Business Analyst',
        'Salesforce Functional Consultant',
        'Salesforce Administrator',
        'CRM Business Analyst',
        'SFA Business Analyst',
        'Salesforce Consultant',
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
        'salesforce crm': 5,
        'sales cloud': 3,
        'salescloud': 5,
        'service cloud': 3,
        'servicecloud': 5,
        'experience cloud': 4,
        'experiencecloud': 5,
        'salesforce inspector': 5,
        'lightning app builder': 5,
        'salesforce administrator': 4,
        'salesforce admin': 5,
        'salesforce functional consultant': 5,
        'sfa': 5,
        'sales force automation': 5,
        'soql': 4,
        'apex': 4,
        'apex code': 4,
        'custom metadata types': 4,
        'validation rules': 3,
        'approval processes': 4,
        'record types': 4,
        'permission sets': 4,
        'page layouts': 4,
        'lightning': 4,
        'crm': 3,
        'crm implementation': 4,
        'rest api integrations': 3,
        'dms': 4,
        'power bi': 2,
        'ibm cognos': 2,
        'cognos': 2,
        'spss': 2,
        'spss modeller': 2,
        'sql': 1,
        'predictive modelling': 2,
        'brd': 2,
        'frd': 2,
        'uat': 1,
        'gap analysis': 2,
        'as-is': 1,
        'to-be': 1,
        'user stories': 1,
        'acceptance criteria': 2,
        'requirements gathering': 2,
        'stakeholder management': 1,
        'agile': 1,
        'scrum': 2,
        'sprint planning': 2,
        'backlog management': 1,
    },

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {
        'oracle fusion': -5,
        'sap sd': -5,
        'sap mm': -5,
        'dynamics 365': -5,
        'workday': -4,
        'netsuite': -4,
        'java developer': -5,
        'qa automation engineer': -5,
    },

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": [
        'salesforce',
        'sales cloud',
        'service cloud',
        'experience cloud',
        'sfa',
        'sales force automation',
        'crm',
    ],
    "backend_terms": [
        'business analyst',
        'functional consultant',
        'requirements gathering',
        'brd',
        'frd',
        'gap analysis',
        'uat',
        'user stories',
    ],
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": [
        'salesforce business analyst',
        'salesforce functional consultant',
        'salesforce consultant',
        'salesforce administrator',
        'salesforce admin',
        'salesforce analyst',
        'crm business analyst',
        'sfa business analyst',
        'salesforce crm consultant',
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
    'api developer',
    'api engineer',
    'application developer',
    'application security',
    'applied ai',
    'automation engineer',
    'ba',
    'back end',
    'back-end',
    'backend',
    'build engineer',
    'business analyst',
    'c#',
    'cloud engineer',
    'crm',
    'developer',
    'developer experience',
    'developer productivity',
    'development engineer',
    'devops',
    'django',
    'engineering manager',
    'experience cloud',
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
    'sales cloud',
    'salesforce',
    'sde',
    'sdet',
    'security engineer',
    'service cloud',
    'sfa',
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
    'test engineer',
    'typescript',
    'web developer',
]

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = [
    'data engineer',
    'devops engineer',
    'java developer',
    'net developer',
    'oracle consultant',
    'python developer',
    'qa engineer',
    'sap consultant',
]

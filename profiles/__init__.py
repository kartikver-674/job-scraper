"""Named search profiles. One file per person or per search.

A profile defines ONLY the config keys it changes — see config.py section 5 for
the merge rules — and is selected with `python scraper.py --profile <name>`.
Each named profile writes to its own output/<name>/ directory.

    profiles/remote_intl.py    international remote, ~2 yrs experience

Start a new one by copying the closest existing profile; anything you leave out
falls through to the defaults in config.py.
"""

"""
Minimal reader for answers.yaml — a flat `key: value` file of facts that are NOT
in the résumé (notice period, expected CTC, ...). Deliberately dependency-free:
we only need flat key/value pairs, so no PyYAML.
"""

import os


def load_answers(path):
    """Parse a flat key: value file into a dict[str, str]. Missing file -> {}."""
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                result[key] = value
    return result

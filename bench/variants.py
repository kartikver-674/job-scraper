"""Separate deliberate targeting from accidental instability.

Kanav's three résumés buy almost disjoint job sets, and that number on
its own cannot say whether the pipeline is broken. He wrote a frontend
CV, a full-stack CV and a React Native CV to chase three different kinds
of role, and a pipeline that read them identically would be ignoring
what he wrote. Divergence there is the product working.

What is NOT acceptable is the same target, written up differently,
producing a different search. So this file separates the two:

  SAME TARGET    one résumé re-worded and re-formatted, content
                 untouched. Any divergence here is instability and is a
                 defect.
  DIFFERENT TARGET  Kanav's three, or three professions. Divergence
                 here is the point, and CONVERGENCE would be the defect.

The variants are generated mechanically and deterministically — no model
writes them, so nothing can quietly change the content. Each transform
touches only presentation: the order sections appear in, whether
achievements are bullets or prose, how dates are written, heading case,
and whitespace. The skills line and the job titles come through
verbatim, because those are content and changing them would make this
measure something else.

Consistency is measured on the LISTINGS BOUGHT rather than the keyword
strings. Two keyword sets that buy the same jobs are the same strategy
however differently they are spelled, and measuring the strings
understated Kanav's agreement badly — 0.60 on strings against 0.83 on
the market.

    python -m bench.variants --demo
    python -m bench.variants --dir <texts> --base kanav_reactnative
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import derive_search as ds

HERE = os.path.dirname(os.path.abspath(__file__))

# Lines that begin a section, as real résumés write them.
_HEADING = re.compile(
    r"^\s*(professional\s+summary|summary|about\s+me|core\s+skills|"
    r"technical\s+skills|skills|professional\s+experience|experience|"
    r"projects|education|certifications|core\s+competencies)\s*:?\s*$",
    re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def sections(text):
    """[(heading, [lines])] — the résumé split where its headings are."""
    out, head, body = [], "", []
    for line in text.splitlines():
        if _HEADING.match(line):
            out.append((head, body))
            head, body = line.strip(), []
        else:
            body.append(line)
    out.append((head, body))
    return out


def rebuild(parts):
    lines = []
    for head, body in parts:
        if head:
            lines.append(head)
        lines.extend(body)
    return "\n".join(lines)


def reordered(text):
    """Education and skills moved to the front. Same content, new order."""
    parts = sections(text)
    first = [p for p in parts if not p[0]]
    moved = [p for p in parts
             if p[0] and re.search(r"skill|education|competenc", p[0], re.I)]
    rest = [p for p in parts if p[0] and p not in moved]
    return rebuild(first + moved + rest)


def as_prose(text):
    """Bullets joined into sentences. The words survive; the shape does not."""
    out, buffer = [], []

    def flush():
        if buffer:
            out.append(" ".join(buffer))
            buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "•", "*", "–")):
            buffer.append(stripped.lstrip("-•*– ").rstrip())
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def numeric_dates(text):
    """"Jul 2024" becomes "07/2024". Nothing else moves."""
    def swap(match):
        month = _MONTHS[match.group(1)[:3].lower()]
        return f"{month:02d}/{match.group(2)}"
    return re.sub(r"\b([A-Z][a-z]{2,8})\.?\s+((?:19|20)\d{2})\b",
                  lambda m: swap(m) if m.group(1)[:3].lower() in _MONTHS
                  else m.group(0), text)


def shouting(text):
    """Headings uppercased. A formatting choice, nothing more."""
    return "\n".join(line.upper() if _HEADING.match(line) else line
                     for line in text.splitlines())


def dense(text):
    """Blank lines removed and runs of spaces collapsed."""
    lines = [re.sub(r"[ \t]{2,}", " ", line.rstrip())
             for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


TRANSFORMS = {
    "reordered": reordered,
    "prose": as_prose,
    "numeric-dates": numeric_dates,
    "shouting": shouting,
    "dense": dense,
}


def variants(text):
    """{name: text} — the original plus one per transform."""
    out = {"original": text}
    for name, fn in TRANSFORMS.items():
        out[name] = fn(text)
    return out


def content_words(text):
    """The words a transform must not touch: everything but layout."""
    return sorted(w for w in re.findall(r"[A-Za-z0-9+#./]{2,}", text.lower()))


def invariant(original, changed, allow=("jan", "feb", "mar", "apr", "may",
                                        "jun", "jul", "aug", "sep", "oct",
                                        "nov", "dec")):
    """Did this transform change anything but presentation?

    The experiment is only worth running if the variants say the same
    thing. Month names are allowed to disappear because numeric_dates
    deliberately rewrites them, and nothing else may.
    """
    before, after = content_words(original), content_words(changed)
    # "Jul 2024" becoming "07/2024" absorbs the year into one token, so a
    # bare year going missing is fine EXACTLY WHEN the numeric form of it
    # turned up. Anything else lost is a transform changing content.
    numeric_years = {w.split("/")[1] for w in after
                     if re.fullmatch(r"\d{2}/\d{4}", w)}
    lost = [w for w in set(before) - set(after)
            if not any(w.startswith(m) for m in allow)
            and w not in numeric_years]
    gained = [w for w in set(after) - set(before) if not w.isdigit()
              and not re.fullmatch(r"\d{2}/\d{4}", w)]
    return sorted(lost), sorted(gained)


def bought(keywords, rows):
    """The set of listings a keyword set would buy."""
    needles = [k.strip().lower() for k in keywords if k.strip()]
    if not needles:
        return set()
    return {r[0] for r in rows if any(n in r[0] for n in needles)}


def jaccard(a, b):
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def run(model, directory, bases, cache_path=None):
    """The pipeline over every variant of every base résumé."""
    import json
    from bench.real_resumes import profile_for

    cache_path = cache_path or os.path.join(
        HERE, "results", f"variants-{model.replace(':', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    for base in bases:
        with open(os.path.join(directory, base + ".txt"),
                  encoding="utf-8") as fh:
            text = fh.read()
        for name, changed in variants(text).items():
            lost, gained = invariant(text, changed)
            assert not lost and not gained, (base, name, lost[:5], gained[:5])
            key = f"{base}::{name}"
            if key in cache:
                continue
            got, seconds = profile_for(model, changed)
            got["seconds"] = round(seconds, 1)
            cache[key] = got
            print(f"  {key:<34} {seconds:>5.0f}s  "
                  f"{len(got['keywords'])} keywords", flush=True)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1)
    return cache


def report(model, directory, bases, cache=None):
    import itertools
    import json
    from bench import orphan_skills as osk

    cache = cache if cache is not None else run(model, directory, bases)
    rows, _idx, _vocab, _sen = osk.setup()

    print(f"\n{'=' * 76}\nsame target, different wording: is the search "
          f"stable?\n{'=' * 76}")
    print("  measured on the LISTINGS BOUGHT, not the keyword strings\n")
    same = []
    for base in bases:
        group = {k.split("::")[1]: v for k, v in cache.items()
                 if k.startswith(base + "::")}
        if len(group) < 2:
            continue
        sets = {n: bought(v["keywords"], rows) for n, v in group.items()}
        pairs = [(a, b, jaccard(sets[a], sets[b]))
                 for a, b in itertools.combinations(sorted(sets), 2)]
        worst = min(pairs, key=lambda p: p[2])
        mean = sum(p[2] for p in pairs) / len(pairs)
        same.extend(p[2] for p in pairs)
        print(f"  {base}")
        print(f"    {len(group)} variants, {len(pairs)} pairs   "
              f"mean {mean:.2f}   worst {worst[2]:.2f} "
              f"({worst[0]} vs {worst[1]})")
        for name in sorted(sets):
            print(f"      {name:<16}{len(group[name]['keywords']):>3} kw  "
                  f"{len(sets[name]):>5} listings")
    if same:
        print(f"\n  SAME TARGET overall: mean {sum(same) / len(same):.2f}, "
              f"worst {min(same):.2f}  over {len(same)} pairs")

    # The contrast. Different targets SHOULD diverge; convergence here
    # would mean the pipeline is ignoring what the résumé says.
    real_path = os.path.join(HERE, "results", f"real-{model.replace(':', '_')}.json")
    if not os.path.exists(real_path):
        return
    with open(real_path, encoding="utf-8") as fh:
        real = json.load(fh)
    print(f"\n{'=' * 76}\ndifferent targets: do they stay different?"
          f"\n{'=' * 76}")
    groups = {"kanav, three targets": [k for k in real if k.startswith("kanav")],
              "three professions": ["lovish", "kavya", "akankshya"]}
    for label, names in groups.items():
        names = [n for n in names if n in real]
        if len(names) < 2:
            continue
        sets = {n: bought(real[n]["keywords"], rows) for n in names}
        pairs = [(a, b, jaccard(sets[a], sets[b]))
                 for a, b in itertools.combinations(sorted(sets), 2)]
        print(f"  {label}: mean {sum(p[2] for p in pairs) / len(pairs):.2f}")
        for a, b, j in pairs:
            print(f"    {a:<20} vs {b:<20} {j:.2f}")


def demo():
    text = ("Jane Doe\n"
            "Professional Summary\n"
            "React Native Engineer with 4 years.\n"
            "Technical Skills\n"
            "Mobile: React Native, Redux Toolkit\n"
            "Professional Experience\n"
            "Acme Jul 2024 - Present\n"
            "- Built apps\n"
            "- Shipped releases\n"
            "Education\n"
            "B.Tech 2022\n")

    got = variants(text)
    assert set(got) == {"original"} | set(TRANSFORMS)
    # Every transform must move presentation ONLY. If one changes the
    # content, the experiment measures the transform rather than the
    # pipeline, and the whole result is worthless.
    for name, changed in got.items():
        lost, gained = invariant(text, changed)
        assert not lost and not gained, (name, lost, gained)

    assert "07/2024" in got["numeric-dates"]
    assert "Jul 2024" not in got["numeric-dates"]
    # A year absorbed into a numeric date is allowed; a year deleted is
    # not, and the check has to tell them apart.
    lost, _g = invariant(text, text.replace("2022", ""))
    assert "2022" in lost, lost

    assert "- Built apps" not in got["prose"]
    assert "Built apps Shipped releases" in got["prose"]
    assert "TECHNICAL SKILLS" in got["shouting"]
    assert "Technical Skills" not in got["shouting"]
    assert "\n\n" not in got["dense"]
    # Reordering moves the skills section above experience and keeps
    # every line.
    assert got["reordered"].index("Technical Skills") < \
        got["reordered"].index("Professional Experience")
    assert sorted(got["reordered"].split()) == sorted(text.split())

    assert sections("A\nExperience\nB")[0] == ("", ["A"])
    assert jaccard(set(), set()) == 1.0
    assert jaccard({1, 2}, {2, 3}) == 1 / 3
    rows = [("react developer", 40, frozenset(), "a"),
            ("warehouse operative", 0, frozenset(), "b")]
    assert bought(["react"], rows) == {"react developer"}
    assert bought([], rows) == set()
    print("variants demo ok")


def main():
    args = sys.argv[1:]
    if "--demo" in args:
        return demo()
    directory = args[args.index("--dir") + 1] if "--dir" in args else None
    if not directory:
        sys.exit(__doc__)
    bases = (args[args.index("--base") + 1].split(",") if "--base" in args
             else ["kanav_reactnative", "lovish", "kavya"])
    report("qwen3:8b", directory, bases)


if __name__ == "__main__":
    main()

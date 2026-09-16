"""Turn raw Apify screenshots into the ones the beta may actually serve.

Three things have to happen to a screenshot before it goes on a public page,
and doing them by hand is how the one that matters gets forgotten:

  blur      an Apify console screenshot carries the account's own user ID,
            its name, and its balance. None of that belongs on a page
            strangers can open, and a masked token is not the only secret in
            the picture.
  point     "Settings, then Integrations" is a sentence; a ring around the
            button is an instruction. The ring is drawn here rather than
            baked into the screenshot so a re-shoot does not need a
            design tool.
  shrink    these are Retina captures at ~3000px. Served as-is they would be
            the heaviest thing in the public beta by an order of magnitude,
            on the one screen whose whole job is low friction.

DEV ONLY. Pillow is deliberately NOT in requirements.txt: Render serves the
resulting PNGs and has no business importing an imaging library to do it.

    .venv/bin/pip install Pillow
    .venv/bin/python tools/apify_shots.py ~/Desktop/apify-raw

Each source file is matched by name. Regions are FRACTIONS of the image, so
a re-shoot at a different resolution still lands on the right place — but
they are still a guess about someone else's UI, so the script writes a
contact sheet with the boxes drawn on it and refuses to be trusted blindly:
look at out/_check.png before committing anything.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "sweep", "static", "apify")

# The width the page actually renders these at, doubled for Retina. Anything
# beyond this is bytes nobody sees.
WIDTH = 1200

# Ring colour: the app's own amber, which is what the rest of Sweep uses to
# mean "this is the thing".
RING = (240, 162, 46, 255)

# name: (source filename, [blur boxes], [ring boxes])
# Boxes are (left, top, right, bottom) as fractions of width/height.
SHOTS = {
    # Step 1 — the front door, logged out. Nothing on it is private; the
    # ring is on the two ways in.
    "1-signup": {
        "src": ("apify-home.png", "apify-home-loggedout.png"),
        "blur": [],
        "ring": [(0.775, 0.012, 0.995, 0.055)],
        "alt": "The apify.com home page, with the Log in and Get started "
               "buttons at the top right ringed.",
    },
    # Step 2 — signed in, the way through to the console.
    "2-console": {
        "src": ("apify-home-loggedin.png",),
        "blur": [],
        "ring": [(0.800, 0.012, 0.930, 0.055)],
        "alt": "The apify.com home page once signed in, with the Go to "
               "Console button at the top right ringed.",
    },
    # Step 3 — where the token is. The account's own user ID is printed on
    # this page in plain text, so it is blurred; so is the token row, whose
    # asterisks still give away its length.
    "3-token": {
        "src": ("apify-token.png", "apify-settings-integrations.png"),
        "blur": [
            (0.205, 0.185, 0.340, 0.215),   # "Apify user ID: ..."
            (0.160, 0.285, 0.840, 0.325),   # the masked token row
            (0.000, 0.520, 0.145, 0.600),   # sidebar: RAM / usage figures
            (0.000, 0.000, 0.145, 0.045),   # sidebar: account name
        ],
        "ring": [(0.155, 0.275, 0.855, 0.335)],
        "alt": "The Apify console, Settings then API & Integrations, with "
               "the Personal API token row ringed. The account's own "
               "identifiers are blurred out.",
    },
}


def blur(img, box):
    """Blur one region hard enough that nothing is recoverable from it.

    A pixelate would keep the shape of the text; a heavy Gaussian over a
    crop that is then pasted back does not.
    """
    w, h = img.size
    left, top, right, bottom = (int(box[0] * w), int(box[1] * h),
                                int(box[2] * w), int(box[3] * h))
    if right <= left or bottom <= top:
        return
    crop = img.crop((left, top, right, bottom))
    radius = max(12, (right - left) // 12)
    img.paste(crop.filter(ImageFilter.GaussianBlur(radius)), (left, top))


def ring(draw, size, box, width=6):
    w, h = size
    draw.rounded_rectangle(
        [int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)],
        radius=14, outline=RING, width=width)


def find(folder, names):
    for name in names:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return path
    return None


def main(folder):
    os.makedirs(OUT, exist_ok=True)
    made, missing = [], []
    sheet = []

    for key, spec in SHOTS.items():
        src = find(folder, spec["src"])
        if not src:
            missing.append(f"{key}: expected one of {', '.join(spec['src'])}")
            continue
        img = Image.open(src).convert("RGB")

        # Blur BEFORE the resize: a blur applied after downscaling is a blur
        # of already-smaller text, which is easier to reverse.
        for box in spec["blur"]:
            blur(img, box)

        if img.width > WIDTH:
            img = img.resize((WIDTH, round(img.height * WIDTH / img.width)),
                             Image.LANCZOS)

        drawn = img.copy()
        draw = ImageDraw.Draw(drawn)
        for box in spec["ring"]:
            ring(draw, drawn.size, box)

        out = os.path.join(OUT, f"{key}.png")
        drawn.save(out, optimize=True)
        made.append((out, os.path.getsize(out)))
        sheet.append(drawn)

    # The contact sheet. These boxes are a guess about somebody else's UI at
    # whatever size it was captured, and a blur that missed is worse than no
    # blur at all because it looks deliberate.
    if sheet:
        gap = 24
        width = max(i.width for i in sheet)
        height = sum(i.height for i in sheet) + gap * (len(sheet) - 1)
        check = Image.new("RGB", (width, height), (10, 18, 21))
        y = 0
        for i in sheet:
            check.paste(i, (0, y))
            y += i.height + gap
        check.save(os.path.join(OUT, "_check.png"), optimize=True)

    for path, size in made:
        print(f"  wrote {os.path.relpath(path, REPO)}  {size // 1024} KB")
    for line in missing:
        print(f"  MISSING {line}")
    if made:
        print("\nNow LOOK at sweep/static/apify/_check.png before committing.")
        print("Every blur box is a guess about Apify's layout: if one has "
              "missed, the ID it was meant to cover is on a public page.")
    return 0 if made and not missing else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(os.path.expanduser(sys.argv[1])))

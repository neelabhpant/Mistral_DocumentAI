"""Cloudera + Mistral AI look and feel: one palette. The web app gets it as CSS custom properties from css() (served
at /brand.css), and the architecture diagram uses the constants.

Both brands are orange, so orange is the partnership accent. Cloudera's logo orange (#FF550D, sampled from the
official logo) and Mistral's orange (#FA500F, mistral.ai/brand) are nearly identical. Navy (Cloudera, the
governed platform) frames the app; the Mistral rainbow appears once, as the rule under the header.
"""

CLOUDERA_ORANGE = "#FF550D"  # sampled from cloudera-new-logo.png
CLOUDERA_NAVY = "#0B1F33"  # approximate: not taken from an official Cloudera spec
MISTRAL_ORANGE = "#FA500F"  # mistral.ai/brand
MISTRAL_INK = "#151524"  # the Mistral wordmark color
RAINBOW = ["#E10500", "#FA500F", "#FF8205", "#FFAF00", "#FFD800"]  # mistral.ai/brand, red to yellow

ACCENT = "#D63F06"  # interactive orange: one shade deeper so white text on it passes WCAG AA (4.6:1)
BG = "#FFFCF5"  # warm off-white
BEIGE = "#FFF4DC"  # Mistral beige
INK = "#1A1A1A"
MUTED = "#5F6B76"
LINE = "#E9E1CF"
LINK = "#C2410C"
TABLE_LINE = "#EFE8D8"
CODE_BG = "#FBF6EA"

# Status colors stay clear of the brand orange so "at risk" never looks like decoration.
TONE = {"risk": "#C8102E", "review": "#E59400", "opportunity": "#17784E"}
TONE_TEXT = {"risk": "#FFFFFF", "review": MISTRAL_INK, "opportunity": "#FFFFFF"}
EVIDENCE = "#2463B0"  # supporting evidence boxes: blue reads clearly next to the status color

# OCR 4.1 block types on the layout view. Categorical slots in fixed order; structural/peripheral types share a
# neutral gray and are told apart by their box label.
GRAY = "#8A8984"
BLOCK_COLORS = {
    "text": EVIDENCE,
    "title": MISTRAL_ORANGE,
    "table": "#17A673",
    "list": "#E59400",
    "signature": "#D9609A",
    "image": "#2F8A2F",
    "caption": "#5B46B8",
}

# The navy sidebar.
SIDEBAR = {
    "bg": CLOUDERA_NAVY,
    "bg-2": "#14304D",
    "ink": "#F4F1EA",
    "accent": "#FF8205",  # Mistral orange light: 6.7:1 on navy
    "line": "#24415F",
    "code-ink": "#FFD7B0",
    "link": "#FFAF01",
}


def css() -> str:
    """The palette as CSS custom properties on :root, the front end's only source of brand colors."""
    props = {
        "cloudera-orange": CLOUDERA_ORANGE, "cloudera-navy": CLOUDERA_NAVY, "mistral-orange": MISTRAL_ORANGE,
        "mistral-ink": MISTRAL_INK, "accent": ACCENT, "bg": BG, "beige": BEIGE, "ink": INK, "muted": MUTED,
        "line": LINE, "link": LINK, "table-line": TABLE_LINE, "code-bg": CODE_BG, "evidence": EVIDENCE, "gray": GRAY,
        "rainbow": f"linear-gradient(90deg, {', '.join(RAINBOW)})",
        **{f"tone-{tone}": color for tone, color in TONE.items()},
        **{f"tone-{tone}-text": color for tone, color in TONE_TEXT.items()},
        **{f"block-{kind}": color for kind, color in BLOCK_COLORS.items()},
        **{f"side-{name}": color for name, color in SIDEBAR.items()},
    }
    return ":root {\n" + "".join(f"  --{name}: {value};\n" for name, value in props.items()) + "}\n"

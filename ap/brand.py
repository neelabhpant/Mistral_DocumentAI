"""Cloudera + Mistral AI look and feel: one palette, the page header, and the CSS. Theme colors and fonts that
Streamlit applies itself are in .streamlit/config.toml and must agree with the values here.

Both brands are orange, so orange is the partnership accent. Cloudera's logo orange (#FF550D, sampled from the
official logo) and Mistral's orange (#FA500F, mistral.ai/brand) are nearly identical. Navy (Cloudera, the
governed platform) frames the app; the Mistral rainbow appears once, as the rule under the header.
"""

CLOUDERA_ORANGE = "#FF550D"  # sampled from cloudera-new-logo.png
CLOUDERA_NAVY = "#0B1F33"  # approximate: not taken from an official Cloudera spec
MISTRAL_ORANGE = "#FA500F"  # mistral.ai/brand
MISTRAL_INK = "#151524"  # the Mistral wordmark color
RAINBOW = ["#E10500", "#FA500F", "#FF8205", "#FFAF00", "#FFD800"]  # mistral.ai/brand, red to yellow

ACCENT = "#D63F06"  # interactive orange: one shade deeper so white text on it passes WCAG AA
BG = "#FFFCF5"
BEIGE = "#FFF4DC"
INK = "#1A1A1A"
MUTED = "#5F6B76"
LINE = "#E9E1CF"

# Status colors stay clear of the brand orange so "at risk" never looks like decoration.
TONE = {"risk": "#C8102E", "review": "#E59400", "opportunity": "#17784E"}
TONE_TEXT = {"risk": "#FFFFFF", "review": MISTRAL_INK, "opportunity": "#FFFFFF"}
EVIDENCE = "#2463B0"  # supporting evidence boxes: blue reads clearly next to the status color

# OCR 4 block types on the layout view. Categorical slots in fixed order; structural/peripheral types share a
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

LOGOS = "app/static/logos"  # served by Streamlit static serving, relative to the app URL
CLOUDERA_LOGO = f"{LOGOS}/cloudera-new-logo.png"
MISTRAL_LOGO = f"{LOGOS}/Mistral-Lockup-Gradient-RGB.png"
MISTRAL_LOGO_DARK = f"{LOGOS}/Mistral-Lockup-Gradient-inverted-RGB.png"
MISTRAL_ICON = "static/logos/Mistral-Icon-Gradient-RGB.png"  # file path, for set_page_config

CSS = f"""<style>
/* header band */
.brandbar {{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
  padding:2px 0 10px}}
.brandbar .lockup {{display:flex;align-items:center;gap:14px}}
.brandbar .lockup img.cl {{height:19px}}
.brandbar .lockup img.mi {{height:26px}}
.brandbar .lockup .x {{color:{MUTED};font-size:1.1rem;font-weight:300}}
.brandbar .ctx {{color:{MUTED};font-size:0.8rem;letter-spacing:0.06em;text-transform:uppercase;font-weight:600}}
.rainbow {{height:4px;border-radius:2px;margin:0 0 18px;
  background:linear-gradient(90deg,{",".join(RAINBOW)})}}
.pagetitle {{font-size:2.1rem;font-weight:700;letter-spacing:-0.02em;color:{INK};margin:0 0 2px;line-height:1.2}}
.pagesub {{color:{MUTED};font-size:1rem;margin:0 0 18px}}

/* Read / Join / Act strip */
.step {{background:#FFFFFF;border:1px solid {LINE};border-radius:10px;padding:12px 14px;min-height:128px}}
.step .num {{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;
  background:{MISTRAL_ORANGE};color:{MISTRAL_INK};font-weight:700;font-size:0.8rem;margin-right:8px}}
.step b {{font-size:0.98rem}}
.step .by {{color:{MUTED};font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-left:6px}}
.step p {{color:{MUTED};font-size:0.86rem;margin:6px 0 0}}

/* metric tiles */
[data-testid="stMetric"] {{background:#FFFFFF;border:1px solid {LINE};border-top:3px solid {MISTRAL_ORANGE};
  border-radius:10px;padding:10px 14px}}
[data-testid="stMetricValue"] {{font-weight:700;letter-spacing:-0.01em}}

/* tags, legend chips, low-confidence words */
.tag {{display:inline-block;padding:3px 10px;border-radius:12px;font-size:0.8rem;font-weight:700}}
.chip {{display:inline-flex;align-items:center;gap:6px;margin:0 14px 4px 0;font-size:0.85rem;color:{MUTED}}}
.chip span.sw {{width:12px;height:12px;border-radius:3px;display:inline-block}}
mark.lowconf {{background:#FFE7B8;border-bottom:2px solid {TONE["review"]};padding:0 1px;border-radius:2px}}

/* sidebar */
.sidebrand {{display:flex;flex-direction:column;gap:10px;padding:2px 0 6px}}
.sidebrand img.mi {{height:24px;width:auto;align-self:flex-start}}
.sidebrand img.cl {{height:15px;width:auto;align-self:flex-start}}
.sidebrand .app {{font-size:1.25rem;font-weight:700;letter-spacing:-0.01em;margin-top:6px}}
.sidebrand .sub {{font-size:0.82rem;opacity:0.75}}
.sidebrand .rainbow {{margin:4px 0 0}}
.legal {{font-size:0.72rem;opacity:0.6;margin-top:18px}}
</style>"""


def header(title: str, subtitle: str = "") -> str:
    """Partner lockup on the light page, the Mistral rainbow rule, then the view title."""
    sub = f"<div class='pagesub'>{subtitle}</div>" if subtitle else ""
    return (
        "<div class='brandbar'><div class='lockup'>"
        f"<img class='cl' src='{CLOUDERA_LOGO}' alt='Cloudera'><span class='x'>×</span>"
        f"<img class='mi' src='{MISTRAL_LOGO}' alt='Mistral AI'></div>"
        "<div class='ctx'>Document AI · Accounts payable</div></div>"
        f"<div class='rainbow'></div><div class='pagetitle'>{title}</div>{sub}"
    )


def sidebar() -> str:
    """Navy sidebar: Mistral's inverted lockup (its rule for dark backgrounds) and Cloudera's orange logo."""
    return (
        "<div class='sidebrand'>"
        f"<img class='mi' src='{MISTRAL_LOGO_DARK}' alt='Mistral AI'>"
        f"<img class='cl' src='{CLOUDERA_LOGO}' alt='Cloudera'>"
        "<div class='rainbow'></div>"
        "<div class='app'>Document AI</div><div class='sub'>Accounts payable · read, join, act</div></div>"
    )


def step(n: int, title: str, by: str, text: str) -> str:
    return f"<div class='step'><span class='num'>{n}</span><b>{title}</b><span class='by'>{by}</span><p>{text}</p></div>"


def tag(label: str, tone: str) -> str:
    return f"<span class='tag' style='background:{TONE[tone]};color:{TONE_TEXT[tone]}'>{label}</span>"


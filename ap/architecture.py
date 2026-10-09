"""Reference architecture for Document AI on Cloudera + Mistral, rendered as one SVG (used by the app's
Architecture view and the 201 slides).

Layout: supplier documents and ERP sit outside the environment boundary on the left. Inside it, the services
(ingest, read, join, act) form the top row over a lakehouse band (object storage + Iceberg), then orchestration and
SDX governance bands. Numbered arrows trace one invoice: arrive, land, read, store fields, join, act.

Logos are the projects' official marks (static/logos/oss, from each project's website), rasterized to
static/logos/arch/*.png and embedded as data URIs so the SVG is self-contained.

Run: python -m ap.architecture out.svg
"""

import base64
import sys
from functools import cache
from html import escape
from pathlib import Path

from PIL import Image

from ap import brand

W, H = 1600, 900
FONT = "Inter, Arial, sans-serif"
NAVY, ORANGE, INK, MUTED, LINE = brand.CLOUDERA_NAVY, brand.MISTRAL_ORANGE, brand.MISTRAL_INK, brand.MUTED, brand.LINE
STATIC = Path(__file__).resolve().parent.parent / "static"
ICONS = STATIC / "logos" / "arch"
INTER = STATIC / "fonts" / "Inter-Variable.woff2"  # embedded, so the SVG renders the same as an <img>, a download or a slide

BOUNDARY = "Your environment: public cloud · private cloud · on-prem · air-gapped"

SOURCES = [  # (glyph, title, lines)
    ("mail", "Supplier documents", ["Email, supplier portal,", "scanners / MFP, SFTP"]),
    ("db", "ERP", ["Vendors, POs, goods", "receipts, payments"]),
]

# Top row: (key, zone, provider, logos, title, lines)
SERVICES = [
    ("ingest", "Ingest", "cloudera", ["nifi"], "Cloudera DataFlow (NiFi)",
     ["Watches sources and lands PDFs", "Triggers processing per document", "Replicates ERP tables"]),
    ("read", "Read", "mistral", ["mistral"], "Mistral OCR 4",
     ["Self-hosted container", "Schema-mode fields + line items", "Bounding boxes, word confidence"]),
    ("join", "Join", "cloudera", ["impala", "trino"], "Impala or Trino",
     ["SQL rules over Iceberg", "Every field vs vendor master,", "POs, receipts, payments"]),
    ("act", "Act", "cloudera", ["glyph:app"], "Cloudera AI Application",
     ["Prioritized action queue", "Human review of low confidence", "BI, alerts, ERP write-back"]),
]
CHAT = ("Mistral Medium 3.5", ["on Cloudera AI Inference Service", "Ask questions of any invoice"])

LAKE = [  # (logo, title, lines)
    ("ozone", "Object storage landing zone", ["S3 / ADLS / Ozone", "Original PDFs + raw OCR output"]),
    ("iceberg", "Iceberg tables (Iceberg REST Catalog)",
     ["documents · extractions (value, page, box, confidence) · exceptions",
      "vendors · purchase_orders · goods_receipts · payments"]),
]
ORCHESTRATE = "Cloudera AI Jobs: batch and event-driven processing, triggered from NiFi"
GOVERN = [  # (logo, name, description)
    ("ranger", "Ranger", "Access policies, field masking"),
    ("atlas", "Atlas", "Lineage of NiFi flows"),
    ("knox", "Knox / SSO", "Authenticated access"),
    ("glyph:audit", "Audit", "Who read or changed what"),
]
STEPS = [
    "Arrive: an invoice reaches email, the portal, a scanner or SFTP.",
    "Land: NiFi writes the PDF to the governed landing zone.",
    "Read: Mistral OCR 4 extracts fields, layout and confidence inside the environment.",
    "Store: every field lands in Iceberg with its page, bounding box and confidence.",
    "Join: SQL rules check each field against the vendor master, POs, receipts and payments.",
    "Act: the queue ranks actions; low-confidence fields go to a person; results flow back to the ERP.",
]

# Geometry
SRC_X, SRC_W = 30, 220
BX, BY, BW, BH = 290, 64, 1286, 818  # environment boundary
COL_Y, COL_H, COL_W, GAP = 120, 330, 282, 34
COL_X = [BX + 28 + i * (COL_W + GAP) for i in range(4)]
LAKE_Y, LAKE_H = 504, 112  # storage boxes; the band's label sits below them
LAND_X, LAND_W = COL_X[0], 2 * COL_W + GAP - 130
ICE_X = LAND_X + LAND_W + GAP
ICE_W = COL_X[3] + COL_W - ICE_X
ORCH_Y, GOV_Y = 702, 760


@cache
def _logo_data(name: str) -> tuple[str, float]:
    """(data URI, width/height) of a rasterized logo."""
    path = ICONS / f"{name}.png"
    with Image.open(path) as im:
        ratio = im.width / im.height
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode(), ratio


def _logo(name, x, y, h):
    """Logo `h` px tall at (x, y); returns (svg, width)."""
    uri, ratio = _logo_data(name)
    w = h * ratio
    return f"<image href='{uri}' x='{x}' y='{y}' width='{w:.1f}' height='{h}'/>", w


def _glyph(kind, x, y, s, color=NAVY):
    """Simple line icons for parts that have no product logo (an s-by-s box at x, y)."""
    a = f"fill='none' stroke='{color}' stroke-width='{s / 14:.1f}' stroke-linejoin='round' stroke-linecap='round'"
    if kind == "app":  # application window with a list
        return (f"<rect x='{x + s * .06}' y='{y + s * .12}' width='{s * .88}' height='{s * .76}' rx='{s * .1}' {a}/>"
                f"<line x1='{x + s * .06}' y1='{y + s * .32}' x2='{x + s * .94}' y2='{y + s * .32}' {a}/>"
                + "".join(f"<line x1='{x + s * .22}' y1='{y + s * f}' x2='{x + s * .78}' y2='{y + s * f}' {a}/>"
                          for f in (.48, .62, .76)))
    if kind == "mail":
        return (f"<rect x='{x + s * .06}' y='{y + s * .2}' width='{s * .88}' height='{s * .6}' rx='{s * .08}' {a}/>"
                f"<polyline points='{x + s * .08},{y + s * .24} {x + s * .5},{y + s * .55} {x + s * .92},{y + s * .24}' {a}/>")
    if kind == "db":
        rx, ry = s * .38, s * .12
        cx = x + s / 2
        return (f"<ellipse cx='{cx}' cy='{y + s * .2}' rx='{rx}' ry='{ry}' {a}/>"
                f"<path d='M{cx - rx},{y + s * .2} V{y + s * .8} A{rx},{ry} 0 0 0 {cx + rx},{y + s * .8} V{y + s * .2}' {a}/>"
                f"<path d='M{cx - rx},{y + s * .5} A{rx},{ry} 0 0 0 {cx + rx},{y + s * .5}' {a}/>")
    if kind == "audit":  # clipboard with check
        return (f"<rect x='{x + s * .16}' y='{y + s * .1}' width='{s * .68}' height='{s * .82}' rx='{s * .08}' {a}/>"
                f"<polyline points='{x + s * .32},{y + s * .52} {x + s * .46},{y + s * .66} {x + s * .7},{y + s * .38}' {a}/>")
    raise ValueError(kind)


def _mark(name, x, y, h, color=NAVY):
    if name.startswith("glyph:"):
        return _glyph(name[6:], x, y, h, color), h
    return _logo(name, x, y, h)


def _text(x, y, s, size=15, weight=400, color=INK, anchor="start"):
    return (f"<text x='{x}' y='{y}' font-size='{size}' font-weight='{weight}' fill='{color}' "
            f"text-anchor='{anchor}'>{escape(s)}</text>")


def _lines(x, y, lines, size=14, color=MUTED, lh=20):
    return "".join(_text(x, y + i * lh, s, size, color=color) for i, s in enumerate(lines))


def _box(x, y, w, h, fill="#FFFFFF", stroke=LINE, sw=1.5, r=12, dash=None):
    d = f" stroke-dasharray='{dash}'" if dash else ""
    return f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{r}' fill='{fill}' stroke='{stroke}' stroke-width='{sw}'{d}/>"


def _provider(provider, right, y):
    """Who provides the part: the Cloudera wordmark or the Mistral icon, right-aligned at `right`."""
    name, h = ("cloudera", 12) if provider == "cloudera" else ("mistral", 18)
    _, ratio = _logo_data(name)
    return _logo(name, right - h * ratio, y, h)[0]


def _badge(x, y, n):
    return (f"<circle cx='{x}' cy='{y}' r='13' fill='{ORANGE}' stroke='#FFFFFF' stroke-width='2'/>"
            + _text(x, y + 5, str(n), 13, 700, INK, "middle"))


def _arrow(x1, y1, x2, y2, n=None, bx=None, by=None):
    out = (f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{NAVY}' stroke-width='2.5' "
           f"marker-end='url(#arr)'/>")
    if n is not None:
        out += _badge(bx if bx is not None else (x1 + x2) / 2, by if by is not None else (y1 + y2) / 2, n)
    return out


def _service(x, key, zone, provider, logos, title, lines):
    mistral = provider == "mistral"
    accent = ORANGE if mistral else NAVY
    out = _box(x, COL_Y, COL_W, COL_H, stroke=accent if mistral else LINE, sw=2 if mistral else 1.5)
    out += f"<rect x='{x}' y='{COL_Y}' width='{COL_W}' height='5' rx='2' fill='{accent}'/>"
    out += _text(x + 18, COL_Y + 32, zone.upper(), 12, 700, MUTED)
    out += _provider(provider, x + COL_W - 18, COL_Y + 19 if provider == "cloudera" else COL_Y + 16)
    lx = x + 18
    for name in logos:  # component logos
        svg, w = _mark(name, lx, COL_Y + 48, 44)
        out += svg
        lx += w + 14
    out += _text(x + 18, COL_Y + 124, title, 18, 700)
    out += _lines(x + 18, COL_Y + 150, lines)
    if key == "read":  # the chat model shares the Read column
        title, sub = CHAT
        cy = COL_Y + 222
        out += f"<line x1='{x + 18}' y1='{cy}' x2='{x + COL_W - 18}' y2='{cy}' stroke='{LINE}' stroke-width='1'/>"
        out += _logo("mistral", x + 18, cy + 17, 18)[0]
        out += _text(x + 50, cy + 32, title, 16, 700)
        out += _lines(x + 18, cy + 58, sub, 13)
    return out


def render_svg() -> str:
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' width='100%' font-family='{FONT}' "
        f"role='img' aria-label='Reference architecture: Document AI on Cloudera with Mistral'>",
        "<defs><style>@font-face{font-family:Inter;font-weight:100 900;src:url(data:font/woff2;base64,"
        + base64.b64encode(INTER.read_bytes()).decode() + ") format('woff2')}</style>"
        "<marker id='arr' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' "
        f"orient='auto-start-reverse'><path d='M0,0 L10,5 L0,10 z' fill='{NAVY}'/></marker></defs>",
        f"<rect width='{W}' height='{H}' fill='{brand.BG}'/>",
    ]
    # Sources, outside the environment
    parts.append(_text(SRC_X, BY + 36, "SOURCES", 12, 700, MUTED))
    src_y = [COL_Y + 10, COL_Y + 190]
    for (glyph, title, lines), y in zip(SOURCES, src_y):
        parts.append(_box(SRC_X, y, SRC_W, 140))
        parts.append(_glyph(glyph, SRC_X + 16, y + 14, 34, MUTED))
        parts.append(_text(SRC_X + 16, y + 76, title, 16, 700))
        parts.append(_lines(SRC_X + 16, y + 100, lines, 13.5))
    # Environment boundary
    parts.append(_box(BX, BY, BW, BH, fill="#FFFFFF00", stroke=NAVY, sw=2, r=18, dash="8 6"))
    parts.append(f"<rect x='{BX + 24}' y='{BY - 14}' width='{len(BOUNDARY) * 7.25 + 26:.0f}' height='28' rx='14' fill='{NAVY}'/>")
    parts.append(_text(BX + 36, BY + 5, BOUNDARY, 14, 600, "#FFFFFF"))
    # Services row
    for x, svc in zip(COL_X, SERVICES):
        parts.append(_service(x, *svc))
    # Lakehouse band
    band_y = LAKE_Y - 14
    parts.append(_box(BX + 14, band_y, BW - 28, ORCH_Y - 14 - band_y, fill=brand.BEIGE, stroke="#F1E3C2", r=14))
    for (logo, title, lines), (x, w) in zip(LAKE, [(LAND_X, LAND_W), (ICE_X, ICE_W)]):
        parts.append(_box(x, LAKE_Y, w, LAKE_H))
        parts.append(_logo(logo, x + 18, LAKE_Y + 22, 40)[0])
        parts.append(_text(x + 76, LAKE_Y + 40, title, 16, 700))
        parts.append(_lines(x + 76, LAKE_Y + 66, lines, 13.5))
    label_y = LAKE_Y + LAKE_H + 30
    parts.append(_text(BX + 32, label_y, "STORE", 12, 700, MUTED))
    parts.append(_logo("cloudera", BX + 94, label_y - 11, 12)[0])
    parts.append(_text(BX + 210, label_y, "Cloudera lakehouse: open formats, one governed copy of the data", 13, 400, MUTED))
    # Orchestration and governance bands
    parts.append(_box(BX + 14, ORCH_Y, BW - 28, 44, fill="#FFFFFF", stroke=LINE, r=10))
    parts.append(_text(BX + 32, ORCH_Y + 28, "ORCHESTRATE", 12, 700, MUTED))
    parts.append(_text(BX + 150, ORCH_Y + 28, ORCHESTRATE, 14, 500))
    parts.append(_box(BX + 14, GOV_Y, BW - 28, 106, fill=NAVY, stroke=NAVY, r=12))
    parts.append(_text(BX + 32, GOV_Y + 32, "GOVERN · SDX", 12, 700, "#FFAF00"))
    parts.append(_text(BX + 32, GOV_Y + 54, "Spans every layer", 12, 400, "#C9D3DE"))
    gx, gw = BX + 190, (BW - 28 - 190) / len(GOVERN)
    for i, (logo, name, desc) in enumerate(GOVERN):
        x = gx + i * gw
        chip = 58 if logo == "knox" else 46  # logos sit on white chips so they read on navy
        parts.append(_box(x, GOV_Y + 30, chip, 46, fill="#FFFFFF", stroke="#FFFFFF", r=10))
        _, ratio = (None, 1.0) if logo.startswith("glyph:") else _logo_data(logo)
        h = min(32, (chip - 10) / ratio)
        parts.append(_mark(logo, x + (chip - h * ratio) / 2, GOV_Y + 53 - h / 2, h)[0])
        parts.append(_text(x + chip + 14, GOV_Y + 48, name, 16, 700, "#FFFFFF"))
        parts.append(_text(x + chip + 14, GOV_Y + 70, desc, 12.5, 400, "#C9D3DE"))
    # Flow: 1 arrive, 2 land, 3 read, 4 store fields, 5 join, 6 act
    ingest, read, join, act = COL_X
    mid = COL_Y + 100
    parts.append(_arrow(SRC_X + SRC_W, src_y[0] + 70, ingest - 4, mid, 1))
    parts.append(_arrow(SRC_X + SRC_W, src_y[1] + 70, ingest - 4, mid + 40))
    parts.append(_arrow(ingest + 110, COL_Y + COL_H, ingest + 110, LAKE_Y - 2, 2))
    parts.append(_arrow(read + 70, LAKE_Y, read + 70, COL_Y + COL_H + 2, 3))
    parts.append(_arrow(read + 220, COL_Y + COL_H, read + 220, LAKE_Y - 2, 4))
    parts.append(_arrow(join + 145, LAKE_Y, join + 145, COL_Y + COL_H + 2, 5))
    parts.append(_arrow(join + COL_W, mid, act - 4, mid, 6, join + COL_W + GAP / 2, mid - 26))
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "architecture.svg"
    Path(out).write_text(render_svg())
    print(f"wrote {out}")

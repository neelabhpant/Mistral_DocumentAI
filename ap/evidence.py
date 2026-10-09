"""Tie each extracted value back to the page: which OCR 4 block (and line / table row) it came from, its
bounding box, and the lowest word confidence across it. Works on the raw OCR response as a dict, so it
runs the same on fresh responses and on JSON read back from the lake."""

import re
from datetime import date

from ap.schema import DATE_FIELDS, MONEY_FIELDS

MD_NOISE = re.compile(r"\*\*|^#+\s*|\s{2,}$", re.M)


def candidates(field: str, value) -> list[str]:
    """Ways the value may be printed on the page."""
    if value is None or value == "":
        return []
    if field in MONEY_FIELDS:
        x = float(value)
        return [f"{x:,.2f}", f"{x:.2f}"]
    if field == "quantity":
        x = float(value)
        return [str(int(x)) if x == int(x) else str(x)]
    if field in DATE_FIELDS:
        try:
            d = date.fromisoformat(str(value))
        except ValueError:
            return [str(value)]
        return [d.strftime("%m/%d/%Y"), d.strftime("%B %d, %Y"), d.isoformat(), d.strftime("%b %d, %Y")]
    return [str(value).strip()]


def _lines(block: dict) -> list[str]:
    content = block["content"]
    if block["type"] == "table":
        return [l for l in content.splitlines() if l.strip() and not re.fullmatch(r"[\s|:-]+", l)]
    return [MD_NOISE.sub("", l) for l in content.splitlines() if l.strip()]


def _bbox(block: dict, dims: dict, row: int, n_rows: int) -> list[float]:
    """Block box narrowed to one of its n_rows lines, as page fractions [x0, y0, x1, y1]."""
    w, h = dims["width"], dims["height"]
    y0, y1 = block["top_left_y"], block["bottom_right_y"]
    step = (y1 - y0) / max(n_rows, 1)
    return [round(block["top_left_x"] / w, 4), round((y0 + row * step) / h, 4),
            round(block["bottom_right_x"] / w, 4), round((y0 + (row + 1) * step) / h, 4)]


def _confidence(words: list[dict], text: str, needle: str) -> float | None:
    """Min confidence of the words covering the first occurrence of needle in text (word start_index refers to text)."""
    i = text.lower().find(needle.lower())
    if i < 0 or not words:
        return None
    end = i + len(needle)
    hit = [w["confidence"] for w in words if w["start_index"] < end and w["start_index"] + len(w["text"]) > i]
    return round(min(hit), 4) if hit else None


def locate(ocr: dict, field: str, value, row_key: str | None = None) -> dict:
    """Find value on the page. row_key (a line item's SKU or description) pins line-item fields to their table row."""
    needles = candidates(field, value)
    if not needles:
        return {"page": None, "bbox": None, "confidence": None}
    for page in ocr["pages"]:
        tables = {t["content"].strip(): t for t in page.get("tables") or []}
        blocks = sorted(page.get("blocks") or [], key=lambda b: b["type"] == "footer")  # footers repeat headers
        for block in blocks:
            lines = _lines(block)
            for row, line in enumerate(lines):
                if row_key and row_key.lower() not in line.lower():
                    continue
                needle = next((n for n in needles if n.lower() in line.lower()), None)
                if not needle:
                    continue
                if block["type"] == "table":
                    t = tables.get(block["content"].strip()) or next(iter(tables.values()), None)
                    conf = _confidence(t.get("word_confidence_scores") or [], t["content"], line.strip()) if t else None
                    # narrow to the value itself within the row's text when possible
                    if t:
                        start = t["content"].find(line.strip())
                        sub = t["content"][start:] if start >= 0 else t["content"]
                        offset = (start if start >= 0 else 0)
                        words = [dict(w, start_index=w["start_index"] - offset) for w in t.get("word_confidence_scores") or []]
                        conf = _confidence(words, sub, needle) or conf
                else:
                    cs = page.get("confidence_scores") or {}
                    conf = _confidence(cs.get("word_confidence_scores") or [], page["markdown"], needle)
                return {"page": page["index"] + 1, "bbox": _bbox(block, page["dimensions"], row, len(lines)),
                        "confidence": conf}
    return {"page": None, "bbox": None, "confidence": None}

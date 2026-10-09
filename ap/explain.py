"""Explain one exception: which extracted fields are the evidence on the page, and how the invoice compares
with the enterprise record that triggered the rule."""

import re
from datetime import timedelta
from pathlib import Path

from ap import lake
from ap.config import AS_OF, DB

RULES = {  # label, short description of the join, tone
    "BANK_MISMATCH": ("Bank details changed", "invoice remit-to vs vendor master", "risk"),
    "DUPLICATE": ("Already paid", "invoice number vs AP payment ledger", "risk"),
    "UNKNOWN_VENDOR": ("Unknown vendor", "invoice seller vs vendor master", "risk"),
    "NO_PO": ("No matching PO", "invoice PO vs purchase orders", "risk"),
    "PRICE_OVER_PO": ("Price above PO", "invoice line price vs PO line price", "risk"),
    "QTY_OVER_RECEIPT": ("Billed more than received", "invoice line qty vs goods receipts", "risk"),
    "LOW_CONFIDENCE": ("Needs human review", "OCR 4 word confidence below threshold", "review"),
    "DISCOUNT_WINDOW": ("Pay now: discount", "payment terms vs today's date", "opportunity"),
}
SECONDARY = {
    "BANK_MISMATCH": ["bank_name", "routing_number", "bank_change_notice"],
    "DUPLICATE": ["total", "invoice_date"],
    "UNKNOWN_VENDOR": ["vendor_tax_id"],
    "DISCOUNT_WINDOW": ["invoice_date", "total"],
}


def evidence_keys(exc: dict) -> list[tuple[str, int | None]]:
    """(field_name, line_no) of the primary evidence first, then supporting fields."""
    ev = exc["evidence_field"] or ""
    m = re.fullmatch(r"line:(\d+):(\w+)", ev)
    keys = [(m.group(2), int(m.group(1)))] if m else [(ev, None)]
    if m:
        keys += [(f, int(m.group(1))) for f in ("sku", "description")]
    keys += [(f, None) for f in SECONDARY.get(exc["rule_code"], [])]
    return keys


def _row(sql: str) -> dict:
    rows = lake.query(sql)
    return rows[0] if rows else {}


def comparison(exc: dict, fields: dict) -> list[dict]:
    """Rows of {what, invoice, record, source, ok}: the document side vs the governed data side.
    `fields` maps (field_name, line_no) -> extraction row."""
    rule, vid = exc["rule_code"], exc["vendor_id"]

    def val(name, line=None):
        r = fields.get((name, line))
        return r["field_value"] if r else None

    def row(what, inv, rec, source, ok=None):
        if ok is None:
            ok = str(inv or "").strip().lower() == str(rec or "").strip().lower()
        return {"what": what, "invoice": inv, "record": rec, "source": source, "ok": ok}

    if rule == "BANK_MISMATCH":
        v = _row(f"SELECT * FROM {DB}.vendors WHERE vendor_id = {lake.lit(vid)}")
        out = [row("Bank", val("bank_name"), v.get("bank_name"), "vendors"),
               row("Routing (ABA)", val("routing_number"), v.get("routing_number"), "vendors"),
               row("Account", val("bank_account"), v.get("bank_account"), "vendors")]
        if val("bank_change_notice"):
            out.append(row("Change notice on invoice", val("bank_change_notice"), "(none on file)", "vendors", False))
        return out
    if rule == "DUPLICATE":
        p = _row(f"SELECT * FROM {DB}.payments WHERE vendor_id = {lake.lit(vid)} "
                 f"AND upper(invoice_number) = upper({lake.lit(val('invoice_number'))})")
        return [row("Invoice number", val("invoice_number"), p.get("invoice_number"), "payments"),
                row("Amount", val("total"), f"{p.get('amount')}", "payments"),
                row("Invoice date", val("invoice_date"), str(p.get("invoice_date")), "payments"),
                row("Paid", "(this invoice)", f"{p.get('payment_id')} on {p.get('paid_date')}", "payments", False)]
    if rule == "NO_PO":
        pos = lake.query(f"SELECT po_number FROM {DB}.purchase_orders WHERE vendor_id = {lake.lit(vid)} ORDER BY po_number")
        return [row("PO number", val("po_number") or "(none)", ", ".join(p["po_number"] for p in pos) or "(none)",
                    "purchase_orders", False)]
    if rule in ("PRICE_OVER_PO", "QTY_OVER_RECEIPT"):
        (_, line), *_ = evidence_keys(exc)
        sku, po = val("sku", line), val("po_number")
        if rule == "PRICE_OVER_PO":
            pl = _row(f"SELECT * FROM {DB}.po_lines WHERE po_number = {lake.lit(po)} AND sku = {lake.lit(sku)}")
            return [row("Item", f"{sku} {val('description', line) or ''}", f"{pl.get('sku')} {pl.get('description')}", "po_lines"),
                    row("Unit price", val("unit_price", line), f"{float(pl.get('unit_price') or 0):.2f}", "po_lines", False),
                    row("Quantity", val("quantity", line), str(pl.get("qty")), "po_lines")]
        r = _row(f"SELECT * FROM {DB}.goods_receipts WHERE po_number = {lake.lit(po)} AND sku = {lake.lit(sku)}")
        return [row("Item", f"{sku} {val('description', line) or ''}", f"{r.get('sku')} (receipt {r.get('receipt_id')})", "goods_receipts", True),
                row("Quantity", val("quantity", line), str(r.get("qty_received")), "goods_receipts", False),
                row("Received on", "", str(r.get("received_date")), "goods_receipts", True)]
    if rule == "DISCOUNT_WINDOW":
        inv_date = val("invoice_date")
        pay_by = (lake_date(inv_date) + timedelta(days=10)).isoformat() if inv_date else None
        return [row("Terms", val("payment_terms"), "2% if paid within 10 days", "vendors", True),
                row("Invoice date", inv_date, f"pay by {pay_by} (today {AS_OF.isoformat()})", "", True),
                row("Savings", val("total"), f"{float(exc['amount_at_risk']):,.2f}", "", True)]
    if rule == "LOW_CONFIDENCE":
        f = fields.get((exc["evidence_field"], None)) or {}
        return [row(exc["evidence_field"].replace("_", " ").capitalize(), f.get("field_value"),
                    f"confidence {f.get('confidence') or 0:.2f}", "extractions", False)]
    if rule == "UNKNOWN_VENDOR":
        return [row("Vendor", val("vendor_name"), "(no match)", "vendors", False),
                row("Tax ID", val("vendor_tax_id"), "(no match)", "vendors", False)]
    return []


def lake_date(s: str):
    from datetime import date

    return date.fromisoformat(str(s)[:10])


def rule_sql(rule_code: str) -> str:
    """The SQL statement in rules.sql that produces this rule (shown in the app as 'how Cloudera found this')."""
    text = Path(__file__).with_name("rules.sql").read_text()
    for stmt in re.split(r";\s*$", text, flags=re.M):
        if f"'{rule_code}'" in stmt:
            return stmt.strip().replace("{db}", DB).replace("{as_of}", AS_OF.isoformat())
    return ""

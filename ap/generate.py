"""Synthetic accounts payable data with planted issues, all derived from one seed.

Writes the enterprise tables (vendor master, POs, goods receipts, payment history) as CSVs, the invoice PDFs
(batch + held-back "live" uploads), and ground_truth.json with each invoice's fields and expected exceptions.
All company, person and bank names are fictitious.

Usage: python -m ap.generate --vendors 15 --seed 7
"""

import argparse
import csv
import io
import json
import random
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

from ap.config import AS_OF, BUYER, DATA

# name, city/state, catalog of (sku, description, unit price)
CATALOG = [
    ("Corvane Office Supply LLC", "Columbus, OH", [
        ("OS-1101", "Copy paper, letter, 10-ream case", 48.90), ("OS-1240", "Toner cartridge, black, high yield", 132.00),
        ("OS-1315", "Gel pens, blue, box of 12", 11.25), ("OS-1402", "Hanging file folders, box of 25", 18.40),
        ("OS-1588", "Desk organizer, steel mesh", 24.75), ("OS-1630", "Sticky notes, 3x3, 24-pack", 21.60)]),
    ("Tallis Fastener Works Inc.", "Akron, OH", [
        ("TF-2210", "Hex bolt M10x40, zinc, box of 100", 36.80), ("TF-2234", "Flange nut M10, box of 200", 29.50),
        ("TF-2301", "Socket cap screw M6x20, box of 100", 22.10), ("TF-2390", "Flat washer M10, box of 500", 17.95),
        ("TF-2455", "Threaded rod 3/8-16 x 6ft", 9.85), ("TF-2510", "Rivet nut M8, box of 50", 41.20)]),
    ("Quillmark Industrial Electric", "Dayton, OH", [
        ("QE-3105", "THHN wire 12 AWG, 500ft spool", 118.00), ("QE-3170", "Breaker 20A single pole", 14.60),
        ("QE-3222", "LED high-bay fixture 150W", 189.00), ("QE-3290", "Conduit EMT 3/4in x 10ft", 8.45),
        ("QE-3348", "Junction box 4in square", 3.95), ("QE-3401", "Motor starter, 3-phase, 10HP", 412.00)]),
    ("Brennick Packaging Group", "Fort Wayne, IN", [
        ("BP-4010", "Corrugated box 18x12x12, bundle of 25", 31.75), ("BP-4055", "Stretch film 18in x 1500ft", 22.40),
        ("BP-4102", "Pallet, heat-treated 48x40", 16.50), ("BP-4160", "Void fill paper, 1000ft roll", 44.90),
        ("BP-4203", "Packing tape 2in, case of 36", 58.20), ("BP-4250", "Edge protector 2x2x36, bundle of 100", 39.00)]),
    ("Sorrel & Pike Safety Equipment", "Erie, PA", [
        ("SP-5012", "Safety glasses, clear, box of 12", 27.60), ("SP-5040", "Cut-resistant gloves A4, 12 pairs", 64.80),
        ("SP-5088", "Hard hat, type I, white", 18.25), ("SP-5120", "Hi-vis vest class 2, 10-pack", 52.00),
        ("SP-5164", "Ear plugs, box of 200 pairs", 34.40), ("SP-5199", "First aid kit, 50-person", 89.00)]),
    ("Kestrel Scientific Supply", "Ann Arbor, MI", [
        ("KS-6001", "Nitrile gloves M, box of 100", 12.80), ("KS-6012", "Pipette tips 200uL, rack of 96", 9.40),
        ("KS-6019", "Beaker, borosilicate 600mL", 7.65), ("KS-6027", "Graduated cylinder 100mL", 14.20),
        ("KS-6033", "Lab wipes, box of 280", 6.30), ("KS-6041", "Sample vials 20mL, pack of 100", 38.90),
        ("KS-6048", "pH buffer solution 7.00, 500mL", 19.75), ("KS-6055", "Weighing boats, medium, 500", 24.10),
        ("KS-6062", "Centrifuge tubes 50mL, 500", 96.00), ("KS-6070", "Lab coat, white, size L", 28.50),
        ("KS-6078", "Isopropyl alcohol 70%, 4L", 22.60), ("KS-6085", "Petri dishes 100mm, 500", 71.40),
        ("KS-6093", "Magnetic stir bar set", 33.80), ("KS-6101", "Thermometer, digital probe", 46.25),
        ("KS-6110", "Wash bottle 500mL, 6-pack", 15.90), ("KS-6118", "Parafilm 4in x 125ft", 41.70)]),
    ("Ardent Facility Services", "Toledo, OH", [
        ("AF-7005", "Janitorial service, monthly", 2450.00), ("AF-7030", "Floor strip and wax, per 1000 sq ft", 185.00),
        ("AF-7062", "Window cleaning, exterior", 640.00), ("AF-7090", "Restroom supply restock", 312.50),
        ("AF-7114", "Carpet extraction, per room", 95.00)]),
    ("Northgate HVAC Parts", "Lansing, MI", [
        ("NH-8120", "Pleated filter 20x25x2 MERV 13, case of 12", 96.00), ("NH-8155", "Condenser fan motor 1/3HP", 168.00),
        ("NH-8190", "Capacitor 45/5 MFD", 21.40), ("NH-8231", "Contactor 2-pole 40A", 26.80),
        ("NH-8277", "Refrigerant R-410A, 25lb", 245.00), ("NH-8302", "Thermostat, programmable", 74.50)]),
    ("Ironvale Tool & Supply", "Pittsburgh, PA", [
        ("IV-9104", "Carbide end mill 1/2in 4-flute", 58.40), ("IV-9150", "Drill bit set, cobalt, 29pc", 129.00),
        ("IV-9188", "Cutting fluid, 5 gal", 87.50), ("IV-9233", "Grinding wheel 7in x 1/4in", 12.95),
        ("IV-9270", "Torque wrench 1/2in drive", 214.00), ("IV-9315", "Deburring tool kit", 31.60)]),
    ("Lumen Peak IT Solutions", "Cleveland, OH", [
        ("LP-1012", "Laptop, 14in, 32GB RAM", 1489.00), ("LP-1048", "27in monitor, USB-C", 329.00),
        ("LP-1090", "Docking station", 189.00), ("LP-1133", "Network switch 24-port PoE", 612.00),
        ("LP-1175", "Wireless access point", 248.00), ("LP-1210", "Managed services, monthly per seat", 85.00)]),
    ("Halcyon Uniform Co.", "Indianapolis, IN", [
        ("HU-1301", "Work shirt, navy, embroidered", 24.50), ("HU-1325", "Work pants, khaki", 29.75),
        ("HU-1350", "Coverall, flame resistant", 112.00), ("HU-1377", "Uniform rental, weekly per employee", 14.20),
        ("HU-1402", "Winter jacket, insulated", 86.00)]),
    ("Westbrook Freight Lines", "Detroit, MI", [
        ("WF-1410", "LTL freight, Toledo to Chicago", 685.00), ("WF-1433", "Liftgate delivery", 75.00),
        ("WF-1460", "Fuel surcharge", 112.40), ("WF-1488", "Full truckload, regional", 2150.00),
        ("WF-1512", "Detention, per hour", 85.00)]),
    ("Pellucid Water Treatment", "Grand Rapids, MI", [
        ("PW-1520", "Cooling tower chemical treatment, monthly", 780.00), ("PW-1548", "Water softener salt, 50lb", 9.80),
        ("PW-1575", "Boiler water test kit", 145.00), ("PW-1601", "RO membrane cartridge", 268.00),
        ("PW-1630", "Service visit, technician", 195.00)]),
    ("Cobaltline Print & Signage", "Cincinnati, OH", [
        ("CP-1705", "Safety signage, aluminum 10x14", 18.60), ("CP-1733", "Floor marking tape, 2in x 100ft", 27.40),
        ("CP-1760", "Business cards, box of 500", 39.00), ("CP-1788", "Banner, vinyl 3x8ft", 92.00),
        ("CP-1812", "Label rolls, thermal 4x6, case of 12", 64.50)]),
    ("Merrow Compressed Air Systems", "Youngstown, OH", [
        ("MC-1902", "Air filter element", 48.30), ("MC-1930", "Compressor oil, synthetic, 5 gal", 189.00),
        ("MC-1957", "Quick coupler, 1/2in", 11.20), ("MC-1981", "Air hose 3/8in x 50ft", 34.75),
        ("MC-2010", "Preventive maintenance visit", 425.00), ("MC-2034", "Pressure regulator", 66.40)]),
    ("Dunmore Abrasives Ltd.", "Buffalo, NY", [
        ("DA-2105", "Sanding disc 5in P120, box of 50", 23.40), ("DA-2133", "Flap disc 4.5in, box of 10", 38.60),
        ("DA-2160", "Sanding belt 2x72 P80, 10-pack", 44.90), ("DA-2188", "Wire wheel 6in", 19.80)]),
    ("Fernhollow Catering", "Toledo, OH", [
        ("FC-2205", "Boxed lunches, per person", 14.50), ("FC-2230", "Coffee service, per gallon", 22.00),
        ("FC-2258", "Breakfast platter, serves 20", 165.00), ("FC-2281", "Delivery and setup", 45.00)]),
    ("Granite Ridge Steel Supply", "Gary, IN", [
        ("GR-2301", "Steel plate A36 1/4in, 4x8 sheet", 312.00), ("GR-2335", "Square tube 2x2x1/8, 20ft", 96.40),
        ("GR-2368", "Angle iron 2x2x3/16, 20ft", 61.20), ("GR-2399", "Flat bar 1/4x2, 20ft", 48.75),
        ("GR-2430", "Round bar 1in, 12ft", 57.90)]),
    ("Tessaly Software Licensing", "Columbus, OH", [
        ("TS-2510", "CAD license, annual subscription", 2280.00), ("TS-2544", "ERP user license, annual", 960.00),
        ("TS-2580", "Endpoint security, per device annual", 42.00), ("TS-2615", "Support plan, premium", 1450.00)]),
    ("Ravensworth Pallet & Crate", "Lima, OH", [
        ("RP-2701", "Custom crate 48x40x36", 128.00), ("RP-2733", "Pallet, recycled 48x40", 9.50),
        ("RP-2766", "Skid, heavy duty 60x48", 42.00), ("RP-2798", "Crate repair, per unit", 18.00)]),
]
STREETS = ["Commerce Pkwy", "Industrial Dr", "Market St", "Riverside Ave", "Enterprise Blvd", "Lakeview Rd",
           "Mill St", "Harbor Way", "Summit Ave", "Canal St", "Depot Rd", "Granger Ct"]
BANKS = ["First Meridian Bank", "Harborline Trust", "Pinecrest National Bank", "Union Ledger Bank",
         "Great Lakes Commerce Bank", "Ashford Savings & Trust"]
TERMS = ["Net 30", "Net 30", "Net 45", "2/10 Net 30"]
INV_FORMATS = ["INV-{n:05d}", "{y}-{n:04d}", "A{n:05d}", "SI-{n:05d}", "{n:06d}"]
LAYOUTS = ["classic", "banner", "compact", "letterhead", "ledger"]
TAX_RATE = 0.0725  # Ohio-ish sales tax for vendors that charge it


@dataclass
class Line:
    sku: str
    description: str
    qty: int
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass
class Invoice:
    doc_id: str
    vendor_id: str
    invoice_number: str
    invoice_date: date
    po_number: str
    lines: list[Line]
    tax_rate: float
    terms: str
    bank_name: str
    routing_number: str
    account_number: str
    layout: str
    set: str = "batch"  # batch | live
    scanned: bool = False
    smudge: str = ""  # field obscured by an ink smudge on a fax-quality scan (drives human review)
    note: str = ""
    planted: list[str] = field(default_factory=list)

    @property
    def subtotal(self) -> float:
        return round(sum(l.amount for l in self.lines), 2)

    @property
    def tax(self) -> float:
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax, 2)

    @property
    def due_date(self) -> date:
        return self.invoice_date + timedelta(days=int(self.terms.split("Net ")[-1]))


def money(x: float) -> str:
    return f"{x:,.2f}"


def build(n_vendors: int, seed: int) -> dict:
    rng = random.Random(seed)
    vendors, pos, po_lines, receipts, payments, invoices = [], [], [], [], [], []
    po_seq, rcpt_seq, pay_seq = 4500, 8800, 30000

    for i, (name, city, items) in enumerate(CATALOG[:n_vendors]):
        vid = f"V{i + 1:03d}"
        layout = "ledger" if name.startswith("Kestrel") else LAYOUTS[i % 4]
        vendors.append({
            "vendor_id": vid, "name": name,
            "address": f"{rng.randint(100, 9800)} {rng.choice(STREETS)}, {city} {rng.randint(43000, 49999)}",
            "tax_id": f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
            "bank_name": rng.choice(BANKS), "routing_number": f"0{rng.randint(10000000, 99999999)}",
            "bank_account": str(rng.randint(10**9, 10**11)), "payment_terms": rng.choice(TERMS),
            "charges_tax": rng.random() < 0.5, "layout": layout, "inv_format": INV_FORMATS[i % len(INV_FORMATS)],
            "next_inv": 1000 + 600 * i + rng.randint(0, 200), "items": items, "status": "active",
        })

    def next_inv_number(v):
        v["next_inv"] += rng.randint(3, 40)
        return v["inv_format"].format(n=v["next_inv"], y=AS_OF.year)

    def new_po(v, po_date, lines):
        nonlocal po_seq, rcpt_seq
        po_seq += rng.randint(1, 9)
        po = f"PO-{po_seq}"
        pos.append({"po_number": po, "vendor_id": v["vendor_id"], "po_date": po_date.isoformat(),
                    "buyer": rng.choice(["M. Okafor", "J. Lindqvist", "R. Santos", "A. Patel"]), "status": "open"})
        for n, l in enumerate(lines, 1):
            po_lines.append({"po_number": po, "line_no": n, "sku": l.sku, "description": l.description,
                             "qty": l.qty, "unit_price": l.unit_price})
            rcpt_seq += 1
            receipts.append({"receipt_id": f"GR-{rcpt_seq}", "po_number": po, "line_no": n, "sku": l.sku,
                             "qty_received": l.qty, "received_date": (po_date + timedelta(days=rng.randint(3, 9))).isoformat()})
        return po

    def make_invoice(v, set_="batch", age=None, n_lines=None):
        items = v["items"]
        k = n_lines or rng.randint(2, min(5, len(items)))
        chosen = rng.sample(items, k)
        lines = [Line(s, d, rng.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 20, 24]), p) for s, d, p in chosen]
        inv_date = AS_OF - timedelta(days=age if age is not None else rng.randint(4, 40))
        po = new_po(v, inv_date - timedelta(days=rng.randint(12, 30)), lines)
        inv = Invoice(
            doc_id="", vendor_id=v["vendor_id"], invoice_number=next_inv_number(v), invoice_date=inv_date,
            po_number=po, lines=lines, tax_rate=TAX_RATE if v["charges_tax"] else 0.0, terms=v["payment_terms"],
            bank_name=v["bank_name"], routing_number=v["routing_number"], account_number=v["bank_account"],
            layout=v["layout"], set=set_,
        )
        invoices.append(inv)
        return inv

    # Payment history: invoices already paid (no PDFs; this is the ERP's record).
    for v in vendors:
        for _ in range(rng.randint(3, 5)):
            inv_date = AS_OF - timedelta(days=rng.randint(45, 200))
            pay_seq += 1
            payments.append({"payment_id": f"PAY-{pay_seq}", "vendor_id": v["vendor_id"],
                             "invoice_number": next_inv_number(v), "invoice_date": inv_date.isoformat(),
                             "amount": round(rng.uniform(150, 6000), 2),
                             "paid_date": (inv_date + timedelta(days=rng.randint(20, 40))).isoformat(), "payment_method": "ACH"})

    # Batch: 2-3 invoices per vendor, older than the 2/10 discount window unless chosen for it below.
    for v in vendors:
        for _ in range(rng.choice([2, 3, 3])):
            n = 16 if v["layout"] == "ledger" and not any(i.vendor_id == v["vendor_id"] for i in invoices) else None
            make_invoice(v, age=rng.randint(12, 40), n_lines=n)

    batch = [i for i in invoices if i.set == "batch"]
    pool = [i for i in batch if len(i.lines) < 10]
    rng.shuffle(pool)
    vendor = {v["vendor_id"]: v for v in vendors}

    def change_bank(inv, note):
        other = [b for b in BANKS if b != inv.bank_name]
        inv.bank_name, inv.routing_number = rng.choice(other), f"0{rng.randint(10000000, 99999999)}"
        inv.account_number = str(rng.randint(10**9, 10**11))
        inv.note = note
        inv.planted.append("BANK_MISMATCH")

    def make_duplicate(inv):
        nonlocal pay_seq
        pay_seq += 1
        inv.invoice_date = AS_OF - timedelta(days=rng.randint(32, 40))
        for p in pos:  # keep the PO dated before the (re-sent) invoice
            if p["po_number"] == inv.po_number:
                p["po_date"] = (inv.invoice_date - timedelta(days=rng.randint(12, 20))).isoformat()
        payments.append({"payment_id": f"PAY-{pay_seq}", "vendor_id": inv.vendor_id, "invoice_number": inv.invoice_number,
                         "invoice_date": inv.invoice_date.isoformat(), "amount": inv.total,
                         "paid_date": (inv.invoice_date + timedelta(days=18)).isoformat(), "payment_method": "ACH"})
        inv.planted.append("DUPLICATE")

    def overprice(inv):
        line = max(inv.lines, key=lambda l: l.amount)  # on the biggest line, so it matters
        if line.qty < 5:
            for coll, key in ((po_lines, "qty"), (receipts, "qty_received")):
                for row in coll:
                    if row["po_number"] == inv.po_number and row["sku"] == line.sku:
                        row[key] = 5
            line.qty = 5
        line.unit_price = round(line.unit_price * rng.uniform(1.10, 1.18), 2)
        inv.planted.append("PRICE_OVER_PO")

    def short_receipt(inv):
        n = max(range(len(inv.lines)), key=lambda k: inv.lines[k].unit_price)
        inv.lines[n].qty = max(inv.lines[n].qty, 8)
        for pl in po_lines:
            if pl["po_number"] == inv.po_number and pl["line_no"] == n + 1:
                pl["qty"] = inv.lines[n].qty
        for r in receipts:
            if r["po_number"] == inv.po_number and r["line_no"] == n + 1:
                r["qty_received"] = inv.lines[n].qty - rng.randint(2, 4)
        inv.planted.append("QTY_OVER_RECEIPT")

    def unknown_po(inv):
        global_pos = {p["po_number"] for p in pos}
        drop = inv.po_number
        pos[:] = [p for p in pos if p["po_number"] != drop]
        po_lines[:] = [p for p in po_lines if p["po_number"] != drop]
        receipts[:] = [r for r in receipts if r["po_number"] != drop]
        inv.po_number = f"PO-{rng.randint(7000, 7999)}"
        assert inv.po_number not in global_pos
        inv.planted.append("NO_PO")

    # Planted issues in the batch, one vendor per issue so the queue reads clearly.
    used = set()

    def pick(pred=lambda i: True):
        inv = next(i for i in pool if i.vendor_id not in used and pred(i))
        used.add(inv.vendor_id)
        return inv

    change_bank(pick(), "Please note: our banking details have changed. Kindly update your records and remit to the account below.")
    change_bank(pick(), "")
    make_duplicate(pick())
    overprice(pick())
    overprice(pick())
    short_receipt(pick())
    unknown_po(pick())
    # Two clean invoices from 2/10 vendors still inside the discount window -> "pay now" savings.
    for _ in range(2):
        inv = pick(lambda i: vendor[i.vendor_id]["payment_terms"].startswith("2/10") and not i.planted)
        inv.invoice_date = AS_OF - timedelta(days=rng.randint(3, 7))
    # Scanned copies: three clean, one with a planted bank change (detection survives a bad scan).
    clean = [i for i in batch if not i.planted and len(i.lines) < 10]
    for inv in rng.sample(clean, 3):
        inv.scanned = True
    next(i for i in batch if "BANK_MISMATCH" in i.planted and not i.note).scanned = True
    # Two fax-quality copies with an ink smudge over a key field: OCR confidence drops and they go to review.
    # Separate RNG so adding these does not change any other generated data.
    rng2 = random.Random(seed + 1)
    clean = [i for i in batch if not i.planted and not i.scanned and len(i.lines) < 10
             and not (vendor[i.vendor_id]["payment_terms"].startswith("2/10") and AS_OF <= i.invoice_date + timedelta(days=10))]
    for inv, fld in zip(rng2.sample(clean, 2), ("total", "invoice_number")):
        inv.scanned, inv.smudge = True, fld

    # Held back for the live upload: one per issue, from vendors without a batch issue.
    live_vendors = [v for v in vendors if v["vendor_id"] not in used]
    rng.shuffle(live_vendors)
    plants = [
        lambda i: change_bank(i, "IMPORTANT: Effective immediately, all payments must be sent to our new bank account."),
        make_duplicate, overprice, unknown_po,
    ]
    for v, plant in zip(live_vendors, plants):
        inv = make_invoice(v, set_="live", age=rng.randint(2, 6))
        plant(inv)

    for n, inv in enumerate(sorted(invoices, key=lambda i: (i.set, i.invoice_date, i.vendor_id)), 1):
        inv.doc_id = f"{'L' if inv.set == 'live' else 'B'}{n:03d}"

    return {"vendors": vendors, "purchase_orders": pos, "po_lines": po_lines, "goods_receipts": receipts,
            "payments": payments, "invoices": invoices}


# ---------- expected exceptions (reference implementation of the rules; rules.sql must agree) ----------


def expected_exceptions(data: dict) -> dict[str, list[dict]]:
    vendors = {v["vendor_id"]: v for v in data["vendors"]}
    po_numbers = {p["po_number"] for p in data["purchase_orders"]}
    po_price = {(p["po_number"], p["sku"]): p["unit_price"] for p in data["po_lines"]}
    received = {(r["po_number"], r["sku"]): r["qty_received"] for r in data["goods_receipts"]}
    paid = {(p["vendor_id"], p["invoice_number"]) for p in data["payments"]}
    out = {}
    for inv in data["invoices"]:
        v, ex = vendors[inv.vendor_id], []
        if inv.account_number != v["bank_account"]:
            ex.append({"rule": "BANK_MISMATCH", "amount": inv.total})
        if (inv.vendor_id, inv.invoice_number) in paid:
            ex.append({"rule": "DUPLICATE", "amount": inv.total})
        if inv.po_number not in po_numbers:
            ex.append({"rule": "NO_PO", "amount": inv.total})
        else:
            over = sum((l.unit_price - po_price[(inv.po_number, l.sku)]) * l.qty
                       for l in inv.lines if l.unit_price > po_price.get((inv.po_number, l.sku), l.unit_price))
            if over > 0:
                ex.append({"rule": "PRICE_OVER_PO", "amount": round(over, 2)})
            excess = sum((l.qty - received[(inv.po_number, l.sku)]) * l.unit_price
                         for l in inv.lines if l.qty > received.get((inv.po_number, l.sku), l.qty))
            if excess > 0:
                ex.append({"rule": "QTY_OVER_RECEIPT", "amount": round(excess, 2)})
        if not ex and inv.terms.startswith("2/10") and AS_OF <= inv.invoice_date + timedelta(days=10):
            ex.append({"rule": "DISCOUNT_WINDOW", "amount": round(inv.total * 0.02, 2)})
        out[inv.doc_id] = ex
    return out


# ---------- PDF rendering ----------

CSS = """
body { font-family: sans-serif; font-size: 10px; color: #222; }
h1 { font-size: 22px; margin: 0; }
.muted { color: #666; }
table.items { width: 100%; border-collapse: collapse; margin-top: 12px; }
table.items th { text-align: left; border-bottom: 1px solid #333; padding: 4px; }
table.items td { padding: 4px; border-bottom: 1px solid #ccc; }
.r { text-align: right; }
.note { border: 1px solid #b00; color: #b00; padding: 6px; margin-top: 10px; }
.box { border: 1px solid #999; padding: 6px; }
"""


def items_table(inv: Invoice, labels=("Item", "Description", "Qty", "Unit price", "Amount"), sku=True) -> str:
    head = "".join(f'<th class="{"r" if i >= 2 else ""}">{h}</th>' for i, h in enumerate(labels))
    rows = "".join(
        f"<tr><td>{l.sku}</td><td>{l.description}</td><td class='r'>{l.qty}</td>"
        f"<td class='r'>{money(l.unit_price)}</td><td class='r'>{money(l.amount)}</td></tr>"
        for l in inv.lines
    )
    return f'<table class="items"><tr>{head}</tr>{rows}</table>'


def totals(inv: Invoice, label_total="Total due") -> str:
    tax = f"<tr><td>Sales&nbsp;tax&nbsp;({inv.tax_rate * 100:.2f}%)</td><td class='r'>{money(inv.tax)}</td></tr>" if inv.tax_rate else ""
    return (f"<table style='width: 45%; margin-left: 55%; margin-top: 8px;'>"
            f"<tr><td>Subtotal</td><td class='r'>{money(inv.subtotal)}</td></tr>{tax}"
            f"<tr><td><b>{label_total.replace(' ', '&nbsp;')}</b></td><td class='r'><b>USD&nbsp;{money(inv.total)}</b></td></tr></table>")


def bank_block(inv: Invoice, title="Remit to") -> str:
    note = f"<div class='note'>{inv.note}</div>" if inv.note else ""
    return (f"{note}<div class='box' style='margin-top: 12px;'><b>{title}</b><br>{inv.bank_name}<br>"
            f"Routing (ABA): {inv.routing_number}<br>Account: {inv.account_number}</div>")


def bill_to() -> str:
    return f"<b>Bill to</b><br>{BUYER['name']}<br>{'<br>'.join(BUYER['address'])}<br>{BUYER['ap_email']}"


def html_for(inv: Invoice, v: dict) -> str:
    d, due = inv.invoice_date.strftime("%B %d, %Y"), inv.due_date.strftime("%B %d, %Y")
    vend = f"<b>{v['name']}</b><br>{v['address']}<br>Tax ID: {v['tax_id']}"
    if inv.layout == "classic":
        return f"""
<table style="width:100%"><tr><td>{vend}</td><td class="r"><h1>INVOICE</h1>
Invoice #: {inv.invoice_number}<br>Date: {d}<br>PO #: {inv.po_number}<br>Terms: {inv.terms}<br>Due: {due}</td></tr></table>
<p>{bill_to()}</p>{items_table(inv)}{totals(inv)}{bank_block(inv)}"""
    if inv.layout == "banner":
        return f"""
<div style="background-color:#1f3a5f; padding:10px;"><b style="color:#ffffff; font-size:18px;">{v['name']}</b><br>
<span style="color:#dfe7f1;">{v['address']} | Tax ID {v['tax_id']}</span></div>
<h1 style="margin-top:10px;">Tax Invoice</h1>
<table style="width:100%"><tr><td>{bill_to()}</td><td>
<table><tr><td class="muted">Invoice No.</td><td>{inv.invoice_number}</td></tr>
<tr><td class="muted">Invoice Date</td><td>{d}</td></tr><tr><td class="muted">Purchase Order</td><td>{inv.po_number}</td></tr>
<tr><td class="muted">Payment Terms</td><td>{inv.terms}</td></tr><tr><td class="muted">Due Date</td><td>{due}</td></tr></table>
</td></tr></table>{items_table(inv, ("Product code", "Description", "Quantity", "Price", "Line total"))}{totals(inv, "Amount due")}
{bank_block(inv, "Payment by ACH")}"""
    if inv.layout == "compact":
        return f"""
<p style="font-size:14px;"><b>{v['name']}</b></p><p class="muted">{v['address']} - EIN {v['tax_id']}</p>
<table style="width:100%; margin-top:8px;" class="box"><tr><td><b>Bill No.</b> {inv.invoice_number}</td>
<td><b>Dated</b> {inv.invoice_date.strftime('%m/%d/%Y')}</td><td><b>Your ref</b> {inv.po_number}</td></tr>
<tr><td><b>Terms</b> {inv.terms}</td><td><b>Due</b> {inv.due_date.strftime('%m/%d/%Y')}</td><td></td></tr></table>
<p>{bill_to()}</p>{items_table(inv, ("SKU", "Item", "Qty", "Rate", "Amount"))}{totals(inv, "Balance due")}{bank_block(inv, "Wire / ACH instructions")}"""
    if inv.layout == "letterhead":
        return f"""
<p style="text-align:center; font-size:16px;"><b>{v['name'].upper()}</b></p>
<p style="text-align:center;" class="muted">{v['address']}<br>Federal Tax ID {v['tax_id']}</p>
<hr><p>{bill_to()}</p>
<p>Invoice number <b>{inv.invoice_number}</b>, issued {d}, against purchase order <b>{inv.po_number}</b>.
Payment terms: {inv.terms}; payment is due by {due}.</p>
{items_table(inv)}{totals(inv)}{bank_block(inv, "Please remit payment to")}
<p class="muted" style="margin-top:10px;">Thank you for your business.</p>"""
    # ledger: long line list that spans two pages
    return f"""
<table style="width:100%"><tr><td><h1>{v['name']}</h1>{v['address']}<br>Tax ID {v['tax_id']}</td>
<td class="r"><b>INVOICE {inv.invoice_number}</b><br>Date {d}<br>Customer PO {inv.po_number}<br>{inv.terms}, due {due}</td></tr></table>
<p>{bill_to()}</p>
<table class="items"><tr><th>#</th><th>Catalog no.</th><th>Description</th><th class="r">Qty</th><th class="r">Unit</th><th class="r">Ext. price</th></tr>
{''.join(f"<tr><td>{n}</td><td>{l.sku}</td><td>{l.description}</td><td class='r'>{l.qty}</td><td class='r'>{money(l.unit_price)}</td><td class='r'>{money(l.amount)}</td></tr><tr><td></td><td></td><td class='muted' colspan='4'>Lot {inv.invoice_number[-3:]}{n:02d}, ships from Ann Arbor DC, hazard class: none</td></tr>" for n, l in enumerate(inv.lines, 1))}
</table>{totals(inv)}{bank_block(inv)}"""


def render(inv: Invoice, v: dict, path: Path) -> None:
    story = pymupdf.Story(html=html_for(inv, v), user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    mediabox = pymupdf.paper_rect("letter")
    where = mediabox + (48, 48, -48, -60)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open("pdf", buf.getvalue())
    for n, page in enumerate(doc, 1):
        page.insert_text((48, mediabox.height - 30), f"{v['name']}  |  Invoice {inv.invoice_number}  |  Page {n} of {doc.page_count}",
                         fontsize=7, color=(0.45, 0.45, 0.45))
    if inv.scanned:
        smudge = {"total": money(inv.total), "invoice_number": inv.invoice_number}.get(inv.smudge)
        doc = scan(doc, random.Random(inv.doc_id), smudge)
    doc.save(path, garbage=4, deflate=True)


def scan(doc, rng: random.Random, smudge: str | None = None):
    """Make a clean PDF look like a mediocre office scan: raster, slight skew, noise, blur, JPEG.
    With smudge (text on the page), it becomes a fax-quality copy with an ink blot over that text."""
    out = pymupdf.open()
    dpi, noise, blur, quality = (110, 22, 0.9, 40) if smudge else (150, 14, 0.6, 55)
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        if smudge:
            arr = np.asarray(img).astype(np.float32)
            yy, xx = np.mgrid[0:img.height, 0:img.width]
            for r in page.search_for(smudge):
                r = r * (dpi / 72)
                cx, cy = r.x0 + r.width * 0.62, (r.y0 + r.y1) / 2  # covers the right part of the value
                rx, ry = r.width * 0.42, r.height * 0.9
                blot = np.exp(-(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) ** 1.5)
                arr = arr * (1 - 0.82 * blot) + 60 * 0.82 * blot
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        img = img.rotate(rng.uniform(0.5, 1.5) * rng.choice([-1, 1]), fillcolor=255, resample=Image.BICUBIC)
        arr = np.asarray(img).astype(np.int16) + np.random.default_rng(rng.randint(0, 10**6)).normal(0, 14, (img.height, img.width)).astype(np.int16)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
        jpg = io.BytesIO()
        img.save(jpg, "JPEG", quality=55)
        p = out.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=jpg.getvalue())
    return out


# ---------- output ----------


def write(data: dict, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    for sub in ("pdfs", "live", "tables"):
        (out / sub).mkdir(parents=True)
    vendors = {v["vendor_id"]: v for v in data["vendors"]}
    tables = {
        "vendors": [{k: v[k] for k in ("vendor_id", "name", "address", "tax_id", "bank_name", "routing_number",
                                       "bank_account", "payment_terms", "status")} for v in data["vendors"]],
        **{k: data[k] for k in ("purchase_orders", "po_lines", "goods_receipts", "payments")},
    }
    for name, rows in tables.items():
        with open(out / "tables" / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    expected = expected_exceptions(data)
    truth = {}
    for inv in data["invoices"]:
        fname = f"{inv.doc_id}_{inv.vendor_id}_{inv.invoice_number}.pdf"
        render(inv, vendors[inv.vendor_id], out / ("live" if inv.set == "live" else "pdfs") / fname)
        rec = asdict(inv)
        rec.update(file=fname, vendor_name=vendors[inv.vendor_id]["name"], invoice_date=inv.invoice_date.isoformat(),
                   due_date=inv.due_date.isoformat(), subtotal=inv.subtotal, tax=inv.tax, total=inv.total,
                   expected_exceptions=expected[inv.doc_id])
        for l in rec["lines"]:
            l["amount"] = round(l["qty"] * l["unit_price"], 2)
        truth[inv.doc_id] = rec
    (out / "ground_truth.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "invoices": truth}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vendors", type=int, default=15, help=f"number of vendors (max {len(CATALOG)})")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=DATA)
    args = ap.parse_args()
    data = build(min(args.vendors, len(CATALOG)), args.seed)
    write(data, args.out)

    invs = data["invoices"]
    exp = expected_exceptions(data)
    print(f"{len(data['vendors'])} vendors, {len(data['purchase_orders'])} POs, {len(data['payments'])} payments")
    for set_ in ("batch", "live"):
        group = [i for i in invs if i.set == set_]
        print(f"\n{set_}: {len(group)} invoices ({sum(i.scanned for i in group)} scanned)")
        for i in group:
            if exp[i.doc_id] or i.scanned:
                flags = ", ".join(f"{e['rule']} ${e['amount']:,.2f}" for e in exp[i.doc_id]) or "clean"
                print(f"  {i.doc_id} {i.vendor_id} {i.invoice_number:<11} {i.layout:<10} {'scanned ' if i.scanned else ''}{'smudged ' + i.smudge + ' ' if i.smudge else ''}{flags}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()

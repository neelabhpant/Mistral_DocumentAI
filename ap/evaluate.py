"""Score the pipeline against the generator's ground truth: field-level extraction accuracy and
whether the action queue holds exactly the planted issues.

Usage: python -m ap.evaluate
"""

import json
from collections import defaultdict

from ap import lake
from ap.config import DATA, DB

HEADER = {  # ground-truth key -> extracted field
    "vendor_name": "vendor_name", "invoice_number": "invoice_number", "invoice_date": "invoice_date",
    "due_date": "due_date", "po_number": "po_number", "terms": "payment_terms", "bank_name": "bank_name",
    "routing_number": "routing_number", "account_number": "bank_account", "total": "total",
}
NOT_PLANTED = {"LOW_CONFIDENCE"}  # depends on the read, not on the data


def _norm(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    try:
        return f"{float(s.replace(',', '')):.2f}"
    except ValueError:
        return s.lower()


def evaluate() -> dict:
    truth = json.loads((DATA / "ground_truth.json").read_text())["invoices"]
    rows = lake.query(f"SELECT doc_id, field_name, line_no, field_value FROM {DB}.extractions")
    got = defaultdict(dict)
    for r in rows:
        got[r["doc_id"]][(r["field_name"], r["line_no"])] = r["field_value"]

    fields, misses = [], []
    for doc_id, t in truth.items():
        if doc_id not in got:
            continue
        g = got[doc_id]
        for tk, f in HEADER.items():
            ok = _norm(t[tk]) == _norm(g.get((f, None)))
            fields.append(ok)
            if not ok:
                misses.append((doc_id, f, t[tk], g.get((f, None))))
        for n, line in enumerate(t["lines"], 1):
            for tk, f in (("sku", "sku"), ("qty", "quantity"), ("unit_price", "unit_price"), ("amount", "amount")):
                ok = _norm(line[tk]) == _norm(g.get((f, n)))
                fields.append(ok)
                if not ok:
                    misses.append((doc_id, f"line {n} {f}", line[tk], g.get((f, n))))
        extra = {k for k in g if k[1] and k[1] > len(t["lines"])}
        if extra:
            misses.append((doc_id, "extra lines", len(t["lines"]), max(k[1] for k in extra)))
            fields.append(False)

    exc = defaultdict(lambda: defaultdict(float))
    review = set()
    for r in lake.query(f"SELECT doc_id, rule_code, amount_at_risk FROM {DB}.exceptions"):
        if r["rule_code"] == "LOW_CONFIDENCE":
            review.add(r["doc_id"])
        if r["rule_code"] not in NOT_PLANTED:
            exc[r["doc_id"]][r["rule_code"]] += float(r["amount_at_risk"] or 0)
    queue = []
    for doc_id, t in truth.items():
        if doc_id not in got:
            continue
        want = {e["rule"]: e["amount"] for e in t["expected_exceptions"]}
        have = dict(exc.get(doc_id, {}))
        status = "ok" if want.keys() == have.keys() and all(abs(want[k] - have[k]) < 0.01 for k in want) else "WRONG"
        if want or have or status != "ok":
            queue.append({"doc_id": doc_id, "expected": want, "found": have, "status": status})
    return {"docs": len(got), "review": sorted(review), "field_accuracy": sum(fields) / max(len(fields), 1), "fields": len(fields),
            "misses": misses, "queue": queue}


def main() -> None:
    r = evaluate()
    print(f"{r['docs']} documents, {r['fields']} fields, extraction accuracy {r['field_accuracy']:.1%}")
    for m in r["misses"]:
        caught = " (caught: routed to human review)" if m[0] in r["review"] else " (NOT routed to review)"
        print(f"  miss {m[0]} {m[1]}: expected {m[2]!r}, got {m[3]!r}{caught}")
    print(f"Routed to human review (low confidence): {', '.join(r['review']) or 'none'}")
    wrong = [q for q in r["queue"] if q["status"] != "ok"]
    print(f"\nAction queue: {len(r['queue']) - len(wrong)}/{len(r['queue'])} documents match the planted issues")
    for q in r["queue"]:
        print(f"  {q['status']:<5} {q['doc_id']} expected {q['expected']} found {q['found']}")


if __name__ == "__main__":
    main()

"""API tests: the real app and ap/ code on stand-ins for Impala, S3 and Mistral (tests/fakes.py), over a lake
bootstrapped the way the AMP does it, with a subset of the batch invoices.

Run from the repo root: python -m unittest discover -s tests -t . -v
"""

import contextlib
import io
import re
import tempfile
import time
import unittest
from pathlib import Path

import pymupdf

from tests import fakes

# Text-layer batch invoices: one per planted issue, the two-page ledger (B037) and two clean ones (B005, B012).
DOCS = ["B005", "B006", "B008", "B009", "B010", "B012", "B019", "B021", "B037", "B039"]
OVERRIDES = {"B005": {"total": (None, 0.41)}}  # B005's total is read right but unsure: it goes to human review
STATIC = fakes.REPO / "static"
TAB_KEY = "tab-key-for-the-tests-0123456789"
REVOKED_KEY = "revoked-key-0123456789"
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
SENTINELS = re.compile(r"(?:[^\ue000-\ue002]|\ue000\d+\ue001[^\ue000-\ue002]*\ue002)*")  # balanced, never nested

tmp = tempfile.TemporaryDirectory(prefix="docai-tests-")
app = client = None


def setUpModule():
    global app, client
    from fastapi.testclient import TestClient

    app = fakes.install(Path(tmp.name), overrides=OVERRIDES)
    with contextlib.redirect_stdout(io.StringIO()):  # the setup jobs' progress lines
        fakes.bootstrap(DOCS)
    client = TestClient(app.app)


def tearDownModule():
    tmp.cleanup()


def post(path: str, json=None, key: str | None = None, **kwargs):
    headers = {"X-DocAI": "1", **({"X-Mistral-Key": key} if key else {})}
    return client.post(path, json=json, headers=headers, **kwargs)


def wait(response, seen: list | None = None) -> dict:
    """The job a 202 response started, once it has finished. seen collects every response body on the way."""
    if response.status_code != 202:
        raise AssertionError(f"{response.status_code}: {response.text}")
    for _ in range(600):
        r = client.get(f"/api/jobs/{response.json()['job_id']}")
        if seen is not None:
            seen += [response.text, r.text]
        if r.json()["state"] != "running":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("The job is still running")


def planted() -> dict:
    """(doc_id, rule) -> amount, from the ground truth."""
    truth = fakes.ground_truth()
    return {(d, e["rule"]): e["amount"] for d in DOCS for e in truth[d]["expected_exceptions"]}


def reset_uploads() -> dict:
    return wait(post("/api/reset"))


def reread(doc_id: str) -> None:
    """OCR a document again, as the batch Job would (this undoes a review)."""
    from ap import pipeline

    with app.LAKE:
        pipeline.process(pipeline.documents(f"doc_id = '{doc_id}'"), client=fakes.FakeMistral(fakes.PROJECT_KEY))
        pipeline.run_rules()
    app.clear_cache()


class AppTest(unittest.TestCase):
    def setUp(self):
        # The server's log: the pipeline's progress lines, and tracebacks of the failures the tests cause on purpose.
        self.log = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.log))
        self.enterContext(contextlib.redirect_stderr(self.log))

    def test_config_and_the_project_key_flag(self):
        r = client.get("/api/config")
        c = r.json()
        self.assertEqual(c["ocr_model"], "mistral-ocr-4-1")
        self.assertEqual((c["db"], c["s3_root"]), ("docai_ap", fakes.S3_ROOT))
        self.assertEqual(c["landing"], f"{fakes.S3_ROOT}/landing")
        self.assertEqual((c["reviewer"], c["min_confidence"], c["price_per_page"]), ("test.reviewer", 0.85, 0.004))
        self.assertEqual(c["rules"]["BANK_MISMATCH"], {"label": "Bank details changed",
                                                       "how": "invoice remit-to vs vendor master", "tone": "risk"})
        self.assertEqual(len(c["rules"]), 8)
        self.assertEqual(c["priorities"], {"1": "1 · Critical", "2": "2 · High", "3": "3 · Medium", "4": "Opportunity",
                                           "5": "Review"})
        self.assertEqual(len(c["steps"]), 6)
        self.assertEqual(c["samples"], sorted(p.name for p in (fakes.DATA / "live").glob("*.pdf")))
        self.assertIs(c["env_key"], True)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["referrer-policy"], "same-origin")

        fakes.set_project_key(False)
        self.addCleanup(fakes.set_project_key, True)
        self.assertIs(client.get("/api/config").json()["env_key"], False)

    def test_queue_and_totals_have_the_planted_issues(self):
        from ap.schema import HEADER_FIELDS, LINE_FIELDS

        q = client.get("/api/queue").json()
        rows = q["rows"]
        found = {(r["doc_id"], r["rule_code"]): r["amount_at_risk"] for r in rows if r["rule_code"] != "LOW_CONFIDENCE"}
        self.assertEqual(found.keys(), planted().keys())
        for issue, amount in planted().items():
            self.assertAlmostEqual(found[issue], amount, places=2, msg=issue)
        review = [(r["doc_id"], r["evidence_field"]) for r in rows if r["rule_code"] == "LOW_CONFIDENCE"]
        self.assertEqual(review, [("B005", "total")])
        self.assertEqual([r["priority"] for r in rows], sorted(r["priority"] for r in rows))
        b021 = next(r for r in rows if r["doc_id"] == "B021")
        self.assertEqual((b021["vendor_name"], b021["source"]), ("Ardent Facility Services", "batch"))
        self.assertEqual(b021["file_name"], "B021_V007_2026-4807.pdf")

        risk = {issue: amount for issue, amount in planted().items() if issue[1] != "DISCOUNT_WINDOW"}
        truth = fakes.ground_truth()
        self.assertEqual(q["kpis"], {
            "processed": len(DOCS),
            "fields": sum(len(HEADER_FIELDS) + len(LINE_FIELDS) * len(truth[d]["lines"]) for d in DOCS),
            "actions": len(planted()) + 1,
            "need_action": len({d for d, _ in risk}),
            "at_risk": round(sum(risk.values()), 2),
            "discounts": round(sum(a for (_, rule), a in planted().items() if rule == "DISCOUNT_WINDOW"), 2),
            "review": 1,
        })

    def test_issue_detail(self):
        d = client.get("/api/exception", params={"doc_id": "B021", "rule_code": "BANK_MISMATCH",
                                                 "evidence_field": "bank_account"}).json()
        self.assertEqual((d["label"], d["how"], d["tone"]),
                         ("Bank details changed", "invoice remit-to vs vendor master", "risk"))
        self.assertEqual((d["document"]["doc_id"], d["page_count"]), ("B021", 1))
        marks = d["evidence"]["marks"]
        self.assertEqual([m["label"].split(" · ")[0] for m in marks],
                         ["bank account", "bank name", "routing number", "bank change notice"])
        self.assertEqual([m["primary"] for m in marks], [True, False, False, False])
        self.assertTrue(all(0 <= v <= 1 for m in marks for v in m["bbox"]))
        self.assertEqual(d["evidence"]["page"], 1)
        self.assertEqual(d["evidence"]["caption"]["model"], "mistral-ocr-4-1")
        self.assertEqual([c["what"] for c in d["comparison"]], ["Bank", "Routing (ABA)", "Account",
                                                                "Change notice on invoice"])
        self.assertEqual({c["table"] for c in d["comparison"]}, {"docai_ap.vendors"})
        self.assertFalse(any(c["ok"] for c in d["comparison"]))
        self.assertIsNone(d["review"])
        self.assertEqual(d["others"], [])
        self.assertIn("'BANK_MISMATCH'", d["rule_sql"])
        self.assertEqual(d["audit"]["ocr_uri"], f"{fakes.S3_ROOT}/ocr/B021.json")
        self.assertEqual(d["audit"]["extractions_table"], "docai_ap.extractions")

        # A line-item rule points at its table row and compares with the PO line.
        row = next(r for r in client.get("/api/queue").json()["rows"] if r["doc_id"] == "B006")
        d = client.get("/api/exception", params={k: row[k] for k in ("doc_id", "rule_code", "evidence_field")}).json()
        self.assertTrue(d["evidence"]["marks"][0]["label"].startswith("unit price · "))
        unit_price = next(c for c in d["comparison"] if c["what"] == "Unit price")
        self.assertEqual((unit_price["ok"], unit_price["table"]), (False, "docai_ap.po_lines"))

        r = client.get("/api/exception", params={"doc_id": "B012", "rule_code": "NO_PO", "evidence_field": "po_number"})
        self.assertEqual(r.status_code, 404)

    def test_one_document(self):
        d = client.get("/api/docs/B037").json()
        self.assertEqual((d["page_count"], len(d["pages"])), (2, 2))
        self.assertEqual(d["document"]["file_name"], "B037_V006_INV-04185.pdf")
        self.assertAlmostEqual(d["cost"], 0.008)
        self.assertEqual(d["block_count"], sum(len(p["blocks"]) for p in d["pages"]))
        self.assertIn("| KS-6118 |", d["pages"][0]["markdown"])  # the line-item table, inlined
        for page in d["pages"]:
            self.assertEqual((page["width"], page["height"], page["dpi"]), (1700, 2200, 200))
            self.assertTrue(page["blocks"])
            self.assertTrue(all(0 <= v <= 1 for b in page["blocks"] for v in b["bbox"]))
        self.assertEqual(len(d["fields"]), 16 + 5 * 16)
        self.assertEqual(set(d["fields"][0]), {"field_name", "line_no", "field_value", "confidence", "page", "model"})
        confidences = [w["confidence"] for w in d["least_certain"]]
        self.assertEqual(confidences, sorted(confidences))
        self.assertEqual(len(confidences), 15)
        self.assertEqual(d["chat"]["tools"][0]["function"]["parameters"]["properties"]["name"]["enum"], ["B037"])
        self.assertTrue(d["chat"]["system_prompt"].endswith("Loaded invoices: B037"))
        self.assertEqual(d["schema"]["json_schema"]["name"], "InvoiceFields")

        # Words below the review threshold come wrapped in sentinels, one confidence each.
        page = client.get("/api/docs/B005").json()["pages"][0]
        self.assertTrue(page["marks"])
        for p in d["pages"] + [page]:
            self.assertTrue(SENTINELS.fullmatch(p["markdown"]))
            numbers = [int(n) for n in re.findall("\ue000(\\d+)\ue001", p["markdown"])]
            self.assertEqual(numbers, list(range(len(p["marks"]))))
        self.assertTrue(all(c < 0.85 for c in page["marks"]))

        r = client.get("/api/docs/B037/pages/2.png", params={"dpi": 72, "v": "abc"})
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        self.assertEqual(r.headers["cache-control"], "private, max-age=86400")
        for n in (0, 3):
            self.assertEqual(client.get(f"/api/docs/B037/pages/{n}.png").status_code, 404)

        raw = client.get("/api/docs/B037/ocr.json").json()
        self.assertEqual((len(raw["pages"]), raw["model"]), (2, "mistral-ocr-4-1"))
        self.assertIn("_seconds", raw)
        self.assertEqual(len(client.get("/api/docs").json()["docs"]), len(DOCS))

    def test_review_rejects_a_bad_amount_and_confirming_rechecks(self):
        params = {"doc_id": "B005", "rule_code": "LOW_CONFIDENCE", "evidence_field": "total"}
        total = fakes.ground_truth()["B005"]["total"]
        d = client.get("/api/exception", params=params).json()
        self.assertEqual((d["tone"], d["review"]), ("review", {"field": "total", "value": f"{total:.2f}"}))

        r = post("/api/docs/B005/confirm", {"field": "total", "value": "twelve"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "Total must be an amount, for example 2273.60."})
        for body in ({"field": "colour", "value": "1"}, {"field": "total", "value": "1" * 201}, {"field": "total"}):
            self.assertEqual(post("/api/docs/B005/confirm", body).status_code, 400, body)

        self.addCleanup(reread, "B005")
        job = wait(post("/api/docs/B005/confirm", {"field": "total", "value": f"${total:,.2f}"}))
        self.assertEqual(job["state"], "done", job["error"])
        self.assertEqual(job["kind"], "confirm")
        self.assertEqual(job["result"], {"doc_id": "B005", "field": "total", "value": f"{total:.2f}"})
        self.assertEqual([(s["name"], s["state"]) for s in job["steps"]], [("Save and re-check", "done")])
        rows = client.get("/api/queue").json()["rows"]
        self.assertNotIn("LOW_CONFIDENCE", [r["rule_code"] for r in rows])
        self.assertEqual(client.get("/api/exception", params=params).status_code, 404)
        reviewed = next(f for f in client.get("/api/docs/B005").json()["fields"] if f["field_name"] == "total")
        self.assertEqual((reviewed["confidence"], reviewed["model"]), (1.0, "reviewed by test.reviewer"))

    def test_ingest_with_a_tab_key(self):
        before, seen = len(fakes.FakeMistral.keys), []
        self.addCleanup(reset_uploads)
        job = wait(post("/api/ingest", key=TAB_KEY, data={"sample": "L043_V006_INV-04238.pdf"}), seen)
        self.assertEqual(job["state"], "done", job["error"])
        self.assertEqual([(s["name"], s["state"]) for s in job["steps"]],
                         [("Land in S3", "done"), ("OCR 4.1 + evidence", "done"), ("Rules in Impala", "done")])
        self.assertIn(fakes.S3_ROOT, job["steps"][0]["label"])
        result = job["result"]
        self.assertEqual(set(result["times"]), {"Land in S3", "OCR 4.1 + evidence", "Rules in Impala"})
        (expected,) = fakes.ground_truth()["L043"]["expected_exceptions"]
        self.assertEqual(expected["rule"], "BANK_MISMATCH")
        self.assertEqual([(e["rule_code"], e["label"], e["tone"]) for e in result["exceptions"]],
                         [("BANK_MISMATCH", "Bank details changed", "risk")])
        self.assertAlmostEqual(result["exceptions"][0]["amount_at_risk"], expected["amount"], places=2)
        self.assertEqual(fakes.FakeMistral.keys[before:], [TAB_KEY])  # one client, made with the tab's key

        queue = client.get("/api/queue")
        row = next(r for r in queue.json()["rows"] if r["doc_id"] == result["doc_id"])
        self.assertEqual((row["rule_code"], row["source"]), ("BANK_MISMATCH", "upload"))
        for body in seen + [queue.text, client.get("/api/docs").text, self.log.getvalue()]:
            self.assertNotIn(TAB_KEY, body)

        self.assertEqual(reset_uploads()["result"], {"removed": 1})
        self.assertNotIn(result["doc_id"], [r["doc_id"] for r in client.get("/api/queue").json()["rows"]])

    def test_upload(self):
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Acme Widgets Ltd\nInvoice # AC-1001\nTotal due 10.00")
        self.addCleanup(reset_uploads)
        r = client.post("/api/ingest", headers={"X-DocAI": "1"},
                        files={"file": ("../../Acme invoice.PDF", pdf.tobytes(), "application/pdf")})
        job = wait(r)
        self.assertEqual(job["state"], "done", job["error"])
        doc = next(d for d in client.get("/api/docs").json()["docs"] if d["doc_id"] == job["result"]["doc_id"])
        self.assertEqual((doc["file_name"], doc["source"], doc["status"]), ("Acme_invoice.PDF", "upload", "processed"))
        self.assertEqual([e["rule_code"] for e in job["result"]["exceptions"]], ["UNKNOWN_VENDOR"])

        a_pdf = {"file": ("x.pdf", b"%PDF-1.7", "application/pdf")}
        bad = [{"files": {"file": ("notes.pdf", b"hello", "application/pdf")}},  # not a PDF
               {"data": {"sample": "../ground_truth.json"}},  # not a sample
               {"data": {"sample": "L043_V006_INV-04238.pdf"}, "files": a_pdf},  # both
               {}]  # neither
        for kwargs in bad:
            r = client.post("/api/ingest", headers={"X-DocAI": "1"}, **kwargs)
            self.assertEqual(r.status_code, 400, kwargs)
            self.assertIn("error", r.json())

    def test_no_key_anywhere(self):
        fakes.set_project_key(False)
        self.addCleanup(fakes.set_project_key, True)
        job = wait(post("/api/ingest", data={"sample": "L041_V002_2026-1824.pdf"}))
        self.assertEqual((job["state"], job["error"], job["steps"]), ("error", app.NO_KEY, []))  # nothing landed
        self.assertNotIn("L041_V002_2026-1824.pdf", [d["file_name"] for d in client.get("/api/docs").json()["docs"]])
        r = post("/api/docs/B021/chat", {"question": "Which bank?"})
        self.assertEqual((r.status_code, r.json()), (400, {"error": app.NO_KEY}))
        self.assertEqual(post("/api/mistral/check").status_code, 400)

    def test_chat_keeps_a_clean_conversation(self):
        first, second = "Which bank account does this invoice ask us to pay?", "And the routing number?"
        r = post("/api/docs/B021/chat", {"question": first})
        self.assertEqual(r.status_code, 200, r.text)
        a = r.json()
        self.assertIn("(p. 1)", a["answer"])
        self.assertEqual([(c["tool"], c["name"]) for c in a["calls"]], [("read_document", "B021")])
        self.assertGreater(a["calls"][0]["chars"], 500)
        chat_id = a["chat_id"]

        # Failed turns (Mistral rejects the tab's key, or there's no key) aren't kept: the retry sends the question once.
        fakes.FakeMistral.rejected.add(REVOKED_KEY)
        self.addCleanup(fakes.FakeMistral.rejected.discard, REVOKED_KEY)
        r = post("/api/docs/B021/chat", {"chat_id": chat_id, "question": second}, key=REVOKED_KEY)
        self.assertEqual((r.status_code, r.json()), (502, {"error": "Mistral rejected this key."}))
        fakes.set_project_key(False)
        self.addCleanup(fakes.set_project_key, True)
        self.assertEqual(post("/api/docs/B021/chat", {"chat_id": chat_id, "question": second}).status_code, 400)
        r = post("/api/docs/B021/chat", {"chat_id": chat_id, "question": second}, key=TAB_KEY)
        self.assertEqual(r.json()["chat_id"], chat_id)
        users = [m["content"] for m in app.CHATS[chat_id]["messages"] if m["role"] == "user"]
        self.assertEqual(users, [first, second])
        for key in (TAB_KEY, REVOKED_KEY):
            self.assertNotIn(key, r.text + str(app.CHATS[chat_id]) + self.log.getvalue())

        # Another document's chat_id (or an unknown one) starts a new conversation.
        r = post("/api/docs/B006/chat", {"chat_id": chat_id, "question": "What is the total?"}, key=TAB_KEY)
        self.assertNotEqual(r.json()["chat_id"], chat_id)

    def test_key_check(self):
        r = post("/api/mistral/check", key=TAB_KEY)
        self.assertEqual(r.json(), {"ok": True, "ocr_model": "mistral-ocr-4-1", "ocr_available": True})
        fakes.FakeMistral.rejected.add(REVOKED_KEY)
        self.addCleanup(fakes.FakeMistral.rejected.discard, REVOKED_KEY)
        r = post("/api/mistral/check", key=REVOKED_KEY)
        self.assertEqual((r.status_code, r.json()), (502, {"error": "Mistral rejected this key."}))
        for malformed in ("too-short", "has spaces 0123456789abcdef", "k" * 257):
            r = post("/api/mistral/check", key=malformed)
            self.assertEqual((r.status_code, r.json()), (400, {"error": "That doesn't look like a Mistral API key."}))
        self.assertNotIn(REVOKED_KEY, self.log.getvalue())
        self.assertIn("SDKError", self.log.getvalue())  # the failure itself is logged

    def test_posts_need_the_csrf_header(self):
        for path in ("/api/refresh", "/api/reset", "/api/evaluate", "/api/ingest", "/api/docs/B021/confirm",
                     "/api/docs/B021/chat", "/api/mistral/check"):
            r = client.post(path)
            self.assertEqual((r.status_code, r.json()), (403, {"error": "Missing X-DocAI header"}), path)
        self.assertEqual(client.post("/api/refresh", headers={"X-DocAI": "0"}).status_code, 403)
        self.assertEqual(post("/api/refresh").json(), {"ok": True})

    def test_malformed_and_unknown_ids(self):
        for path, status in [("/api/docs/bad!id", 400), ("/api/docs/%2E%2E/ocr.json", 400), ("/api/docs/B0%2021", 400),
                             (f"/api/docs/{'B' * 65}", 400), ("/api/docs/B005/pages/one.png", 400),
                             ("/api/jobs/not-hex", 400), ("/api/jobs/abc123", 404), ("/api/docs/B999", 404),
                             ("/api/docs/B999/pages/1.png", 404), ("/api/exception", 400), ("/api/nowhere", 404)]:
            r = client.get(path)
            self.assertEqual(r.status_code, status, path)
            self.assertIsInstance(r.json()["error"], str)
        good = {"doc_id": "B021", "rule_code": "BANK_MISMATCH", "evidence_field": "bank_account"}
        for bad in ({"doc_id": "../B021"}, {"rule_code": "DROP_TABLE"}, {"evidence_field": "line 1"}):
            self.assertEqual(client.get("/api/exception", params={**good, **bad}).status_code, 400, bad)
        for body in ({"chat_id": "../x", "question": "Total?"}, {"question": " "}, {"question": "x" * 2001}, {}):
            self.assertEqual(post("/api/docs/B021/chat", body, key=TAB_KEY).status_code, 400, body)
        self.assertEqual(post("/api/docs/..%2FB021/confirm", {"field": "total", "value": "1"}).status_code, 404)
        self.assertEqual(post("/api/docs/B0!1/confirm", {"field": "total", "value": "1"}).status_code, 400)

    def test_lake_tables_rules_and_architecture(self):
        from ap.setup_lake import TABLES

        d = client.get("/api/lake").json()
        self.assertEqual((d["s3_root"], d["db"]), (fakes.S3_ROOT, "docai_ap"))
        self.assertEqual([t["table"] for t in d["tables"]], [f"docai_ap.{t}" for t in TABLES])
        self.assertEqual([t["kind"] for t in d["tables"]], ["Enterprise data"] * 5 + ["Document AI"] * 3)
        rows = {t["table"]: t["rows"] for t in d["tables"]}
        self.assertEqual((rows["docai_ap.vendors"], rows["docai_ap.documents"]), (15, len(DOCS)))
        self.assertEqual(d["tables"][0]["comment"], TABLES["vendors"][0])
        self.assertIn("TRUNCATE TABLE {db}.exceptions", d["rules_sql"])
        r = client.get("/api/architecture.svg")
        self.assertEqual((r.headers["content-type"], r.headers["cache-control"]), ("image/svg+xml", "no-cache"))
        self.assertIn("<svg", r.text[:500])

    def test_evaluate_job(self):
        job = wait(post("/api/evaluate"))
        self.assertEqual((job["state"], job["kind"]), ("done", "evaluate"))
        r = job["result"]
        self.assertEqual((r["docs"], r["field_accuracy"], r["misses"], r["review"]), (len(DOCS), 1.0, [], ["B005"]))
        self.assertEqual(r["planted_total"], len({d for d, _ in planted()}))
        self.assertEqual(r["planted_ok"], r["planted_total"])

    def test_brand_css(self):
        r = client.get("/brand.css")
        self.assertEqual(r.headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertTrue(r.text.startswith(":root {"))
        names = set(re.findall(r"--([\w-]+):", r.text))
        self.assertLessEqual({"accent", "rainbow", "tone-review-text", "block-table", "side-bg-2", "side-link"}, names)

    @unittest.skipUnless((STATIC / "index.html").exists(), "static/index.html isn't there yet")
    def test_index_has_the_csp(self):
        r = client.get("/")
        self.assertTrue(r.headers["content-type"].startswith("text/html"))
        self.assertEqual(r.headers["content-security-policy"], CSP)
        self.assertEqual((r.headers["cache-control"], r.headers["x-content-type-options"]), ("no-cache", "nosniff"))

    @unittest.skipUnless((STATIC / "app.css").exists(), "static/app.css isn't there yet")
    def test_every_css_variable_is_defined(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"--([\w-]+)\s*:", client.get("/brand.css").text + css))
        self.assertEqual(set(re.findall(r"var\(\s*--([\w-]+)", css)) - defined, set())
        self.assertEqual(client.get("/static/app.css").headers["cache-control"], "no-cache")


if __name__ == "__main__":
    unittest.main()

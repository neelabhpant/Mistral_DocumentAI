"""Two ways to give Mistral "context" about a contract.

A. Document annotations: context goes to OCR 4 itself (JSON schema + optional prompt) and it returns structured fields.
B. Tool use (Mistral cookbook pattern): a system prompt goes to a chat model, which calls OCR results as a tool.

CLI: .venv/bin/python contract_ai.py [--n 5]
  Runs CUAD contracts with schema-only vs schema+prompt and scores both against CUAD's labels.
"""

import argparse
import difflib
import json
import random
import re
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd
from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, Field

from ocr_extract import CUAD, PDF_DIR, ocr_bytes
from test_mistral_api import get_client

CHAT_MODEL = "mistral-medium-latest"

# ---------- A. Structured extraction with OCR 4 document annotations ----------


class Answer(BaseModel):
    value: str | None = Field(description="The answer, or null if the contract does not address it.")
    quote: str | None = Field(description="Verbatim sentence from the contract that supports the answer, or null.")


class ContractFields(BaseModel):
    document_name: Answer = Field(description="The name or title of the contract.")
    parties: list[str] = Field(description="Names of the parties who signed the contract.")
    agreement_date: Answer = Field(description="The date the contract was signed or dated.")
    effective_date: Answer = Field(description="The date the contract becomes effective, if different from the agreement date.")
    expiration_date: Answer = Field(description="The date the contract's initial term expires.")
    renewal_term: Answer = Field(description="The renewal term after the initial term expires, e.g. automatic renewals.")
    notice_period_to_terminate_renewal: Answer = Field(
        description="Notice period required to terminate renewal (or avoid automatic renewal)."
    )
    governing_law: Answer = Field(description="Which state or country's law governs the interpretation of the contract.")
    exclusivity: Answer = Field(description="Is there an exclusive dealing commitment or exclusivity granted? Yes or No.")
    non_compete: Answer = Field(description="Is a party restricted from competing with the counterparty? Yes or No.")
    anti_assignment: Answer = Field(description="Is consent or notice required to assign the contract? Yes or No.")
    cap_on_liability: Answer = Field(description="Is a party's liability capped (e.g. a maximum amount)? Yes or No.")
    termination_for_convenience: Answer = Field(
        description="Can a party terminate without cause (for convenience)? Yes or No."
    )
    audit_rights: Answer = Field(description="Does a party have the right to audit the other's books or records? Yes or No.")


DEFAULT_PROMPT = """You are a contract analyst extracting key terms for a contract review database.
Rules:
- Answer only from the document. If a term is not addressed, set value to null (do not guess).
- Dates: format as M/D/YY (e.g. 3/29/18). Read the date as written in the contract's own locale (e.g. 29/3/18 in an Australian contract is March 29, 2018).
- governing_law: the full jurisdiction name only, no abbreviations (e.g. "New York", "Ohio", "People's Republic of China").
- renewal_term: short form such as "successive 1 year", "perpetual", or null if there is no renewal.
- notice_period_to_terminate_renewal: duration only, e.g. "30 days", "3 months".
- Yes/No questions: answer exactly "Yes" or "No".
- quote: copy the supporting text verbatim, at most two sentences."""

ANNOTATION_FORMAT = response_format_from_pydantic_model(ContractFields)

# CUAD label column for each field and how to compare it.
FIELD_SPECS = {
    "document_name": ("Document Name-Answer", "text"),
    "parties": ("Parties-Answer", "parties"),
    "agreement_date": ("Agreement Date-Answer", "date"),
    "effective_date": ("Effective Date-Answer", "date"),
    "expiration_date": ("Expiration Date-Answer", "date"),
    "renewal_term": ("Renewal Term-Answer", "text"),
    "notice_period_to_terminate_renewal": ("Notice Period To Terminate Renewal- Answer", "text"),
    "governing_law": ("Governing Law-Answer", "text"),
    "exclusivity": ("Exclusivity-Answer", "yesno"),
    "non_compete": ("Non-Compete-Answer", "yesno"),
    "anti_assignment": ("Anti-Assignment-Answer", "yesno"),
    "cap_on_liability": ("Cap On Liability-Answer", "yesno"),
    "termination_for_convenience": ("Termination For Convenience-Answer", "yesno"),
    "audit_rights": ("Audit Rights-Answer", "yesno"),
}
CORRECT = {"match", "both empty"}


def annotate(client, data: bytes, prompt: str | None = DEFAULT_PROMPT, **ocr_kwargs):
    """One OCR 4 call that returns both the page markdown and response.document_annotation (JSON string)."""
    return ocr_bytes(client, data, annotation_format=ANNOTATION_FORMAT, annotation_prompt=prompt, **ocr_kwargs)


def parse_fields(document_annotation: str | None) -> dict:
    return json.loads(document_annotation) if document_annotation else {}


@lru_cache(maxsize=1)
def _labels() -> pd.DataFrame:
    df = pd.read_csv(CUAD / "master_clauses.csv")
    df.index = [Path(f).stem for f in df["Filename"]]
    return df


def cuad_labels(filename: str) -> dict | None:
    df = _labels()
    stem = Path(filename).stem
    if stem not in df.index:
        return None
    row = df.loc[stem]
    return {field: (None if pd.isna(row[col]) else str(row[col])) for field, (col, _) in FIELD_SPECS.items()}


def _norm(s: str) -> str:
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|pty|plc|lp|limited)\b\.?", " ", s.lower())
    return " ".join(re.sub(r"[^\w ]", " ", s).split())


def _similar(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6)


def _date(s: str):
    return pd.to_datetime(s, errors="coerce", format="mixed")


def _redacted_date_match(predicted: str, label: str) -> bool | None:
    """CUAD redacts unknown date parts as [] (e.g. "[]/[]/2015"): compare only the parts that are given."""
    parts, p = label.strip().split("/"), _date(predicted)
    if len(parts) != 3 or pd.isna(p):
        return None
    m, d, y = parts
    return (m == "[]" or int(m) == p.month) and (d == "[]" or int(d) == p.day) and (y == "[]" or int(y) % 100 == p.year % 100)


def compare(kind: str, predicted, label: str | None) -> str:
    """match / partial / mismatch / missed (label exists, nothing extracted) / extra (extracted, no label) / both empty."""
    if kind == "parties":
        pred = [p for p in (predicted or []) if p]
        if not label:
            return "extra" if pred else "both empty"
        if not pred:
            return "missed"
        gold = [g for g in label.split(";") if g.strip()]
        hits = sum(any(_similar(g, p) for p in pred) for g in gold)
        return "match" if hits == len(gold) else "partial" if hits else "mismatch"

    if not label:
        return "extra" if predicted else "both empty"
    if not predicted:
        return "missed"
    if kind == "yesno":
        return "match" if predicted.strip().lower().startswith(label.strip().lower()) else "mismatch"
    if kind == "date":
        if "[]" in label and (ok := _redacted_date_match(predicted, label)) is not None:
            return "match" if ok else "mismatch"
        p, g = _date(predicted), _date(label)
        if not pd.isna(p) and not pd.isna(g):
            return "match" if p.date() == g.date() else "mismatch"
    return "match" if _similar(predicted, label) else "mismatch"


def score(fields: dict, labels: dict) -> list[dict]:
    rows = []
    for field, (_, kind) in FIELD_SPECS.items():
        raw = fields.get(field)
        value = raw if kind == "parties" else (raw or {}).get("value")
        quote = None if kind == "parties" else (raw or {}).get("quote")
        rows.append(
            {
                "field": field,
                "value": "; ".join(value) if isinstance(value, list) else value,
                "quote": quote,
                "label": labels.get(field),
                "result": compare(kind, value, labels.get(field)),
            }
        )
    return rows


def accuracy(rows: list[dict]) -> float:
    return sum(r["result"] in CORRECT or (r["result"] == "partial") * 0.5 for r in rows) / len(rows)


# ---------- B. Chat with documents: system prompt + tool (cookbook pattern) ----------

SYSTEM_PROMPT = """You are a contract review assistant. You answer questions about the contracts the user has loaded.

# READ DOCUMENT INSTRUCTIONS
You can read a contract with the `read_document` tool. It returns the contract's full text, extracted by Mistral OCR 4, as markdown with a "### Page N" heading per page. Call it before answering any question about a contract's content; read every contract the question involves.

# ANSWERING RULES
- Answer only from the contract text. If the contract does not address the question, say "Not stated in the contract."
- Cite the page number(s) your answer comes from, e.g. (p. 3).
- Quote short key phrases verbatim when precision matters (dates, amounts, notice periods).
- Be concise: a direct answer first, then supporting detail.

Loaded contracts: {documents}"""


def tool_spec(doc_names: list[str]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_document",
                "description": "Read the OCR-extracted text of one loaded contract.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": doc_names, "description": "The contract's file name."}
                    },
                    "required": ["name"],
                },
            },
        }
    ]


def ask(client, messages: list[dict], docs: dict[str, str], model: str = CHAT_MODEL, max_steps: int = 6):
    """Run the tool loop until the model answers. `docs` maps name -> OCR markdown (with ### Page headings).

    Mutates `messages` (system/user/assistant/tool turns) and returns (answer, tool_calls made).
    """
    tools, calls = tool_spec(list(docs)), []
    for _ in range(max_steps):
        resp = client.chat.complete(model=model, messages=messages, tools=tools, temperature=0)
        msg = resp.choices[0].message
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})
        if not msg.tool_calls:
            return msg.content, calls
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
            name = args.get("name")
            result = docs.get(name, f"Error: no loaded document named {name!r}. Available: {list(docs)}")
            calls.append({"tool": tc.function.name, "name": name, "chars": len(result)})
            messages.append({"role": "tool", "name": tc.function.name, "content": result, "tool_call_id": tc.id})
    return "Stopped: too many tool calls without an answer.", calls


def doc_for_chat(resp, page_markdown) -> str:
    return "\n\n".join(f"### Page {p.index + 1}\n{page_markdown(p)}" for p in resp.pages)


# ---------- CLI: does the prompt help? ----------


def sample_contracts(n: int, seed: int = 7) -> list[Path]:
    labelled = set(_labels().index)
    pdfs = sorted(
        p for p in PDF_DIR.rglob("*") if p.suffix.lower() == ".pdf" and p.stem in labelled and p.stat().st_size < 400_000
    )
    return random.Random(seed).sample(pdfs, n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="number of CUAD contracts to sample")
    ap.add_argument("--seed", type=int, default=7, help="sampling seed (change it to test on unseen contracts)")
    args = ap.parse_args()
    client = get_client()

    results = {"schema only": [], "schema + prompt": []}
    for pdf in sample_contracts(args.n, args.seed):
        print(f"\n=== {pdf.name}")
        data, labels = pdf.read_bytes(), cuad_labels(pdf.name)
        for variant, prompt in (("schema only", None), ("schema + prompt", DEFAULT_PROMPT)):
            t = time.perf_counter()
            resp = annotate(client, data, prompt)
            rows = score(parse_fields(resp.document_annotation), labels)
            results[variant] += [dict(r, doc=pdf.stem[:30]) for r in rows]
            print(f"  {variant:16s} accuracy {accuracy(rows):.0%}  ({time.perf_counter() - t:.1f}s, {len(resp.pages)} pages)")

    print("\n=== Per-field results (✓ = match or correctly empty)")
    df = pd.DataFrame(
        {v: pd.DataFrame(rows).assign(ok=lambda d: d.result.isin(CORRECT)).groupby("field", sort=False).ok.mean() for v, rows in results.items()}
    )
    print(df.map(lambda x: f"{x:.0%}").to_string())
    for v, rows in results.items():
        print(f"overall {v:16s} {accuracy(rows):.0%}")

    print("\n=== Differences between variants")
    a, b = results["schema only"], results["schema + prompt"]
    for ra, rb in zip(a, b):
        if ra["result"] != rb["result"]:
            print(f"  {ra['doc']:30s} {ra['field']:34s} label={ra['label']!r}")
            print(f"      schema only:     {ra['value']!r} -> {ra['result']}")
            print(f"      schema + prompt: {rb['value']!r} -> {rb['result']}")


if __name__ == "__main__":
    main()

"""Ask questions about invoices: a Mistral chat model with a system prompt and a read_document tool that
returns OCR 4's text (the tool-usage pattern from Mistral's OCR cookbook)."""

import json

CHAT_MODEL = "mistral-medium-latest"

SYSTEM_PROMPT = """You are an accounts payable assistant. You answer questions about the supplier invoices the user has loaded.

# READ DOCUMENT INSTRUCTIONS
You can read an invoice with the `read_document` tool. It returns the invoice's full text, extracted by Mistral OCR 4, as markdown with a "### Page N" heading per page. Call it before answering any question about an invoice's content; read every invoice the question involves.

# ANSWERING RULES
- Answer only from the invoice text. If the invoice does not say, answer "Not stated on the invoice."
- Cite the page number(s) your answer comes from, e.g. (p. 1).
- Quote amounts, dates, account numbers and invoice numbers exactly as printed.
- Be concise: a direct answer first, then supporting detail.

Loaded invoices: {documents}"""

SUGGESTIONS = [
    "Which bank account does this invoice ask us to pay, and does it mention a change?",
    "List the line items with quantity and unit price.",
    "When is payment due, and is there an early-payment discount?",
]


def tool_spec(doc_names: list[str]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read the OCR-extracted text of one loaded invoice.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": doc_names, "description": "The invoice's name."}},
                "required": ["name"],
            },
        },
    }]


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

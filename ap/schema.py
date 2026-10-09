"""What OCR 4 extracts from an invoice (document annotation schema + prompt)."""

from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, Field


class LineItem(BaseModel):
    sku: str | None = Field(description="Item / product / catalog code exactly as printed, or null.")
    description: str = Field(description="Line item description.")
    quantity: float = Field(description="Billed quantity.")
    unit_price: float = Field(description="Unit price (rate) as a plain number.")
    amount: float = Field(description="Line total as a plain number.")


class InvoiceFields(BaseModel):
    vendor_name: str = Field(description="Legal name of the company issuing the invoice (the seller, not the bill-to customer).")
    vendor_address: str | None = Field(description="Seller's address on one line.")
    vendor_tax_id: str | None = Field(description="Seller's tax ID / EIN / Federal Tax ID.")
    invoice_number: str = Field(description="Invoice number (may be labelled Invoice #, Invoice No., Bill No.).")
    invoice_date: str = Field(description="Invoice issue date, YYYY-MM-DD.")
    due_date: str | None = Field(description="Payment due date, YYYY-MM-DD.")
    po_number: str | None = Field(description="Buyer's purchase order number (may be labelled PO #, Purchase Order, Your ref, Customer PO).")
    payment_terms: str | None = Field(description="Payment terms exactly as printed, e.g. 'Net 30' or '2/10 Net 30'.")
    currency: str | None = Field(description="ISO currency code, e.g. USD.")
    bank_name: str | None = Field(description="Bank the seller asks to be paid into.")
    routing_number: str | None = Field(description="Bank routing / ABA number, digits only.")
    bank_account: str | None = Field(description="Bank account number to remit to, digits only.")
    bank_change_notice: str | None = Field(
        description="Verbatim text of any notice that the seller's banking or remittance details have changed, else null."
    )
    line_items: list[LineItem] = Field(description="Every billed line, in order, across all pages.")
    subtotal: float | None = Field(description="Subtotal before tax.")
    tax: float | None = Field(description="Sales tax amount, 0 if none.")
    total: float = Field(description="Total amount due.")


INVOICE_PROMPT = """You are an accounts payable clerk keying supplier invoices into the ERP.
Rules:
- Answer only from the document; use null when a field is not printed. Never invent values.
- The vendor is the seller who issued the invoice, never the bill-to customer.
- Dates: convert to YYYY-MM-DD.
- Amounts and quantities: plain numbers without currency symbols or thousands separators.
- routing_number and bank_account: digits only, exactly as printed (do not drop leading zeros).
- line_items: include every billed line across all pages; ignore sub-rows that only carry lot or shipping notes.
- Copy invoice_number, po_number and payment_terms exactly as printed."""

ANNOTATION_FORMAT = response_format_from_pydantic_model(InvoiceFields)
HEADER_FIELDS = [f for f in InvoiceFields.model_fields if f != "line_items"]
LINE_FIELDS = list(LineItem.model_fields)
MONEY_FIELDS = {"subtotal", "tax", "total", "unit_price", "amount"}
DATE_FIELDS = {"invoice_date", "due_date"}

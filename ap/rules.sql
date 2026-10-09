-- Action rules: extracted invoice fields (from Mistral OCR 4) joined against governed enterprise data.
-- {db}, {as_of} and {min_confidence} are filled in by ap/pipeline.py. Statements are separated by ";".

-- Extractions pivoted to one row per invoice / per invoice line.
CREATE VIEW IF NOT EXISTS {db}.invoice_header AS
SELECT doc_id,
  MAX(CASE WHEN field_name = 'vendor_name' THEN field_value END) AS vendor_name,
  MAX(CASE WHEN field_name = 'vendor_tax_id' THEN field_value END) AS vendor_tax_id,
  MAX(CASE WHEN field_name = 'invoice_number' THEN field_value END) AS invoice_number,
  CAST(MAX(CASE WHEN field_name = 'invoice_date' THEN field_value END) AS DATE) AS invoice_date,
  CAST(MAX(CASE WHEN field_name = 'due_date' THEN field_value END) AS DATE) AS due_date,
  MAX(CASE WHEN field_name = 'po_number' THEN field_value END) AS po_number,
  MAX(CASE WHEN field_name = 'payment_terms' THEN field_value END) AS payment_terms,
  MAX(CASE WHEN field_name = 'bank_name' THEN field_value END) AS bank_name,
  MAX(CASE WHEN field_name = 'bank_account' THEN field_value END) AS bank_account,
  MAX(CASE WHEN field_name = 'bank_change_notice' THEN field_value END) AS bank_change_notice,
  CAST(MAX(CASE WHEN field_name = 'total' THEN field_value END) AS DECIMAL(12,2)) AS total
FROM {db}.extractions WHERE line_no IS NULL GROUP BY doc_id;

CREATE VIEW IF NOT EXISTS {db}.invoice_lines AS
SELECT doc_id, line_no,
  MAX(CASE WHEN field_name = 'sku' THEN field_value END) AS sku,
  MAX(CASE WHEN field_name = 'description' THEN field_value END) AS description,
  CAST(MAX(CASE WHEN field_name = 'quantity' THEN field_value END) AS DECIMAL(12,2)) AS quantity,
  CAST(MAX(CASE WHEN field_name = 'unit_price' THEN field_value END) AS DECIMAL(12,2)) AS unit_price,
  CAST(MAX(CASE WHEN field_name = 'amount' THEN field_value END) AS DECIMAL(12,2)) AS amount
FROM {db}.extractions WHERE line_no IS NOT NULL GROUP BY doc_id, line_no;

-- Invoice -> vendor master: tax ID first, then exact name.
CREATE VIEW IF NOT EXISTS {db}.invoice_vendor AS
SELECT h.*, COALESCE(vt.vendor_id, vn.vendor_id) AS vendor_id
FROM {db}.invoice_header h
LEFT JOIN {db}.vendors vt ON regexp_replace(vt.tax_id, '[^0-9]', '') = regexp_replace(h.vendor_tax_id, '[^0-9]', '')
LEFT JOIN {db}.vendors vn ON lower(trim(vn.name)) = lower(trim(h.vendor_name));

TRUNCATE TABLE {db}.exceptions;

-- Remit-to account differs from the vendor master: classic business email compromise.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'BANK_MISMATCH',
  concat('Invoice asks for payment to ', coalesce(h.bank_name, '?'), ' acct ', h.bank_account,
         '; vendor master has ', v.bank_name, ' acct ', v.bank_account,
         if(h.bank_change_notice IS NOT NULL, '. Invoice includes a "bank details changed" notice.', '')),
  h.total, 1, 'Hold payment. Verify the change by calling the vendor on the number in the vendor master.',
  'bank_account', now()
FROM {db}.invoice_vendor h JOIN {db}.vendors v ON v.vendor_id = h.vendor_id
WHERE regexp_replace(h.bank_account, '[^0-9]', '') != regexp_replace(v.bank_account, '[^0-9]', '');

-- Already paid.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'DUPLICATE',
  concat('Invoice ', h.invoice_number, ' was already paid: ', p.payment_id, ' on ', CAST(p.paid_date AS STRING),
         ' for ', CAST(p.amount AS STRING)),
  h.total, 1, 'Block payment and notify the vendor that this invoice was paid.', 'invoice_number', now()
FROM {db}.invoice_vendor h
JOIN {db}.payments p ON p.vendor_id = h.vendor_id AND upper(trim(p.invoice_number)) = upper(trim(h.invoice_number));

-- No vendor match: nothing else can be checked.
INSERT INTO {db}.exceptions
SELECT doc_id, NULL, invoice_number, 'UNKNOWN_VENDOR',
  concat('No vendor master record for "', coalesce(vendor_name, '?'), '" (tax ID ', coalesce(vendor_tax_id, '?'), ')'),
  total, 2, 'Route to vendor management: onboard the vendor or reject the invoice.', 'vendor_name', now()
FROM {db}.invoice_vendor WHERE vendor_id IS NULL;

-- PO missing or not on file.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'NO_PO',
  if(h.po_number IS NULL, 'Invoice does not reference a purchase order',
     concat('PO ', h.po_number, ' does not exist for this vendor')),
  h.total, 2, 'Route to the buyer to match a PO or approve as a non-PO invoice.', 'po_number', now()
FROM {db}.invoice_vendor h
LEFT JOIN {db}.purchase_orders p ON p.po_number = upper(trim(h.po_number)) AND p.vendor_id = h.vendor_id
WHERE h.vendor_id IS NOT NULL AND p.po_number IS NULL;

-- Billed price above the PO price, per line.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'PRICE_OVER_PO',
  concat(coalesce(l.sku, l.description), ': billed ', CAST(l.unit_price AS STRING), ' vs PO ', CAST(pl.unit_price AS STRING),
         ' x ', CAST(CAST(l.quantity AS INT) AS STRING)),
  CAST((l.unit_price - pl.unit_price) * l.quantity AS DECIMAL(12,2)), 3,
  'Short-pay to the PO price and send the vendor a price dispute.', concat('line:', CAST(l.line_no AS STRING), ':unit_price'), now()
FROM {db}.invoice_vendor h
JOIN {db}.invoice_lines l ON l.doc_id = h.doc_id
JOIN {db}.po_lines pl ON pl.po_number = upper(trim(h.po_number)) AND pl.sku = l.sku
WHERE l.unit_price > pl.unit_price;

-- Billed quantity above what the warehouse received, per line.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'QTY_OVER_RECEIPT',
  concat(coalesce(l.sku, l.description), ': billed ', CAST(CAST(l.quantity AS INT) AS STRING), ', received ',
         CAST(r.qty_received AS STRING), ' (', r.receipt_id, ')'),
  CAST((l.quantity - r.qty_received) * l.unit_price AS DECIMAL(12,2)), 3,
  'Short-pay to the received quantity; ask the warehouse to confirm.', concat('line:', CAST(l.line_no AS STRING), ':quantity'), now()
FROM {db}.invoice_vendor h
JOIN {db}.invoice_lines l ON l.doc_id = h.doc_id
JOIN {db}.goods_receipts r ON r.po_number = upper(trim(h.po_number)) AND r.sku = l.sku
WHERE l.quantity > r.qty_received;

-- Key fields read with low confidence: a person checks them before anything is paid.
INSERT INTO {db}.exceptions
SELECT e.doc_id, h.vendor_id, h.invoice_number, 'LOW_CONFIDENCE',
  concat(e.field_name, ' read as "', e.field_value, '" with confidence ', CAST(CAST(e.confidence AS DECIMAL(4,2)) AS STRING)),
  NULL, 5, 'Human review: confirm the value against the page before approval.', e.field_name, now()
FROM {db}.extractions e JOIN {db}.invoice_vendor h ON h.doc_id = e.doc_id
WHERE e.line_no IS NULL AND e.confidence < {min_confidence}
  AND e.field_name IN ('invoice_number', 'invoice_date', 'po_number', 'bank_account', 'routing_number', 'total');

-- Clean invoice inside an early-payment discount window: an opportunity, not a risk.
INSERT INTO {db}.exceptions
SELECT h.doc_id, h.vendor_id, h.invoice_number, 'DISCOUNT_WINDOW',
  concat('Terms ', h.payment_terms, ': pay by ', CAST(date_add(h.invoice_date, 10) AS STRING), ' to save 2%'),
  CAST(h.total * 0.02 AS DECIMAL(12,2)), 4, 'Approve and pay now to capture the early-payment discount.', 'payment_terms', now()
FROM {db}.invoice_vendor h
WHERE h.payment_terms LIKE '2/10%' AND DATE '{as_of}' <= date_add(h.invoice_date, 10)
  AND h.doc_id NOT IN (SELECT doc_id FROM {db}.exceptions)

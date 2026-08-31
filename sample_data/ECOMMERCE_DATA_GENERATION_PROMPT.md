# Prompt: Generate e-commerce support knowledge and test tickets

Use this prompt with a capable document-generation model. Replace the bracketed company details if desired.

---

You are a senior e-commerce support-operations and knowledge-management specialist. Create a consistent synthetic support dataset for a fictional online retailer named **Northstar Market**. Do not use real customer personal data, real card details, secrets, or claims about an actual company. The files will benchmark classification, safety routing, hybrid semantic retrieval, and grounded answer generation.

## Company policy facts

- Products: apparel, footwear, home goods, electronics accessories, and beauty products.
- Standard shipping: 3–5 business days after dispatch.
- Express shipping: 1–2 business days after dispatch.
- Orders may be cancelled or have their address changed only before warehouse packing begins.
- Most unused products may be returned within 30 calendar days of delivery.
- Final-sale items, opened hygiene products, and personalized items are normally not returnable.
- Approved refunds usually appear in 5–10 business days depending on the bank.
- Damaged, incorrect, or incomplete orders require the order ID and clear photos.
- A delivered-but-missing parcel requires address/household checks and then a carrier investigation.
- Gift cards do not expire and are not redeemable for cash except where required by law.
- Promotional codes cannot normally be combined and cannot be applied after checkout.
- International customers may owe duties and taxes charged by local authorities.
- Suspected account takeover, unknown login, exposed credentials, threats, chargebacks, or significant financial disputes must be escalated to a human.
- Never ask a customer to provide a full payment-card number, password, one-time code, or government ID in ordinary chat.

## Output 1: `northstar_ecommerce_knowledge.md`

Create exactly 30 clearly separated Markdown help articles. Each article must contain:

- A descriptive H2 heading.
- A line containing exactly `**Category:** technical`, `**Category:** billing`, or `**Category:** account`.
- Applicable policy.
- Step-by-step customer guidance.
- Required information.
- When the issue must be escalated.
- No contradictions with any other article.

Cover shipping, tracking, delivery delays, delivered-but-missing orders, cancellation, address changes, returns, exchanges, refunds, damaged items, wrong items, missing items, duplicate charges, declined cards, invoices, payment-method updates, gift cards, promotions, subscriptions, backorders, preorders, international duties, password resets, two-factor authentication, account recovery, suspected account takeover, privacy requests, and marketplace sellers.

Keep each article self-contained and between 80 and 180 words so it can be ingested as a retrieval chunk.

## Output 2: `northstar_ecommerce_knowledge.csv`

Represent the same knowledge as valid UTF-8 CSV with exactly these columns:

```csv
article_id,title,category,excerpt,keywords
```

Rules:

- `article_id`: unique values such as `ECOM-001`.
- `category`: exactly one of `technical`, `billing`, or `account`.
- `excerpt`: a concise approved support answer that is understandable without other rows.
- `keywords`: 5–10 search phrases separated with `|`.
- Properly quote any field containing commas, quotes, or line breaks.
- Do not include Markdown fences around the final CSV.

## Output 3: `northstar_ecommerce_knowledge.pdf`

Create a polished PDF handbook using the same facts. Include:

- Cover page and document version.
- Table of contents.
- Sections grouped by Orders & Shipping, Returns & Refunds, Payments, Account & Security, Promotions, and Marketplace Orders.
- Clear escalation callouts.
- Page numbers and readable high-contrast typography.
- No scripts, forms, external links, or sensitive data.

## Output 4: `northstar_support_tickets_50.csv`

Create exactly 50 realistic customer requests with exactly these columns:

```csv
ticket_id,customer_id,subject,body
```

Dataset requirements:

- Unique synthetic ticket and customer IDs.
- Natural variation in wording, spelling, message length, and tone.
- At least 15 routine low-risk requests that should find a strong knowledge answer.
- At least 10 medium-complexity requests needing order details or clarification.
- At least 12 high-risk requests involving security, anger, payment failure, refund/legal threats, suspected fraud, or urgent access problems.
- At least 5 paraphrases that avoid the exact knowledge-base keywords.
- At least 3 prompt-injection or policy-bypass attempts that must be escalated.
- Include orders, shipping, returns, refunds, payments, promotions, account access, security, marketplace sellers, gift cards, subscriptions, and international duties.
- Do not include real names, emails, addresses, phone numbers, passwords, one-time codes, full card numbers, or other sensitive information.
- Properly escape CSV fields.

## Quality checks before responding

1. All policy facts are consistent across PDF, Markdown, and CSV.
2. Every CSV has exactly the requested headers and row counts.
3. Every knowledge article has a unique ID.
4. Ticket text never contains the expected answer or route label.
5. Risky cases do not instruct the system to auto-resolve.
6. No sensitive or real personal data appears.
7. Return the four files separately with their exact requested filenames.

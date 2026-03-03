# PRD: Fathom-to-Google-Docs Call Notes Automation (V1)

## 1. Objective
Automatically append Fathom meeting output (starting with Fathom's summary) to the correct Google Notes Doc after each call.

This system should:
- Receive a Fathom webhook.
- Match the webhook to the correct company/person notes doc in one known Google Drive folder.
- Append a new page section at the bottom of that doc with meeting summary content.

## 2. Background
- Existing Apps Script creates/populates notes docs tied to calendar calls.
- During calls, notes are taken manually in those docs.
- After call ends, Fathom sends webhook payload with meeting content (summary, transcript, action items enabled).
- We now need a server-side pipeline to route that payload to the right doc and append structured output.

## 3. Scope
### In scope (V1)
- Fathom webhook ingestion on Vercel (Python).
- Signature verification.
- Pull docs from one configured Google Drive folder.
- LLM-based doc matching using doc text + webhook context.
- Append to matched doc with a page break and standardized section.
- Use Fathom-provided summary as primary inserted summary text.
- Skip when no match is found.

### Out of scope (V1)
- Human review queue for low-confidence matches.
- Duplicate-event prevention/idempotency store.
- Alerting/notifications.
- Custom transcript re-summarization prompt pipeline.
- Multi-folder routing logic.

## 4. Success Criteria
- For typical meetings, system appends summary to the correct notes doc within a few minutes of webhook arrival.
- Unmatched meetings are skipped (no incorrect write) and logged.
- Invalid webhook signatures are rejected.

## 5. Functional Requirements
1. Webhook Endpoint
- Endpoint: `POST /api/fathom_webhook`
- Verify Fathom signature headers:
  - `webhook-id`
  - `webhook-timestamp`
  - `webhook-signature`
- Reject invalid signatures with `401`.

2. Payload Parsing
- Parse fields used for matching and writing:
  - `meeting_title`
  - `calendar_invitees` (name/email/domain)
  - `default_summary.markdown_formatted`
  - `action_items` (optional for V1 output)
  - `transcript` (available for future matching fallback)

3. Candidate Doc Collection
- Read all Google Docs in configured folder (`GOOGLE_DRIVE_TARGET_FOLDER_ID`).
- For each doc, collect:
  - File name
  - Plain text content

4. Matching
- Use LLM to pick the best document candidate using:
  - Primary identifiers: company name + person name.
  - Secondary context: meeting title/invitees/domains/doc body.
- Behavior:
  - If a reasonable single best match exists: write to that doc.
  - If no match: skip write and log.

5. Append Format
- Insert page break at end of matched document.
- Append section with:
  - Header line: meeting title + timestamp
  - "Fathom Summary"
  - Raw Fathom markdown summary content (or plain text if missing)
  - Optional "Action Items" block if present

## 6. Non-Functional Requirements
- Latency target: complete processing in near-real-time (minutes acceptable).
- Reliability: no crash on malformed payload; safely skip and log.
- Security: webhook signature verification required in production.
- Observability: structured logs for match result and write status.

## 7. Configuration
Environment variables:
- `FATHOM_WEBHOOK_SECRET` (required)
- `GOOGLE_DRIVE_TARGET_FOLDER_ID` (required for matching)
- `GOOGLE_SERVICE_ACCOUNT_JSON` (required for Drive/Docs API access)
- `LOG_LEVEL` (optional)
- `OPENAI_API_KEY` (required for LLM matching)
- `OPENAI_MODEL` (optional, default lightweight model)

## 8. Technical Design (Simple + Robust)
1. Ingest
- Vercel Python function receives webhook and verifies signature.

2. Normalize
- Build a compact `meeting_context` object:
  - title
  - invitee names/emails/domains
  - summary text
  - recording/share URLs

3. Fetch Candidates
- Drive API list docs in target folder.
- Docs API fetch text content for each candidate.

4. LLM Match
- Prompt includes:
  - `meeting_context`
  - candidate docs (id, title, key text excerpt)
- Expected model output JSON:
  - `matched_doc_id` or `null`
  - `reason`

5. Append
- If matched: Docs API batchUpdate:
  - insert page break at doc end
  - insert formatted section content
- If unmatched: return success response but log "skipped_unmatched".

## 9. Logging Requirements
- Log event metadata:
  - webhook id
  - meeting title
  - selected doc id/title or unmatched
  - write status (success/failure)
- Do not log full transcript unless debugging mode is explicitly enabled.

## 10. Failure Handling
- Invalid signature: `401`
- Missing required fields: log + skip
- Drive/Docs API failure: log error + non-200 only if retry desired
- LLM match failure/timeouts: log + skip write

## 11. Implementation Plan
Phase 1: Payload Contract Lock
- Confirm fields from Fathom docs.
- Capture one real payload from production webhook for validation.

Phase 2: Google API Wiring
- Service account auth.
- List docs in folder and fetch text.

Phase 3: LLM Matching
- Add deterministic prompt + parse JSON output.
- Route to matched doc or skip unmatched.

Phase 4: Append Summary
- Page break + section insert using Docs API.

Phase 5: Hardening
- Better logs and basic retry-safe behavior.

## 12. Acceptance Criteria
- Given valid Fathom webhook and a clear matching doc, summary is appended to correct doc with page break.
- Given no clear match, nothing is written and event is logged as unmatched.
- Endpoint rejects invalid signatures.

## 13. Open Items
- Confirm exact real-world Fathom payload shape from at least one live event.
- Decide final section template details (date label, action-item formatting).
- Decide whether to include action items in V1 output body or summary-only.

## 14. Recommended Payload Discovery Approach
Best path:
1. Use Fathom docs schema as baseline:
   - https://developers.fathom.ai/webhooks
   - https://developers.fathom.ai/api-reference/webhook-payloads/new-meeting-content-ready
2. Trigger one real meeting webhook.
3. Capture one sanitized payload sample from Vercel logs for field validation.
4. Lock parser to observed + documented fields.

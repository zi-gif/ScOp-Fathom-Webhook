# Fathom Webhook Starter (Vercel + Python)

## Homepage
- The root URL (`/`) serves a static page that displays this `README.md`.
- Webhook endpoint remains at `/api/fathom_webhook`.

## Required configuration
Set these env vars in Vercel:
- `FATHOM_WEBHOOK_SECRET`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_DRIVE_TARGET_FOLDER_ID`
- `OPENAI_API_KEY`
- Optional: `OPENAI_MODEL` (default `gpt-4o-mini`)

## Deploy
1. `npx vercel env add FATHOM_WEBHOOK_SECRET production`
2. `npx vercel env add GOOGLE_SERVICE_ACCOUNT_JSON production`
3. `npx vercel env add GOOGLE_DRIVE_TARGET_FOLDER_ID production`
4. `npx vercel env add OPENAI_API_KEY production`
5. `npx vercel --prod --yes`

## Replay a signed fixture webhook
1. Export secret locally:
   `export FATHOM_WEBHOOK_SECRET='whsec_...'`
2. Send fixture:
   `python3 tests/send_signed_fixture.py --url 'https://<your-domain>/api/fathom_webhook' --secret \"$FATHOM_WEBHOOK_SECRET\"`

## Behavior
- Verifies Fathom webhook signatures (`webhook-id`, `webhook-timestamp`, `webhook-signature`).
- Reads all Google Docs in target folder.
- Uses OpenAI to match meeting payload to the best doc.
- Appends a page-break section with summary and action items.
- Skips and logs if unmatched.

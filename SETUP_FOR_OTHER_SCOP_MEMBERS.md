# Setup: For Other ScOp Members

This is the correct setup for ScOp teammates:

- shared GitHub repo
- separate Vercel project per person
- separate Fathom webhook per person
- separate Google Drive folder per person
- separate Google service account / Google Cloud project per person

Do not share one Vercel project if different people are writing to different Drive folders. The app uses one runtime config per deployment, so each person needs their own deployment.

## What Each Person Needs To Create

Each ScOp member should have their own:

- Vercel project
- Fathom webhook
- Google Cloud project
- Google service account JSON key
- Google Drive target folder ID
- OpenAI API key

They can all use the same GitHub repo and same code.

## Step 1: Get Access To The Repo

Ask to be added to:

- GitHub repo: `ScOp-Fathom-Webhook`

Clone it locally:

```bash
git clone git@github.com:zi-gif/ScOp-Fathom-Webhook.git
cd ScOp-Fathom-Webhook
```

## Step 2: Create Your Own Google Drive Folder

Create one Google Drive folder that will contain your notes docs.

This folder is the one the app will scan for matching notes documents.

You will need the folder ID from the URL:

```text
https://drive.google.com/drive/folders/FOLDER_ID_HERE
```

## Step 3: Create Your Own Google Cloud Project

In Google Cloud:

1. Create a new project
2. Enable:
   - Google Drive API
   - Google Docs API
3. Create a service account
4. Generate a JSON key for that service account

Keep the JSON key private.

## Step 4: Share Your Notes Folder With Your Service Account

Take the `client_email` from the service account JSON and share your Google Drive notes folder with that email as:

- `Editor`

Without this step, the app cannot read or write your docs.

## Step 5: Create Your Own Vercel Project

In the project folder, run:

```bash
npx vercel
```

This should create a new Vercel project for you personally. Do not reuse someone else's project if you are using your own Drive folder.

## Step 6: Add Your Own Environment Variables In Vercel

Set these in your Vercel project:

```bash
npx vercel env add FATHOM_WEBHOOK_SECRET production
npx vercel env add OPENAI_API_KEY production
npx vercel env add GOOGLE_DRIVE_TARGET_FOLDER_ID production
npx vercel env add GOOGLE_SERVICE_ACCOUNT_JSON production
```

Values:

- `FATHOM_WEBHOOK_SECRET`
  - your own Fathom webhook secret
- `OPENAI_API_KEY`
  - your own OpenAI API key
- `GOOGLE_DRIVE_TARGET_FOLDER_ID`
  - your own Google Drive notes folder ID
- `GOOGLE_SERVICE_ACCOUNT_JSON`
  - your own full service account JSON

Optional:

```bash
npx vercel env add OPENAI_MODEL production
npx vercel env add OPENAI_CLEANUP_MODEL production
```

## Step 7: Deploy Your Project

Deploy your own copy:

```bash
npx vercel --prod --yes
```

Your webhook URL will be:

```text
https://YOUR-PROJECT.vercel.app/api/fathom_webhook
```

## Step 8: Create Your Own Fathom Webhook

In Fathom:

1. Create a webhook
2. Set the destination URL to your own Vercel deployment:

```text
https://YOUR-PROJECT.vercel.app/api/fathom_webhook
```

3. Copy the webhook secret into your Vercel env vars

Do not point multiple people at the same deployment if each person has their own Drive folder.

## Step 9: Test The Integration

You can test either with:

- a real Fathom meeting, or
- the included fixture replay script

Local replay:

```bash
python3 tests/send_signed_fixture.py \
  --url "https://YOUR-PROJECT.vercel.app/api/fathom_webhook" \
  --secret "YOUR_FATHOM_WEBHOOK_SECRET"
```

## Step 10: Verify It Works

Check:

1. Vercel logs show the webhook was processed
2. The correct Google Doc was matched
3. Only one `Intro Call:` section is appended
4. Empty fields in the notes doc are auto-filled once when supported by the meeting data

## What Not To Share

Do not share:

- service account JSON keys
- Google Drive folder IDs if docs should stay private
- OpenAI API keys
- Fathom webhook secrets across isolated deployments

## If You Need To Inspect Infra

If someone needs to inspect, but not own, infrastructure:

- GitHub repo access
- Vercel project access
- Google Cloud `Viewer`

But if they are using their own Drive folder, they should still create their own Google Cloud project and Vercel project.

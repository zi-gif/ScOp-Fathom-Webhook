# Setup: For Other ScOp Members

This project is set up as one shared internal integration for ScOp.

## Shared Infrastructure
- Shared GitHub repo
- Shared Vercel project
- Shared Fathom webhook secret
- Shared Google service account
- Shared Google Drive target folder

## What This Means
- This is one shared integration, not isolated per-person infrastructure.
- If someone changes shared environment variables or rotates secrets, it affects everyone.
- Signed test webhook calls are valid against the shared endpoint.

## Access To Grant
1. Add teammates to the GitHub repo.
2. Add teammates to the Vercel project/team.
3. Share the Google Drive notes folder or docs with teammates if they need to inspect outputs.
4. Add teammates to the Google Cloud project if they need to inspect service-account or API configuration.

## Recommended Permissions
- GitHub: write access
- Vercel: project/team access
- Google Drive folder: Editor if they need to inspect or manage docs
- Google Cloud: Viewer unless they need to manage credentials or APIs

## Verification Workflow
1. Open the Vercel project logs.
2. Check the target Google Doc.
3. Confirm only one `Intro Call:` section exists.
4. Confirm empty notes fields are autofilled only once per document.

## Operational Notes
- Do not commit secrets into Git.
- If the service account key is exposed, rotate it immediately.
- This app assumes one shared notes folder configuration per deployment.

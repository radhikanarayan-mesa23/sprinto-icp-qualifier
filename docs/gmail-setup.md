# Gmail drafts: one-time setup

The agent can put its drafts into Gmail's **Drafts** folder. It can never send: the code has no send call (a test enforces
this), and the Growth Lead reviews and sends each one herself. This runs on your own computer only. It is deliberately not part
of the public web viewer, because that page has no sign-in and anyone could otherwise write into your mailbox.

You do these steps once. They need your Google account, so I cannot do them for you.

1. **Pick the account.** The Gmail address that should hold the drafts. A personal Gmail is simplest. A school or company
   Workspace account may have an admin who blocks third-party apps.
2. **Create a project.** Go to <https://console.cloud.google.com/>, create a project (for example "Sprinto drafts"). It is free.
3. **Enable the Gmail API.** APIs & Services, then Library, search "Gmail API", Enable.
4. **Consent screen.** APIs & Services, then OAuth consent screen (or "Google Auth Platform"). Choose **External**, give it a name and
   your email as the support contact. Under Scopes, add `https://www.googleapis.com/auth/gmail.compose`. Under Test users, add the
   Gmail address from step 1.
5. **Create the client.** Credentials (or Clients), Create OAuth client ID, application type **Desktop app**. Download the JSON.
6. **Save it outside the repo:**
   ```bash
   mkdir -p ~/.config/sprinto && mv ~/Downloads/client_secret_*.json ~/.config/sprinto/gmail_client_secret.json
   chmod 600 ~/.config/sprinto/gmail_client_secret.json
   ```
7. **Sign in once:**
   ```bash
   .venv/bin/python scripts/gmail_drafts.py --auth
   ```
   Your browser opens. Google warns "Google hasn't verified this app". That is expected for your own app: choose Advanced, then
   continue. The sign-in is saved to `~/.config/sprinto/gmail_token.json` (readable only by you).

## Using it

```bash
# See what would be created. Touches nothing.
.venv/bin/python scripts/gmail_drafts.py --list --db data/real_run.db

# Create the drafts. Asks before it does anything.
.venv/bin/python scripts/gmail_drafts.py --create --db data/real_run.db --sender-name "Your Name"
```

Only records that are **go or go-flagged**, **passed the provenance gate** and have a **contact email** are eligible, and each is created
once; running it again skips ones already in Gmail. Open Gmail, then Drafts, to review, edit and send.

## Things to know

- **The scope is `gmail.compose`.** It is the narrowest scope Google offers for creating drafts, and it technically also allows
  sending. This tool only calls "create draft".
- **While the app is in Testing, Google expires the sign-in after 7 days.** Run `--auth` again when it says so. Publishing the app
  ("In production") removes the expiry, and the "unverified app" warning stays. That is fine for your own use.
- **Revoke access any time** at <https://myaccount.google.com/permissions>.
- **Real contact emails are personal data.** This reads the database you name with `--db`. Do not point it at a public database.

# Weco Reachout Bot

Slack-triggered drafter for personalized reach-out emails to Weco users.

## How it works

Two triggers:

1. **Slash command** — `/reachout email@domain.com [optional note]`
2. **Emoji reaction** — react `:envelope:` on any message containing an email address (e.g. in `#user-activity`)

For each trigger, the bot:
1. Looks up the user in the **Notion User Activity DB** (page keyed by email — populated by the Weco User Tracking pipeline)
2. Pulls their recent runs from **Supabase** (`user_funnel`, `runs`, `nodes`)
3. If the most recent run errored, peeks at the source code (only to inform whether to offer help — never quoted)
4. Calls Claude (via Weco LiteLLM proxy) to draft a personalized email matching the template
5. DMs you a preview + a **📧 Open in Superhuman** button — clicking it opens a `mailto:` link, which Superhuman handles when set as the default mail client

## Setup

### 1. Slack app

Create a new Slack app at [api.slack.com/apps](https://api.slack.com/apps) named **reachout**.

**Bot Token Scopes**:
- `commands`
- `chat:write`
- `reactions:read`
- `channels:history`
- `groups:history`
- `users:read`
- `users:read.email`

**Slash Command**: `/reachout` → Request URL `https://<render-url>/slack/commands`

**Event Subscriptions**: Request URL `https://<render-url>/slack/events`, subscribe to bot event `reaction_added`.

**Interactivity**: Request URL `https://<render-url>/slack/interactivity` (not strictly required since the button uses native `url`, but Slack apps with buttons benefit from having it set).

Install to workspace → grab Bot User OAuth Token + Signing Secret.

### 2. Notion integration

The User Activity DB (`3232e4b52eba44079d3a8d46705e5cd8`) must be shared with the bot's Notion integration. Use the same integration as the Weco User Tracking pipeline, or create a new "Weco Reachout" integration and add it via **Connections → Add connection** on the DB.

### 3. Supabase

Reuses the existing Weco production Supabase. Same `SUPABASE_URL` + `SUPABASE_KEY` (service role) as Weco User Tracking.

### 4. Default mail client

On Mac: open Superhuman → Settings → Set as default mail app. This is what makes `mailto:` URLs from the Slack button open Superhuman compose with subject + body pre-filled.

### 5. Deploy to Render

```bash
gh repo create weco-reachout-bot --private --source=. --push
```

Then in Render: New Web Service → connect this repo → it auto-detects `render.yaml`. Set env vars per `.env.example`.

After Render gives you a URL, paste it into the three Slack app Request URL fields.

## Env vars

See `.env.example`.

## Local dev

```bash
cp .env.example .env  # fill in
pip install -r requirements.txt
python server.py
```

Use [ngrok](https://ngrok.com/) to expose port 8080 and point Slack at the ngrok URL.

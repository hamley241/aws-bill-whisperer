# Slack Quickstart

Get from `git clone` to your first `/whisper scan` finding in under 10 minutes.

This is the open-source, self-hosted setup. The Slack app and the
webhook both run in **your** infrastructure — no Whisper vendor ever
sees your AWS data, your prompts, or your Slack tokens (per
[CLAUDE.md](../CLAUDE.md) data sovereignty).

## What you'll need

- An AWS account with credentials configured locally (or a deploy-time
  role for SAM).
- A Slack workspace where you have permission to install custom apps.
- Python 3.10+ and `aws` CLI for the Lambda path (or just Python +
  `ngrok` for the local path).

You'll choose one of two deployment paths:

| Path | Best for | Public endpoint? |
|------|----------|------------------|
| **A. Local + Socket Mode** | Laptop dev, first demo | Not needed |
| **B. AWS Lambda (SAM)** | Production / always-on | Yes (API Gateway) |

---

## 1. Clone and install

```bash
git clone https://github.com/hamley241/aws-bill-whisperer.git
cd aws-bill-whisperer
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,openai]'   # openai is optional; bedrock works without it
```

## 2. Create your Slack app from the manifest

1. Open <https://api.slack.com/apps?new_app=1> → **From a manifest** →
   select your workspace.
2. Open [`slack/manifest.yaml`](../slack/manifest.yaml), copy its
   contents into the manifest editor.
3. **Path A (Socket Mode)**: in the manifest, set
   `socket_mode_enabled: true` and remove the `url:` and `request_url:`
   fields (Socket Mode doesn't use a webhook). You can do this after
   import in the app's "Socket Mode" page.
4. **Path B (Lambda)**: leave the `url:` and `request_url:` fields as
   placeholders for now; you'll replace them in step 4.
5. Click **Create**, then **Install to Workspace**.

After install, grab:
- **Bot User OAuth Token** (xoxb-…) → `SLACK_BOT_TOKEN`
- **Signing Secret** from *Basic Information* → `SLACK_SIGNING_SECRET`
- **(Path A only)** App-Level Token from *Socket Mode* with the
  `connections:write` scope (xapp-…) → `SLACK_APP_TOKEN`

## 3. Configure your environment

Export the credentials and your LLM choice:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
export WHISPER_LLM_BACKEND=bedrock          # or openai / anthropic
# export OPENAI_API_KEY=sk-...              # if WHISPER_LLM_BACKEND=openai
# export ANTHROPIC_API_KEY=sk-ant-...       # if WHISPER_LLM_BACKEND=anthropic
export AWS_PROFILE=default                  # or AWS_REGION, etc.
```

Now sanity-check the install:

```bash
whisper-config doctor
```

You should see green ticks for `scan`, `llm:<backend>`, `slack`, and
`prompt-log`. Any red ticks tell you exactly what's missing.

## 4. Run the webhook

### Path A — Local + Socket Mode

```bash
export SLACK_APP_TOKEN=xapp-...
python -m slack.run_local
```

Socket Mode opens a persistent WebSocket from Slack to your laptop —
no public URL or ngrok needed. Slack delivers events through that
tunnel.

### Path B — AWS Lambda via SAM

```bash
sam build
sam deploy --guided \
  --parameter-overrides \
    SlackBotToken=$SLACK_BOT_TOKEN \
    SlackSigningSecret=$SLACK_SIGNING_SECRET \
    LlmBackend=bedrock
```

When the deploy finishes, copy the `WhisperSlackEndpoint` output (it
looks like
`https://abc123.execute-api.us-east-1.amazonaws.com/Prod/slack/events`).
Then, in your Slack app config, replace the three placeholder URLs:

- Slash command URL (`/whisper`)
- Event subscriptions request URL
- Interactivity request URL

…with that endpoint. Save each page. Slack will verify the URL.

## 5. First scan

In any channel where the bot is invited (or any public channel — the
manifest includes `chat:write.public`), type:

```
/whisper scan
```

Within ~90 seconds you should see a threaded message with:
- Total monthly waste and finding count.
- Top 5 findings sorted by `$ impact`.
- A "Open PR" button (stubbed today; PR-native remediation lands in
  a future release).

Reply in the same thread or `@whisper` to ask follow-up questions —
the bot uses the scan you just ran as context.

## What to do if it doesn't work

- `whisper-config doctor` → tells you which capability is missing.
- Slack's app config has an **Event Subscriptions** request-URL
  verification check; if it fails, your `SLACK_SIGNING_SECRET` is
  probably wrong or the URL isn't reachable.
- For Lambda, check the CloudWatch logs for `WhisperSlackFunction`.
- For local Socket Mode, look at stdout from `python -m
  slack.run_local`.

## What the bot can see

By default, the bot scopes are read/write for chat in channels it's
been added to, plus `chat:write.public` for posting in public channels
without an invite. It does **not** read your DMs and it does **not**
read messages from channels it isn't in.

Every LLM prompt and response is appended to
`~/.whisper/prompts.log` (or `/tmp/whisper-prompts.log` on Lambda).
That's the audit trail for "what data did the model see?".

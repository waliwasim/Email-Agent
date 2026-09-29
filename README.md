# AI Daily News Agent

Emails you a daily summary of what's happening in AI — new tools, releases, and
research — pulled from major AI news RSS feeds and summarized by Google Gemini's
free API. Runs automatically on GitHub Actions, so there's no server to pay for.

**Total cost: ₹0/month**, as long as you stay within these free limits:
- Gemini free tier: 1,500 requests/day (you'll use 1/day)
- GitHub Actions: 2,000 free minutes/month on a private repo, unlimited on a public repo (you'll use ~1 min/day)
- Gmail SMTP: free, no sending limit relevant to 1 email/day

---

## Setup (takes about 10 minutes)

### Step 1 — Get a free Gemini API key
1. Go to https://aistudio.google.com/apikey
2. Sign in with any Google account (no credit card needed)
3. Click "Create API key" and copy it

### Step 2 — Create a Gmail "App Password"
Gmail won't let scripts log in with your normal password, so you need a special one:
1. Turn on 2-Step Verification on the Gmail account you'll send FROM:
   https://myaccount.google.com/signinoptions/two-step-verification
2. Then go to https://myaccount.google.com/apppasswords
3. Create a new app password (name it "AI Digest"), copy the 16-character code

### Step 3 — Put this code on GitHub
1. Create a new **private** GitHub repository (e.g. `ai-daily-digest`)
2. Upload all the files in this folder to it (or `git push` them)

### Step 4 — Add your secrets
In your new repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add these four secrets:

| Secret name          | Value                                      |
|-----------------------|---------------------------------------------|
| `GEMINI_API_KEY`      | the key from Step 1                        |
| `GMAIL_ADDRESS`       | the Gmail address you made the app password for |
| `GMAIL_APP_PASSWORD`  | the 16-character code from Step 2          |
| `RECIPIENT_EMAIL`     | the email address that should receive the digest (can be the same Gmail, or any other) |

### Step 5 — Test it
Go to the **Actions** tab → click "Daily AI News Digest" → **Run workflow** → Run.
Check your inbox in about a minute. If it fails, click into the run to read the error log.

### Step 6 — Let it run
That's it. It will now run automatically every day at the time set in
`.github/workflows/daily-ai-summary.yml` (default: 07:00 UTC ≈ 12:30 PM PKT).
To change the time, edit the `cron` line in that file — cron times are always in UTC.

---

## Customizing

- **Change news sources**: edit the `RSS_FEEDS` list in `main.py`. Any site with an
  RSS feed works — just add its feed URL.
- **Change summary style**: edit the prompt text inside `build_prompt()` in `main.py`.
- **Change delivery time**: edit the `cron` schedule in the workflow file.
- **Add more recipients**: change `RECIPIENT_EMAIL` to a comma-separated list.

## Why this stays free
- No servers rented — GitHub Actions provides the compute for free on a schedule.
- No paid AI API — Gemini's free tier has no expiry and no credit card requirement (as of 2026).
- No email service fees — Gmail SMTP is free for personal-volume sending.

If Google ever tightens Gemini's free tier, drop-in alternatives with similarly
generous free tiers include Groq (https://console.groq.com) and OpenRouter's
free models (https://openrouter.ai/models?max_price=0) — swap the API call in
`summarize_with_gemini()` for either.

"""
Daily AI News Summarizer
-------------------------
Fetches recent AI-related articles from a curated list of RSS feeds,
summarizes them into a short digest using Google Gemini's free API tier,
and emails the digest via Gmail SMTP.

All pieces used here are free:
- RSS feeds: public, no API key needed
- Gemini API: free tier (no credit card required as of 2026)
- Gmail SMTP: free with an "app password"
- Runs on GitHub Actions: free scheduled compute

Required environment variables (set as GitHub Secrets, see README.md):
  GEMINI_API_KEY      - from https://aistudio.google.com/apikey
  GMAIL_ADDRESS        - the Gmail account that will SEND the email
  GMAIL_APP_PASSWORD   - 16-char app password for that Gmail account
  RECIPIENT_EMAIL      - where the daily summary should be sent
"""

import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests

# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.artificialintelligence-news.com/feed/",
]

# Only include articles published within this many hours (catches "daily" news)
LOOKBACK_HOURS = 30

GEMINI_MODEL = "gemini-3.8-flash"  # fast + on the free tier
GEMINI_API_KEY = None
GEMINI_URL = None
GMAIL_ADDRESS = None
GMAIL_APP_PASSWORD = None
RECIPIENT_EMAIL = None


def load_config():
    """Read required env vars into module globals. Called inside the
    top-level try/except so missing/misnamed secrets get logged instead
    of crashing before any error handling runs."""
    global GEMINI_API_KEY, GEMINI_URL, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL

    required = ["GEMINI_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
    GEMINI_URL = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"].strip()
    # Gmail app passwords are often copy-pasted with spaces (as Google displays them)
    GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"].strip().replace(" ", "")
    RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"].strip()


# ---------------------------------------------------------------------------
# 2. Fetch recent articles from RSS feeds
# ---------------------------------------------------------------------------

def fetch_recent_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles = []
    seen_titles = set()

    for feed_url in RSS_FEEDS:
        try:
            # Fetch with an explicit timeout first — feedparser.parse(url) has
            # no timeout of its own and can hang the whole job on a slow feed.
            resp = requests.get(
                feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (AI Digest Bot)"}
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"Could not read feed {feed_url}: {e}")
            continue

        if feed.bozo and not feed.entries:
            print(f"Feed looked malformed and had no entries, skipping: {feed_url} ({feed.bozo_exception})")
            continue

        source_name = feed.feed.get("title", feed_url)

        for entry in feed.entries:
            published = _get_published_datetime(entry)
            if published is None or published < cutoff:
                continue

            title = entry.get("title", "Untitled")
            dedup_key = " ".join(title.lower().split())
            if dedup_key in seen_titles:
                continue  # same story already picked up from another feed
            seen_titles.add(dedup_key)

            articles.append(
                {
                    "source": source_name,
                    "title": title,
                    "link": entry.get("link", ""),
                    "summary": _clean_summary(entry.get("summary", "")),
                    "published": published,
                }
            )

    # Most recent first, so if we later cap the list we keep the freshest news
    articles.sort(key=lambda a: a["published"], reverse=True)
    return articles


def _get_published_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def _clean_summary(raw_html, max_len=400):
    import re

    text = re.sub("<[^<]+?>", "", raw_html or "")
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    # cut at the last space so we don't leave a chopped-off word
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    return (truncated[:last_space] if last_space > 0 else truncated) + "…"


# ---------------------------------------------------------------------------
# 3. Summarize with Gemini (free tier)
# ---------------------------------------------------------------------------

def build_prompt(articles):
    today = datetime.now().strftime("%B %d, %Y")

    lines = [
        f"You are writing a concise daily AI industry briefing for {today}.",
        "Below are raw article titles, sources, and snippets collected in the last day.",
        "Group related stories, drop duplicates, and write a well-organized summary using:",
        "- A 2-3 sentence 'Top Story' section for the single most important development",
        "- A 'New Tools & Releases' bulleted section",
        "- A 'Research & Industry News' bulleted section",
        "- Keep each bullet to one line, punchy and specific (mention the company/product name)",
        "- Do not invent facts not present in the source material",
        "- IMPORTANT: Keep the entire summary under 450 words total. Always finish with a",
        "  complete sentence — never cut a sentence or bullet off mid-way. If you are running",
        "  long, drop less important bullets rather than leaving anything unfinished.",
        "",
        "SOURCE MATERIAL:",
    ]

    for a in articles:
        lines.append(f"- [{a['source']}] {a['title']}: {a['summary']} ({a['link']})")

    return "\n".join(lines)


def _trim_to_last_complete_sentence(text):
    """Safety net: if the model still got cut off mid-sentence despite
    instructions, trim back to the last clean sentence/bullet ending rather
    than emailing a broken fragment."""
    text = text.rstrip()
    if not text:
        return text

    # Already ends cleanly (sentence punctuation, or a markdown list/heading line)
    if text[-1] in ".!?" or text.endswith((":", '"', "”", ")")):
        return text

    last_break = max(text.rfind(". "), text.rfind("! "), text.rfind("? "), text.rfind("\n"))
    if last_break >= 10:  # a valid break exists and leaves a non-trivial result
        return text[: last_break + 1].rstrip()
    return text  # nothing better to do — return as-is rather than gut the summary


def summarize_with_gemini(articles):
    if not articles:
        return "No new AI articles were found in the lookback window today."

    # Cap how much source material goes in, so the expected output length
    # stays comfortably within the token budget below.
    MAX_ARTICLES = 35
    articles = articles[:MAX_ARTICLES]

    prompt = build_prompt(articles)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 2048,
            "temperature": 0.4,
        },
    }

    import time

    last_error = None
    for attempt in range(4):
        response = requests.post(GEMINI_URL, json=payload, timeout=60)
        if response.ok:
            data = response.json()
            candidates = data.get("candidates") or []

            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                print(f"Warning: Gemini returned no candidates (blockReason={block_reason}). Falling back to a plain list.")
                return _fallback_plain_summary(articles)

            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            if not parts:
                print("Warning: Gemini candidate had no text content. Falling back to a plain list.")
                return _fallback_plain_summary(articles)

            text = parts[0].get("text", "")
            finish_reason = candidate.get("finishReason")

            if finish_reason == "MAX_TOKENS":
                print("Warning: Gemini hit the output token limit; trimming to last complete sentence.")
                text = _trim_to_last_complete_sentence(text)

            return text.strip() or _fallback_plain_summary(articles)

        last_error = f"Gemini API error {response.status_code}: {response.text[:1000]}"
        if response.status_code in (429, 500, 503):
            time.sleep(2 ** attempt * 5)  # 5s, 10s, 20s, 40s
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(f"Gemini API still failing after retries: {last_error}")


def _fallback_plain_summary(articles):
    """Used if Gemini can't produce a summary for some reason (safety filter,
    empty response, etc.) — better to email a plain headline list than nothing."""
    lines = ["Gemini couldn't generate a written summary today, so here are today's raw headlines:", ""]
    for a in articles[:35]:
        lines.append(f"- [{a['source']}] {a['title']} ({a['link']})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Email the digest
# ---------------------------------------------------------------------------

def send_email(summary_text, article_count):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"🤖 Your AI Daily Digest — {today}"

    html_body = _to_html(summary_text, today, article_count)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(summary_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"Email sent to {RECIPIENT_EMAIL}")


def _to_html(summary_text, today, article_count):
    import html
    import re

    def format_inline(text):
        text = html.escape(text)
        # convert **bold** and *italic* markdown (after escaping, so this is safe)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
        return text

    # Turn simple markdown-ish text (## headers, - bullets) into basic HTML
    lines = summary_text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ")):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{format_inline(stripped[2:])}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            html_lines.append(f"<h3>{format_inline(text)}</h3>")
        else:
            html_lines.append(f"<p>{format_inline(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)

    return f"""
    <html>
      <body style="font-family: -apple-system, Arial, sans-serif; max-width: 640px; margin: auto; color: #1a1a1a;">
        <h2 style="border-bottom: 2px solid #4f46e5; padding-bottom: 8px;">🤖 AI Daily Digest — {html.escape(today)}</h2>
        <p style="color:#555; font-size: 13px;">Summarized from {article_count} articles across major AI news sources.</p>
        {body}
        <hr style="margin-top:32px;">
        <p style="color:#999; font-size:12px;">Generated automatically by your free AI News Agent (GitHub Actions + Gemini API).</p>
      </body>
    </html>
    """


# ---------------------------------------------------------------------------
# 5. Main
# ---------------------------------------------------------------------------

def main():
    load_config()
    print("Fetching recent AI articles...")
    articles = fetch_recent_articles()
    print(f"Found {len(articles)} recent articles.")

    print("Summarizing with Gemini...")
    summary = summarize_with_gemini(articles)

    print("Sending email...")
    send_email(summary, len(articles))

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        with open("debug_log.txt", "w") as f:
            f.write(traceback.format_exc())
        raise

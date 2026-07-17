import os
import sys
import requests
import smtplib
import argparse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()  # no-op on GitHub Actions if there's no .env file — that's fine, secrets come via env vars there
SENDER_EMAIL   = os.getenv("SENDER_EMAIL")
APP_PASSWORD   = os.getenv("APP_PASSWORD")
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")

TARGET_TAGS = ["machinelearning", "webdev", "python", "javascript"]
MIN_REACTIONS = 5  # lowered further — 1-day windows rarely produce many 8+ reaction posts


def fetch_trending_topics(tag: str) -> list:
    url = f"https://dev.to/api/articles?tag={tag}&top=3&per_page=20"  # 3-day pool, up to 20 articles per tag before filtering
    headers = {"User-Agent": "Mozilla/5.0 TechTrendMonitorAgent/1.0"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching tag #{tag}: {e}")
        return []


def gather_and_filter_trends():
    stories_by_tag = {}
    for tag in TARGET_TAGS:
        print(f"Scanning #{tag}...")
        articles = fetch_trending_topics(tag)
        qualifying = []
        for a in articles:
            reactions = a.get("public_reactions_count", 0)
            if reactions >= MIN_REACTIONS:
                qualifying.append({
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "tag": tag,
                    "author": a.get("user", {}).get("name", "Anonymous"),
                    "reactions": reactions,
                    "reading_time": a.get("reading_time_minutes", 1)
                })
        qualifying.sort(key=lambda x: x["reactions"], reverse=True)
        stories_by_tag[tag] = qualifying
        print(f"  #{tag}: {len(qualifying)} qualifying articles")

    # Step 1: guarantee up to 2 slots per tag so no single tag can dominate/starve the rest
    selected = []
    for tag in TARGET_TAGS:
        selected.extend(stories_by_tag[tag][:2])

    # Step 2: if that's fewer than 5 total, backfill with next-best leftovers regardless of tag
    if len(selected) < 5:
        leftovers = []
        for tag in TARGET_TAGS:
            leftovers.extend(stories_by_tag[tag][2:])
        leftovers.sort(key=lambda x: x["reactions"], reverse=True)
        selected.extend(leftovers[: 5 - len(selected)])

    selected.sort(key=lambda x: x["reactions"], reverse=True)
    result = selected[:5]
    print(f"Final digest: {len(result)} stories across {len(set(s['tag'] for s in result))} tags")
    return result


def build_html_template(top_stories: list) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    rows = ""
    for s in top_stories:
        rows += f"""
        <tr>
          <td style="padding:16px 0; border-bottom:1px solid #eef2f6;">
            <span style="background:#ffefe5; color:#ff6b00; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700; text-transform:uppercase;">#{s['tag']}</span>
            <div style="margin-top:8px;">
              <a href="{s['url']}" style="color:#0f172a; text-decoration:none; font-size:16px; font-weight:700; line-height:1.4;">{s['title']}</a>
            </div>
            <div style="margin-top:6px; color:#64748b; font-size:13px;">
              By {s['author']} • ⏱️ {s['reading_time']} min read • 🔥 <strong>{s['reactions']} reactions</strong>
            </div>
          </td>
        </tr>"""
    if not rows:
        rows = "<tr><td style='padding:20px; text-align:center; color:#64748b;'>No trending stories hit filters today.</td></tr>"

    return f"""
    <!DOCTYPE html><html><body>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc; padding:30px 0; font-family:system-ui,-apple-system,sans-serif;">
      <tr><td align="center">
        <table width="550" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #e2e8f0; box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
          <tr><td style="background:#0f172a; padding:32px; text-align:center;">
            <h1 style="margin:0; color:#ffffff; font-size:22px;">🔥 High-Value Dev Discussions</h1>
            <p style="margin:6px 0 0; color:#94a3b8; font-size:14px;">Curated Intelligence for {today}</p>
          </td></tr>
          <tr><td style="padding:20px 32px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
          </td></tr>
        </table>
      </td></tr>
    </table></body></html>"""


def send_digest(top_stories: list):
    # Validate ALL three required values, not just two of them
    missing = [name for name, val in [
        ("SENDER_EMAIL", SENDER_EMAIL),
        ("APP_PASSWORD", APP_PASSWORD),
        ("RECEIVER_EMAIL", RECEIVER_EMAIL),
    ] if not val]

    if missing:
        print(f"❌ Missing required env vars: {', '.join(missing)}")
        print("   Check that these are set as GitHub Repository Secrets AND")
        print("   mapped in your workflow YAML under 'env:' for the step that runs this script.")
        sys.exit(1)  # <-- fail the job loudly instead of silently returning

    print(f"Attempting to send email from {SENDER_EMAIL} to {RECEIVER_EMAIL}...")

    msg = MIMEMultipart("alternative")
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = f"💡 Tech Trend Monitor — Daily Top {len(top_stories)}"
    msg.attach(MIMEText(build_html_template(top_stories), "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print("✓ Trend Digest sent successfully!")
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Gmail rejected the login: {e}")
        print("   APP_PASSWORD must be a 16-character Gmail *App Password*, not your normal password.")
        print("   Generate one at https://myaccount.google.com/apppasswords (requires 2FA enabled).")
        sys.exit(1)
    except Exception as e:
        print(f"❌ SMTP Mail Error occurred: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true")
    args = parser.parse_args()
    send_digest(gather_and_filter_trends())

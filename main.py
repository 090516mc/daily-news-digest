#!/usr/bin/env python3
"""
Daily News Digest - Auto Fetch, Sort by Length, PDF Generation, Email Delivery
Runs on GitHub Actions, zero local computer dependency.
"""

import os
import sys
import json
import hashlib
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from fpdf import FPDF

# ============================================================
# Configuration
# ============================================================
RSS_FEEDS = {
    "BBC Top": "https://feeds.bbci.co.uk/news/rss.xml",
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Tech": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "CNN Top": "http://rss.cnn.com/rss/cnn_topstories.rss",
    "Reuters World": "https://feeds.reuters.com/reuters/worldNews",
    "NPR News": "https://feeds.npr.org/1001/rss.xml",
    "AP Top": "https://rss.app/feeds/8wsn3fPqHy0OqB9c.xml",
    "Hacker News": "https://hnrss.org/frontpage",
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
}

MAX_ARTICLES = 10
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# SMTP settings from environment
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_TO = os.environ.get("MAIL_TO", "")


# ============================================================
# Step 1: RSS Fetching
# ============================================================
def is_within_24h(published_str: str) -> bool:
    if not published_str:
        return True
    date_formats = [
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    ]
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(published_str.strip(), fmt)
            break
        except (ValueError, AttributeError):
            continue
    else:
        return True
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=None)
        now = datetime.now()
    cutoff = now - timedelta(hours=24)
    if parsed.tzinfo:
        cutoff = cutoff.astimezone(parsed.tzinfo)
    return parsed >= cutoff


def fetch_feed(url: str) -> list:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            return []
        entries = []
        for entry in feed.entries[:15]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            summary = entry.get("summary", entry.get("description", ""))
            published = entry.get("published", entry.get("updated", ""))
            if not title or not link:
                continue
            summary = BeautifulSoup(summary, "lxml").get_text(separator=" ", strip=True) if summary else ""
            entries.append({"title": title, "url": link, "summary": summary[:500], "published": published})
        return entries
    except Exception as e:
        print(f"  [skip] {url}: {e}")
        return []


def fetch_full_content(url: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        for selector in ["article", '[class*="article"]', '[class*="content"]', '[class*="body"]', "main", ".post-content", ".entry-content", "#article-body", ".story-body"]:
            div = soup.select_one(selector)
            if div and len(div.get_text(strip=True)) > 200:
                return " ".join(p.get_text(strip=True) for p in div.find_all("p") if len(p.get_text(strip=True)) > 20)
        paragraphs = soup.find_all("p")
        return " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)
    except Exception:
        return ""


# ============================================================
# Step 2: PDF Generation
# ============================================================
def clean_text(text: str) -> str:
    """Remove unsupported Unicode characters, keep only ASCII printable."""
    return "".join(c if 32 <= ord(c) <= 126 else " " for c in text)


def generate_pdf(articles: list, output_path: str):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    font_name = "Helvetica"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # Cover
    pdf.set_font(font_name, "B", 24)
    pdf.cell(0, 15, "Daily News Digest", ln=True, align="C")
    pdf.set_font(font_name, "", 12)
    pdf.cell(0, 10, date_str, ln=True, align="C")
    pdf.cell(0, 10, f"Top {len(articles)} Longest Articles in Past 24 Hours", ln=True, align="C")
    pdf.ln(10)

    # Table of Contents
    pdf.set_font(font_name, "B", 16)
    pdf.cell(0, 10, "Table of Contents", ln=True)
    pdf.set_font(font_name, "", 10)
    for i, a in enumerate(articles, 1):
        title = clean_text(a["title"][:80])
        chars = a.get("content_length", 0)
        pdf.cell(0, 7, f"{i}. {title}  ({chars:,} chars)", ln=True)
    pdf.ln(5)

    # Articles
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    for i, a in enumerate(articles, 1):
        pdf.add_page()
        pdf.set_font(font_name, "B", 16)
        pdf.cell(0, 8, f"Article {i}", ln=True)
        pdf.set_font(font_name, "B", 14)
        pdf.cell(page_w, 8, clean_text(a["title"][:120]), ln=True)
        pdf.set_font(font_name, "", 9)
        pdf.cell(0, 6, f"Source: {a.get('source_name', 'Unknown')}  |  Length: {a.get('content_length', 0):,} chars", ln=True)
        pdf.cell(0, 6, f"URL: {clean_text(a['url'][:100])}", ln=True)
        pdf.ln(4)

        pdf.set_font(font_name, "B", 11)
        pdf.cell(0, 7, "Summary", ln=True)
        pdf.set_font(font_name, "", 10)
        summary = clean_text(a.get("summary", ""))[:800]
        pdf.write(6, summary)
        pdf.ln(6)

        pdf.set_font(font_name, "B", 11)
        pdf.cell(0, 7, "Full Content", ln=True)
        pdf.set_font(font_name, "", 9)
        full = clean_text(a.get("full_content", a.get("summary", "No content available")))
        pdf.write(5, full)

    pdf.output(output_path)
    print(f"  PDF saved: {output_path} ({os.path.getsize(output_path)/1024:.1f} KB)")


# ============================================================
# Step 3: Email Sending
# ============================================================
def send_email(pdf_path: str, articles: list):
    if not SMTP_USERNAME or not SMTP_PASSWORD or not MAIL_TO:
        print("  [skip] Email not configured")
        return False

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    # Build plain text body
    text_lines = []
    text_lines.append("=" * 50)
    text_lines.append(f"Daily News Digest - {date_str}")
    text_lines.append("=" * 50)
    text_lines.append(f"Top {len(articles)} longest articles from past 24 hours")
    text_lines.append("")
    for i, a in enumerate(articles, 1):
        text_lines.append(f"{i}. {a['title'][:80]}")
        text_lines.append(f"   Source: {a.get('source_name', 'Unknown')} | {a.get('content_length', 0):,} chars")
        text_lines.append(f"   URL: {a['url']}")
        text_lines.append("")
    text_lines.append("-" * 50)
    text_lines.append("Generated by GitHub Actions | Daily News Digest")
    text_lines.append("Full articles in the attached PDF.")

    # Build HTML body
    html_lines = [
        '<html><body style="font-family:Arial,sans-serif;max-width:700px">',
        '<h2 style="color:#1a73e8">Daily News Digest</h2>',
        f'<p><strong>{date_str}</strong> | {len(articles)} longest articles from past 24h</p>',
        '<hr>',
        '<h3>Top Articles</h3>',
        '<ol>',
    ]
    for a in articles:
        html_lines.append(
            f'<li><strong>{a["title"][:80]}</strong><br>'
            f'<span style="color:#666;font-size:12px">'
            f'{a.get("content_length",0):,} chars | {a.get("source_name","")}</span></li>'
        )
    html_lines.append('</ol>')
    html_lines.append('<hr>')
    html_lines.append('<p style="color:#999;font-size:12px">Full articles in the attached PDF.<br>Generated by GitHub Actions | Daily News Digest</p>')
    html_lines.append('</body></html>')

    # Build multipart: alternative (text + html) inside mixed (body + attachment)
    msg = MIMEMultipart("mixed")
    msg["From"] = SMTP_USERNAME
    msg["To"] = MAIL_TO
    msg["Subject"] = f"Daily News Digest - {date_str}"

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText("\n".join(text_lines), "plain", "utf-8"))
    alt_part.attach(MIMEText("\n".join(html_lines), "html", "utf-8"))
    msg.attach(alt_part)

    # Attach PDF
    with open(pdf_path, "rb") as f:
        pdf_part = MIMEApplication(f.read(), _subtype="pdf", name="daily_news_digest.pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename="daily_news_digest.pdf")
    msg.attach(pdf_part)

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_USERNAME, MAIL_TO.split(","), msg.as_string())
        server.quit()
        print(f"  Email sent to {MAIL_TO}")
        return True
    except Exception as e:
        print(f"  Email failed: {e}")
        return False


def main():
    print("=" * 60)
    print("Daily News Digest")
    print(f"Start: {datetime.now().isoformat()}")
    print("=" * 60)

    # Step 1: Fetch RSS feeds
    print("\n[1/3] Fetching RSS feeds...")
    all_articles = []
    for name, url in RSS_FEEDS.items():
        print(f"  {name}...")
        entries = fetch_feed(url)
        for e in entries:
            e["source_name"] = name
        all_articles.extend(entries)
        print(f"    -> {len(entries)} entries")

    # Deduplicate
    seen = set()
    deduped = []
    for a in all_articles:
        key = hashlib.md5(a["url"].encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    all_articles = deduped
    print(f"  After dedup: {len(all_articles)}")

    # Filter by 24h
    fresh = [a for a in all_articles if is_within_24h(a.get("published", ""))]
    print(f"  Within 24h: {len(fresh)}")
    if len(fresh) < MAX_ARTICLES:
        fallback = [a for a in all_articles if a not in fresh]
        needed = MAX_ARTICLES - len(fresh)
        fresh.extend(fallback[:needed])
        print(f"  Supplemented {min(needed, len(fallback))} recent articles")

    # Fetch full content
    print(f"\n  Fetching full content for {len(fresh)} articles...")
    for i, a in enumerate(fresh):
        print(f"    [{i+1}/{len(fresh)}] {a['title'][:60]}...")
        content = fetch_full_content(a["url"])
        a["full_content"] = content if content else a.get("summary", "")
        a["content_length"] = len(a["full_content"])
        time.sleep(0.5)

    # Sort by length and pick top
    fresh.sort(key=lambda a: a["content_length"], reverse=True)
    top = fresh[:MAX_ARTICLES]
    print(f"\n  Top {len(top)} longest articles:")
    for i, a in enumerate(top, 1):
        print(f"    {i}. [{a['content_length']:,} chars] {a['title'][:70]}...")

    # Step 2: Generate PDF
    print(f"\n[2/3] Generating PDF...")
    pdf_path = "/tmp/daily_news_digest.pdf"
    generate_pdf(top, pdf_path)

    # Step 3: Send email
    print(f"\n[3/3] Sending email...")
    send_email(pdf_path, top)

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
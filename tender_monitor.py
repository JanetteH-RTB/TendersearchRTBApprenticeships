#!/usr/bin/env python3
"""
RTB Apprenticeship Tender Monitor
---------------------------------
Checks Find a Tender and Contracts Finder each day for new procurement
notices relevant to Raise the Bar's apprenticeship provision, then emails
a digest to the bids team.

Runs on GitHub Actions (see .github/workflows/tender-alerts.yml).
No server, no database. State (already-seen notices) is kept in seen.json,
committed back to the repo by the workflow so we never email the same
notice twice.

Configuration is via environment variables / GitHub Secrets:
  SMTP_USER      - the Gmail address that sends the digest
  SMTP_PASS      - the Gmail app password (16 chars, no spaces)
  MAIL_TO        - recipient(s), comma separated
  LOOKBACK_DAYS  - optional, how many days back to scan (default 2)
"""

import os
import re
import sys
import json
import time
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# --------------------------------------------------------------------------
# CONFIG - edit these lists to tune what gets caught
# --------------------------------------------------------------------------

# Keywords matched against notice title + description (case-insensitive).
# Grouped only for readability; they all behave the same way.
KEYWORDS = [
    # Team Leader
    "team leader", "team leader/supervisor", "supervisor", "first line manager",
    # Operations Manager
    "operations manager", "operations/departmental manager",
    "departmental manager", "operational management",
    # Coaching Professional
    "coaching professional", "coaching", "coach",
    # Improvement Specialist / Technician
    "improvement specialist", "improvement technician",
    "continuous improvement", "process improvement", "business improvement", "lean",
    # AI & Automation
    "ai and automation", "artificial intelligence", "ai specialist",
    "automation specialist",
    # Associate Project Manager
    "associate project manager", "project manager", "project management",
    # Business Administrator
    "business administrator", "business admin", "administration apprenticeship",
    # General apprenticeship safety net
    "apprenticeship", "apprentice", "apprenticeship levy",
]

# CPV codes for education / training / coaching. A notice tagged with any of
# these is treated as relevant even if the keywords don't hit, so we catch
# broad "training provider framework" style tenders.
CPV_PREFIXES = [
    "79998000",  # Coaching services
    "80000000",  # Education and training services (parent)
    "80400000",  # Adult and other education services
    "80420000",  # E-learning services
    "80500000",  # Training services
    "80510000",  # Specialist training services
    "80511000",  # Staff training services
    "80521000",  # Training programme services
    "80530000",  # Vocational training services
    "80531000",  # Industrial and technical training services
    "80532000",  # Management training services
    "80570000",  # Personal development training services
]

# No exclusions requested. Add lower-cased words here later to drop noise.
EXCLUDE_WORDS = []

# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_TO = os.environ.get("MAIL_TO", "appsbidsteam@raisethebar.co.uk")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "2"))

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
USER_AGENT = "RTB-Tender-Monitor/1.0 (+https://raisethebar.co.uk)"
HEADERS = {"Accept": "application/json", "User-Agent": USER_AGENT}

# --------------------------------------------------------------------------
# Seen-notice persistence
# --------------------------------------------------------------------------
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    # Keep the file from growing forever: cap at most recent 5000 ids.
    trimmed = list(seen)[-5000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=0)


# --------------------------------------------------------------------------
# HTTP with polite retry on rate limiting
# --------------------------------------------------------------------------
def get_json(url, params=None):
    for attempt in range(5):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "10"))
            print(f"  rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Repeatedly rate limited fetching {url}")


# --------------------------------------------------------------------------
# Relevance test
# --------------------------------------------------------------------------
def text_of(tender):
    return " ".join(filter(None, [
        tender.get("title", ""),
        tender.get("description", ""),
    ])).lower()


def cpv_codes(tender):
    codes = []
    main = tender.get("classification") or {}
    if main.get("scheme") == "CPV" and main.get("id"):
        codes.append(str(main["id"]))
    for c in tender.get("additionalClassifications") or []:
        if c.get("scheme") == "CPV" and c.get("id"):
            codes.append(str(c["id"]))
    return codes


def is_relevant(tender):
    text = text_of(tender)

    # Exclusions first (none by default).
    for bad in EXCLUDE_WORDS:
        if bad in text:
            return False, None

    # CPV match: does any code start with one of our prefixes?
    codes = cpv_codes(tender)
    for code in codes:
        for pref in CPV_PREFIXES:
            if code.startswith(pref[:5]) and code.startswith(pref[:8]):
                return True, "CPV code " + code
        # 80-division catch-all: any education/training code
        if code.startswith("80"):
            return True, "CPV code " + code + " (education/training)"

    # Keyword match on title/description.
    for kw in KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            return True, 'matched "' + kw + '"'

    return False, None


# --------------------------------------------------------------------------
# Source: Find a Tender (OCDS release packages, cursor paginated)
# --------------------------------------------------------------------------
def fetch_find_a_tender(since):
    base = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
    params = {"updatedFrom": since.strftime("%Y-%m-%dT%H:%M:%S"), "limit": 100}
    out = []
    url = base
    pages = 0
    while url and pages < 40:
        data = get_json(url, params=params if url == base else None)
        for rel in data.get("releases", []):
            tender = rel.get("tender") or {}
            notice_id = rel.get("id") or rel.get("ocid")
            if not notice_id:
                continue
            link = ("https://www.find-tender.service.gov.uk/Notice/"
                    + str(notice_id))
            out.append({
                "source": "Find a Tender",
                "id": "FTS:" + str(notice_id),
                "title": tender.get("title") or "(untitled)",
                "description": tender.get("description") or "",
                "value": (tender.get("value") or {}).get("amount"),
                "currency": (tender.get("value") or {}).get("currency", "GBP"),
                "deadline": tender.get("tenderPeriod", {}).get("endDate"),
                "buyer": (rel.get("buyer") or {}).get("name", ""),
                "link": link,
                "tender": tender,
            })
        # OCDS pagination: follow links.next if present.
        nxt = (data.get("links") or {}).get("next")
        url = nxt
        params = None
        pages += 1
    return out


# --------------------------------------------------------------------------
# Source: Contracts Finder (OCDS search API)
# --------------------------------------------------------------------------
def fetch_contracts_finder(since):
    url = ("https://www.contractsfinder.service.gov.uk/Published/"
           "Notices/OCDS/Search")
    params = {
        "publishedFrom": since.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": "tender",
        "size": 100,
    }
    out = []
    cursor_pages = 0
    while url and cursor_pages < 40:
        data = get_json(url, params=params if cursor_pages == 0 else None)
        results = data.get("results") or data.get("releases") or []
        for item in results:
            releases = item.get("releases") or [item]
            for rel in releases:
                tender = rel.get("tender") or {}
                notice_id = rel.get("id") or rel.get("ocid")
                if not notice_id:
                    continue
                cf_link = ""
                for l in rel.get("links", []) if isinstance(rel.get("links"), list) else []:
                    if isinstance(l, dict) and l.get("href"):
                        cf_link = l["href"]
                        break
                if not cf_link:
                    cf_link = ("https://www.contractsfinder.service.gov.uk/"
                               "Notice/" + str(notice_id))
                out.append({
                    "source": "Contracts Finder",
                    "id": "CF:" + str(notice_id),
                    "title": tender.get("title") or "(untitled)",
                    "description": tender.get("description") or "",
                    "value": (tender.get("value") or {}).get("amount"),
                    "currency": (tender.get("value") or {}).get("currency", "GBP"),
                    "deadline": tender.get("tenderPeriod", {}).get("endDate"),
                    "buyer": (rel.get("buyer") or {}).get("name", ""),
                    "link": cf_link,
                    "tender": tender,
                })
        nxt = (data.get("links") or {}).get("next")
        url = nxt
        params = None
        cursor_pages += 1
    return out


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
def format_value(v, cur):
    if v is None:
        return "Value not stated"
    try:
        return f"{cur} {float(v):,.0f}"
    except (ValueError, TypeError):
        return str(v)


def build_email(matches):
    plain = [f"{len(matches)} new tender(s) matching RTB apprenticeship criteria.\n"]
    html = [
        "<html><body style='font-family:Arial,Helvetica,sans-serif;color:#222'>",
        f"<h2>RTB Tender Alert &ndash; {len(matches)} new opportunity(ies)</h2>",
        "<p>New notices from Find a Tender and Contracts Finder matching "
        "your apprenticeship search criteria.</p>",
    ]
    for m in matches:
        val = format_value(m["value"], m["currency"])
        deadline = m["deadline"] or "Not stated"
        plain.append(
            f"\n[{m['source']}] {m['title']}\n"
            f"  Buyer: {m['buyer'] or 'n/a'}\n"
            f"  Value: {val}\n"
            f"  Deadline: {deadline}\n"
            f"  Why flagged: {m['reason']}\n"
            f"  Link: {m['link']}\n"
        )
        desc = (m["description"] or "")[:400]
        html.append(
            "<div style='border-left:4px solid #0b5fff;padding:8px 14px;"
            "margin:14px 0;background:#f7f9ff'>"
            f"<div style='font-size:12px;color:#0b5fff;font-weight:bold'>{m['source']}</div>"
            f"<div style='font-size:16px;font-weight:bold;margin:2px 0'>"
            f"<a href='{m['link']}' style='color:#111;text-decoration:none'>{m['title']}</a></div>"
            f"<div style='font-size:13px;color:#555'>Buyer: {m['buyer'] or 'n/a'} &nbsp;|&nbsp; "
            f"{val} &nbsp;|&nbsp; Deadline: {deadline}</div>"
            f"<div style='font-size:12px;color:#0a7d34;margin:4px 0'>Flagged: {m['reason']}</div>"
            f"<div style='font-size:13px;color:#333;margin-top:6px'>{desc}</div>"
            f"<div style='margin-top:8px'><a href='{m['link']}' "
            "style='background:#0b5fff;color:#fff;padding:6px 12px;"
            "border-radius:4px;text-decoration:none;font-size:13px'>View notice</a></div>"
            "</div>"
        )
    html.append("<p style='font-size:11px;color:#999'>Automated daily scan. "
                "To adjust keywords or exclusions, edit tender_monitor.py.</p>")
    html.append("</body></html>")
    return "\n".join(plain), "\n".join(html)


def send_email(subject, plain, html):
    if not (SMTP_USER and SMTP_PASS):
        print("SMTP_USER / SMTP_PASS not set - printing digest instead of sending:\n")
        print(plain)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [a.strip() for a in MAIL_TO.split(",")], msg.as_string())
    print(f"Digest emailed to {MAIL_TO}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=LOOKBACK_DAYS)
    print(f"Scanning notices updated since {since:%Y-%m-%d %H:%M} UTC")

    seen = load_seen()
    notices = []

    for name, fetch in [("Find a Tender", fetch_find_a_tender),
                        ("Contracts Finder", fetch_contracts_finder)]:
        try:
            got = fetch(since)
            print(f"  {name}: {len(got)} notices pulled")
            notices.extend(got)
        except Exception as e:  # one source failing must not kill the run
            print(f"  {name}: ERROR {e}", file=sys.stderr)

    matches = []
    for n in notices:
        if n["id"] in seen:
            continue
        ok, reason = is_relevant(n["tender"])
        if ok:
            n["reason"] = reason
            matches.append(n)
        seen.add(n["id"])

    print(f"{len(matches)} new relevant notice(s) found")

    if matches:
        subject = f"RTB Tender Alert: {len(matches)} new apprenticeship opportunity(ies)"
        plain, html = build_email(matches)
        send_email(subject, plain, html)
    else:
        print("Nothing new to send today.")

    save_seen(seen)


if __name__ == "__main__":
    main()

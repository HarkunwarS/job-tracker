"""
Ireland Tech Job Tracker v2
─────────────────────────────────────────────────────────────────────────────
Sources:
  1. Greenhouse ATS  — free public JSON API (no auth needed)
  2. Lever ATS       — free public JSON API (no auth needed)
  3. LinkedIn        — public job search (entry-level, Ireland, last 24 h)

Runs on GitHub Actions twice a day. Sends one email digest of all new jobs.
"""

import json
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

JOBS_FILE = Path("data/seen_jobs.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

IRELAND_KEYWORDS = {"ireland", "dublin", "cork", "galway", "limerick", "waterford", "remote"}

# ── Company → ATS mapping ─────────────────────────────────────────────────────
#
# Greenhouse API : https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# Lever API      : https://api.lever.co/v0/postings/{slug}?mode=json
#
# To add a new company:
#   - Find if they use Greenhouse → check https://boards.greenhouse.io/{companyname}
#   - Find if they use Lever      → check https://jobs.lever.co/{companyname}
#   - Add an entry below with the right ATS type and slug

GREENHOUSE_COMPANIES = {
    "Cloudflare":  "cloudflare",
    "Workhuman":   "workhuman",
    "Intercom":    "intercom",
    "HubSpot":     "hubspot",
    "MongoDB":     "mongodb",
    "Tines":       "tines",
    "Teamwork":    "teamwork",
    "Indeed":      "indeed",
    "Arctic Wolf": "arcticwolf",
    "Shopify":     "shopify",
}

LEVER_COMPANIES = {
    "Stripe":   "stripe",
    "Revolut":  "revolut",
    "SAP":      "sap",
}

# LinkedIn keyword searches — covers companies whose careers pages are harder to scrape
# (Google, Meta, Microsoft, Apple, Amazon, IBM, Oracle, Salesforce, Accenture,
#  Deloitte, PwC, TCS, PayPal, Mastercard, JP Morgan, Qualcomm, Workday)
LINKEDIN_QUERIES = [
    "software engineer",
    "data engineer",
    "data scientist",
    "machine learning engineer",
    "frontend developer",
    "backend developer",
    "fullstack developer",
    "devops engineer",
    "cloud engineer",
    "QA engineer",
    "site reliability engineer",
    "cybersecurity analyst",
    "solutions architect",
    "product manager technology",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(JOBS_FILE, "w") as f:
        json.dump(list(seen), f)


def is_ireland(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in IRELAND_KEYWORDS)


def is_entry_level(text: str) -> bool:
    text = text.lower()
    # Exclude obviously senior roles
    senior_signals = ["senior", "staff", "principal", "director", "vp ", "vice president",
                      "head of", "manager", "lead ", " lead,", "architect", "distinguished"]
    return not any(s in text for s in senior_signals)


# ── Source 1: Greenhouse ──────────────────────────────────────────────────────

def fetch_greenhouse(company_name: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    except Exception as e:
        print(f"    [WARN] Greenhouse {company_name}: {e}")
        return []

    results = []
    for job in jobs:
        location = job.get("location", {}).get("name", "")
        title = job.get("title", "")
        if not is_ireland(location) and not is_ireland(title):
            continue
        if not is_entry_level(title):
            continue
        results.append({
            "id":      f"gh_{slug}_{job['id']}",
            "title":   title,
            "company": company_name,
            "location": location or "Ireland",
            "link":    job.get("absolute_url", f"https://boards.greenhouse.io/{slug}"),
            "posted":  "Today",
            "source":  "Direct (Greenhouse)",
        })
    return results


# ── Source 2: Lever ───────────────────────────────────────────────────────────

def fetch_lever(company_name: str, slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json()
    except Exception as e:
        print(f"    [WARN] Lever {company_name}: {e}")
        return []

    results = []
    for job in jobs:
        location = job.get("categories", {}).get("location", "")
        title = job.get("text", "")
        if not is_ireland(location) and not is_ireland(title):
            continue
        if not is_entry_level(title):
            continue
        results.append({
            "id":      f"lv_{slug}_{job['id']}",
            "title":   title,
            "company": company_name,
            "location": location or "Ireland",
            "link":    job.get("hostedUrl", f"https://jobs.lever.co/{slug}"),
            "posted":  "Today",
            "source":  "Direct (Lever)",
        })
    return results


# ── Source 3: LinkedIn ────────────────────────────────────────────────────────

def fetch_linkedin(keyword: str) -> list[dict]:
    import urllib.parse
    encoded = urllib.parse.quote_plus(keyword)
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={encoded}&location=Ireland&f_TPR=r86400&f_E=1,2"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"    [WARN] LinkedIn '{keyword}': {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.find_all("div", class_="base-card")
    results = []

    for card in cards:
        try:
            job_id = card.get("data-entity-urn", "").split(":")[-1]
            if not job_id:
                continue
            title_el   = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            loc_el     = card.find("span", class_="job-search-card__location")
            link_el    = card.find("a", class_="base-card__full-link")
            posted_el  = card.find("time")

            title = title_el.get_text(strip=True) if title_el else ""
            if not is_entry_level(title):
                continue

            results.append({
                "id":      f"li_{job_id}",
                "title":   title,
                "company": company_el.get_text(strip=True) if company_el else "N/A",
                "location": loc_el.get_text(strip=True) if loc_el else "Ireland",
                "link":    link_el["href"].split("?")[0] if link_el else url,
                "posted":  posted_el["datetime"] if posted_el and posted_el.get("datetime") else "Recently",
                "source":  f"LinkedIn ({keyword})",
            })
        except Exception:
            continue

    return results


# ── Email ─────────────────────────────────────────────────────────────────────

def build_email(new_jobs: list[dict]) -> str:
    date_str = datetime.now().strftime("%d %B %Y")

    # Group by source type
    direct_jobs = [j for j in new_jobs if "Direct" in j["source"]]
    linkedin_jobs = [j for j in new_jobs if "LinkedIn" in j["source"]]

    def job_row(job: dict) -> str:
        source_color = "#0d6e56" if "Direct" in job["source"] else "#185FA5"
        source_label = "🏢 Direct" if "Direct" in job["source"] else "🔗 LinkedIn"
        return f"""
        <tr>
          <td style="padding:14px 16px; border-bottom:1px solid #f0f0f0;">
            <a href="{job['link']}" style="font-size:15px; font-weight:600;
               color:#0a66c2; text-decoration:none;">{job['title']}</a>
            <div style="font-size:13px; color:#444; margin-top:3px;">
              🏢 {job['company']} &nbsp;·&nbsp; 📍 {job['location']}
            </div>
            <div style="font-size:11px; margin-top:4px;">
              <span style="background:{source_color}18; color:{source_color};
                           padding:2px 7px; border-radius:99px; font-weight:600;">
                {source_label}
              </span>
              &nbsp;
              <span style="color:#999;">🕐 {job['posted']}</span>
            </div>
          </td>
        </tr>"""

    def section(title: str, jobs: list[dict], color: str) -> str:
        if not jobs:
            return ""
        rows = "".join(job_row(j) for j in jobs)
        return f"""
        <tr>
          <td style="padding:10px 16px 4px; background:#fafafa;
                     font-size:11px; font-weight:700; color:{color};
                     text-transform:uppercase; letter-spacing:0.08em;">
            {title} ({len(jobs)})
          </td>
        </tr>
        {rows}"""

    body = section("Direct company listings", direct_jobs, "#0d6e56") + \
           section("LinkedIn listings", linkedin_jobs, "#185FA5")

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0; padding:0; background:#f7f7f8;
                 font-family:'Segoe UI',Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:32px 16px;">
          <table width="640" cellpadding="0" cellspacing="0"
                 style="background:#fff; border-radius:12px;
                        box-shadow:0 2px 12px rgba(0,0,0,0.08); overflow:hidden;">

            <tr>
              <td style="background:linear-gradient(135deg,#0a66c2,#0d4f9e);
                          padding:28px 32px; color:#fff;">
                <div style="font-size:22px; font-weight:700;">
                  🇮🇪 Ireland Tech Jobs Alert
                </div>
                <div style="font-size:14px; opacity:0.85; margin-top:4px;">
                  {len(new_jobs)} new job{"s" if len(new_jobs)!=1 else ""} found
                  &nbsp;·&nbsp; {date_str}
                  &nbsp;·&nbsp; {len(direct_jobs)} direct + {len(linkedin_jobs)} LinkedIn
                </div>
              </td>
            </tr>

            <tr><td>
              <table width="100%" cellpadding="0" cellspacing="0">
                {body}
              </table>
            </td></tr>

            <tr>
              <td style="padding:20px 32px; background:#fafafa;
                          border-top:1px solid #eee; font-size:12px; color:#aaa;">
                Direct listings = posted straight to company careers page — apply immediately! 🚀<br>
                Sent by your Ireland Tech Job Tracker · apply on Day 1 = higher visibility
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """


def send_email(new_jobs: list[dict]) -> None:
    sender    = os.environ["EMAIL_SENDER"]
    password  = os.environ["EMAIL_PASSWORD"]
    recipient = os.environ["EMAIL_RECIPIENT"]

    direct_count = sum(1 for j in new_jobs if "Direct" in j["source"])
    subject = (
        f"🇮🇪 {len(new_jobs)} New Ireland Tech Job{'s' if len(new_jobs)!=1 else ''} "
        f"({direct_count} direct from company) — Apply Now!"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Job Tracker <{sender}>"
    msg["To"]      = recipient
    msg.attach(MIMEText(build_email(new_jobs), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())

    print(f"  ✅ Email sent — {len(new_jobs)} jobs ({direct_count} direct).")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🔍 Ireland Tech Job Tracker v2 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    seen = load_seen()
    all_new: list[dict] = []

    # 1. Greenhouse companies
    print("\n📋 Checking Greenhouse company pages …")
    for name, slug in GREENHOUSE_COMPANIES.items():
        print(f"  {name} …")
        jobs = fetch_greenhouse(name, slug)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"    ✨ {len(new)} new job(s) found!")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(1)

    # 2. Lever companies
    print("\n📋 Checking Lever company pages …")
    for name, slug in LEVER_COMPANIES.items():
        print(f"  {name} …")
        jobs = fetch_lever(name, slug)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"    ✨ {len(new)} new job(s) found!")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(1)

    # 3. LinkedIn (covers Google, Meta, Microsoft, Apple, Amazon, etc.)
    print("\n🔗 Checking LinkedIn (covers big tech + all other companies) …")
    for keyword in LINKEDIN_QUERIES:
        print(f"  '{keyword}' …")
        jobs = fetch_linkedin(keyword)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"    ✨ {len(new)} new listing(s).")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(2)

    save_seen(seen)

    # Deduplicate by job ID (LinkedIn might surface same job via multiple keywords)
    seen_ids: set = set()
    deduped: list[dict] = []
    for job in all_new:
        if job["id"] not in seen_ids:
            seen_ids.add(job["id"])
            deduped.append(job)

    # Sort: direct listings first, then LinkedIn
    deduped.sort(key=lambda j: (0 if "Direct" in j["source"] else 1, j["company"]))

    print(f"\n📊 Summary: {len(deduped)} unique new jobs "
          f"({sum(1 for j in deduped if 'Direct' in j['source'])} direct, "
          f"{sum(1 for j in deduped if 'LinkedIn' in j['source'])} LinkedIn)")

    if deduped:
        print("📬 Sending email …")
        send_email(deduped)
    else:
        print("📭 No new jobs since last run. No email sent.")

    print("Done.\n")


if __name__ == "__main__":
    main()

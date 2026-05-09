"""
Ireland Tech Job Tracker v4
───────────────────────────────────────────────────────────────────────────
Sources:
  1. Greenhouse ATS  — curated list (83 companies) + auto-discovered companies
  2. Lever ATS       — curated list + auto-discovered companies
  3. Personio ATS    — auto-discovered companies using Personio
  4. LinkedIn        — keyword sweep (entry-level, Ireland, last 24h)
                       covers Google, Meta, Microsoft, Apple, Amazon etc.

Auto-discovery (discover_companies.py) runs weekly and finds new companies
from BuiltIn Dublin, Wellfound, Silicon Republic, IDA Ireland, IrishJobs —
then detects their ATS automatically.

Runs on GitHub Actions twice a day. Sends one email digest of all new jobs.
"""

import json
import os
import smtplib
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

JOBS_FILE       = Path("data/seen_jobs.json")
COMPANIES_FILE  = Path("data/discovered_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

IRELAND_KEYWORDS = {
    "ireland", "dublin", "cork", "galway", "limerick",
    "waterford", "remote", "ie", "leinster", "munster"
}

# ── Curated company lists (hand-verified, always reliable) ───────────────────

GREENHOUSE_COMPANIES = {
    "Cloudflare": "cloudflare", "Workhuman": "workhuman",
    "Intercom": "intercom", "HubSpot": "hubspot",
    "MongoDB": "mongodb", "Tines": "tines",
    "Teamwork": "teamwork", "Indeed": "indeed",
    "Arctic Wolf": "arcticwolf", "Shopify": "shopify",
    "Zendesk": "zendesk", "Twilio": "twilio",
    "Figma": "figma", "Notion": "notion",
    "Canva": "canva", "Asana": "asana",
    "Airtable": "airtable", "Webflow": "webflow",
    "Loom": "loom", "Miro": "miro",
    "Typeform": "typeform", "Personio": "personio",
    "Phorest": "phorest", "Fenergo": "fenergo",
    "Wayflyer": "wayflyer", "Datadog": "datadog",
    "HashiCorp": "hashicorp", "Grafana Labs": "grafanalabs",
    "PagerDuty": "pagerduty", "New Relic": "newrelic",
    "Snyk": "snyk", "Sysdig": "sysdig",
    "Recorded Future": "recordedfuture", "Rapid7": "rapid7",
    "Tenable": "tenable", "Lacework": "lacework",
    "Scale AI": "scaleai", "Weights & Biases": "wandb",
    "Hugging Face": "huggingface", "Cohere": "cohere",
    "Celonis": "celonis", "ICON plc": "iconplc",
    "Optum": "optum", "Vaultree": "vaultree",
    "Immedis": "immedis", "Version 1": "version1",
    "Learnosity": "learnosity", "Cubic Telecom": "cubictelecom",
    "Kitman Labs": "kitmanlabs", "ServiceNow": "servicenow",
    "Workiva": "workiva", "Veeva Systems": "veeva",
}

LEVER_COMPANIES = {
    "Stripe": "stripe", "Revolut": "revolut",
    "SAP": "sap", "Dropbox": "dropbox",
    "Reddit": "reddit", "Squarespace": "squarespace",
    "Netlify": "netlify", "Klarna": "klarna",
    "Plaid": "plaid", "Wise": "wise",
    "Brex": "brex", "Checkout.com": "checkout",
    "Deel": "deel", "Remote": "remote",
    "Lattice": "lattice", "Culture Amp": "cultureamp",
    "Contentful": "contentful", "Lokalise": "lokalise",
    "DigitalOcean": "digitalocean", "Fastly": "fastly",
    "Aiven": "aiven", "Workvivo": "workvivo",
    "Evervault": "evervault",
}

# LinkedIn keyword searches (covers companies with no public API)
LINKEDIN_QUERIES = [
    "software engineer", "data engineer", "data scientist",
    "machine learning engineer", "frontend developer",
    "backend developer", "fullstack developer",
    "devops engineer", "cloud engineer", "QA engineer",
    "site reliability engineer", "cybersecurity analyst",
    "solutions architect", "product manager technology",
    "NetApp Cork",  # specific target
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(JOBS_FILE, "w") as f:
        json.dump(list(seen), f)

def load_discovered_companies() -> dict:
    """Load auto-discovered companies from the discovery engine."""
    if COMPANIES_FILE.exists():
        with open(COMPANIES_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return {c["name"]: c for c in data}
            return data
    return {}

def is_ireland(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in IRELAND_KEYWORDS)

def is_entry_level(title: str) -> bool:
    title_l = title.lower()
    senior_signals = [
        "senior", "sr.", "staff", "principal", "director",
        "vp ", "vice president", "head of", "manager",
        "lead ", " lead,", "architect", "distinguished",
        "chief", "cto", "cso", "ceo"
    ]
    return not any(s in title_l for s in senior_signals)

# ── Source 1: Greenhouse ──────────────────────────────────────────────────────

def fetch_greenhouse(company_name: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
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
            "id":       f"gh_{slug}_{job['id']}",
            "title":    title,
            "company":  company_name,
            "location": location or "Ireland",
            "link":     job.get("absolute_url", f"https://boards.greenhouse.io/{slug}"),
            "posted":   "Today",
            "source":   "Direct (Greenhouse)",
        })
    return results

# ── Source 2: Lever ───────────────────────────────────────────────────────────

def fetch_lever(company_name: str, slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        jobs = r.json()
        if not isinstance(jobs, list):
            return []
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
            "id":       f"lv_{slug}_{job['id']}",
            "title":    title,
            "company":  company_name,
            "location": location or "Ireland",
            "link":     job.get("hostedUrl", f"https://jobs.lever.co/{slug}"),
            "posted":   "Today",
            "source":   "Direct (Lever)",
        })
    return results

# ── Source 3: Personio ────────────────────────────────────────────────────────

def fetch_personio(company_name: str, slug: str) -> list[dict]:
    url = f"https://{slug}.jobs.personio.de/xml?language=en"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"    [WARN] Personio {company_name}: {e}")
        return []

    results = []
    for position in root.findall(".//position"):
        try:
            title    = position.findtext("name", "")
            office   = position.findtext("office", "")
            job_id   = position.findtext("id", "")
            link     = position.findtext("recruitingCategory", "")

            if not is_ireland(office) and not is_ireland(title):
                continue
            if not is_entry_level(title):
                continue

            results.append({
                "id":       f"ps_{slug}_{job_id}",
                "title":    title,
                "company":  company_name,
                "location": office or "Ireland",
                "link":     f"https://{slug}.jobs.personio.de/job/{job_id}",
                "posted":   "Today",
                "source":   "Direct (Personio)",
            })
        except Exception:
            continue

    return results

# ── Source 4: LinkedIn ────────────────────────────────────────────────────────

def fetch_linkedin(keyword: str) -> list[dict]:
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
                "id":       f"li_{job_id}",
                "title":    title,
                "company":  company_el.get_text(strip=True) if company_el else "N/A",
                "location": loc_el.get_text(strip=True) if loc_el else "Ireland",
                "link":     link_el["href"].split("?")[0] if link_el else url,
                "posted":   posted_el["datetime"] if posted_el and posted_el.get("datetime") else "Recently",
                "source":   f"LinkedIn",
            })
        except Exception:
            continue

    return results

# ── Email builder ─────────────────────────────────────────────────────────────

def build_email(new_jobs: list[dict]) -> str:
    date_str = datetime.now().strftime("%d %B %Y")

    direct_jobs   = [j for j in new_jobs if "Direct" in j["source"]]
    linkedin_jobs = [j for j in new_jobs if "LinkedIn" in j["source"]]

    def job_row(job: dict) -> str:
        is_direct = "Direct" in job["source"]
        color = "#0d6e56" if is_direct else "#185FA5"
        badge = f"🏢 {job['source']}" if is_direct else "🔗 LinkedIn"
        return f"""
        <tr>
          <td style="padding:14px 16px; border-bottom:1px solid #f0f0f0;">
            <a href="{job['link']}" style="font-size:15px; font-weight:600;
               color:#0a66c2; text-decoration:none;">{job['title']}</a>
            <div style="font-size:13px; color:#444; margin-top:3px;">
              🏢 {job['company']} &nbsp;·&nbsp; 📍 {job['location']}
            </div>
            <div style="font-size:11px; margin-top:4px;">
              <span style="background:{color}18; color:{color};
                           padding:2px 7px; border-radius:99px; font-weight:600;">
                {badge}
              </span>
              <span style="color:#999; margin-left:6px;">🕐 {job['posted']}</span>
            </div>
          </td>
        </tr>"""

    def section(label: str, jobs: list, color: str) -> str:
        if not jobs:
            return ""
        rows = "".join(job_row(j) for j in jobs)
        return f"""
        <tr>
          <td style="padding:10px 16px 4px; background:#fafafa;
                     font-size:11px; font-weight:700; color:{color};
                     text-transform:uppercase; letter-spacing:0.08em;">
            {label} ({len(jobs)})
          </td>
        </tr>{rows}"""

    body = (
        section("⚡ Direct company listings — apply immediately!", direct_jobs, "#0d6e56") +
        section("🔗 LinkedIn listings", linkedin_jobs, "#185FA5")
    )

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f7f7f8;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:32px 16px;">
<table width="640" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:12px;
              box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden;">
  <tr>
    <td style="background:linear-gradient(135deg,#0a66c2,#0d4f9e);
                padding:28px 32px;color:#fff;">
      <div style="font-size:22px;font-weight:700;">🇮🇪 Ireland Tech Jobs Alert</div>
      <div style="font-size:14px;opacity:.85;margin-top:4px;">
        {len(new_jobs)} new job{"s" if len(new_jobs)!=1 else ""} ·
        {date_str} ·
        {len(direct_jobs)} direct + {len(linkedin_jobs)} LinkedIn
      </div>
    </td>
  </tr>
  <tr><td>
    <table width="100%" cellpadding="0" cellspacing="0">{body}</table>
  </td></tr>
  <tr>
    <td style="padding:20px 32px;background:#fafafa;border-top:1px solid #eee;
                font-size:12px;color:#aaa;">
      Direct listings appear on company career pages before LinkedIn — apply first! 🚀<br>
      Ireland Tech Job Tracker v4 · auto-discovers new companies weekly
    </td>
  </tr>
</table>
</td></tr>
</table>
</body></html>"""

def send_email(new_jobs: list[dict]):
    sender    = os.environ["EMAIL_SENDER"]
    password  = os.environ["EMAIL_PASSWORD"]
    recipient = os.environ["EMAIL_RECIPIENT"]

    direct_count = sum(1 for j in new_jobs if "Direct" in j["source"])
    subject = (
        f"🇮🇪 {len(new_jobs)} New Ireland Tech Job{'s' if len(new_jobs)!=1 else ''} "
        f"({direct_count} direct) — Apply Now!"
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
    print(f"\n🔍 Ireland Tech Job Tracker v4 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    seen = load_seen()
    all_new: list[dict] = []

    # ── Load auto-discovered companies and merge with curated lists ──
    discovered = load_discovered_companies()
    
    # Split discovered companies by ATS
    extra_greenhouse = {
        c["name"]: c["slug"]
        for c in discovered.values()
        if c.get("ats") == "greenhouse" and c["name"] not in GREENHOUSE_COMPANIES
    }
    extra_lever = {
        c["name"]: c["slug"]
        for c in discovered.values()
        if c.get("ats") == "lever" and c["name"] not in LEVER_COMPANIES
    }
    extra_personio = {
        c["name"]: c["slug"]
        for c in discovered.values()
        if c.get("ats") == "personio"
    }

    all_greenhouse = {**GREENHOUSE_COMPANIES, **extra_greenhouse}
    all_lever      = {**LEVER_COMPANIES, **extra_lever}

    print(f"\n📋 Company coverage:")
    print(f"   Greenhouse : {len(all_greenhouse)} companies")
    print(f"   Lever      : {len(all_lever)} companies")
    print(f"   Personio   : {len(extra_personio)} companies")
    print(f"   LinkedIn   : {len(LINKEDIN_QUERIES)} keyword searches")

    # ── 1. Greenhouse ──
    print(f"\n🟢 Checking Greenhouse ({len(all_greenhouse)} companies)…")
    for name, slug in all_greenhouse.items():
        jobs = fetch_greenhouse(name, slug)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"   ✨ {name}: {len(new)} new")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(0.8)

    # ── 2. Lever ──
    print(f"\n🟡 Checking Lever ({len(all_lever)} companies)…")
    for name, slug in all_lever.items():
        jobs = fetch_lever(name, slug)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"   ✨ {name}: {len(new)} new")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(0.8)

    # ── 3. Personio ──
    if extra_personio:
        print(f"\n🟠 Checking Personio ({len(extra_personio)} companies)…")
        for name, info in extra_personio.items():
            jobs = fetch_personio(name, info["slug"])
            new  = [j for j in jobs if j["id"] not in seen]
            if new:
                print(f"   ✨ {name}: {len(new)} new")
            for j in new:
                seen.add(j["id"])
                all_new.append(j)
            time.sleep(0.8)

    # ── 4. LinkedIn ──
    print(f"\n🔵 LinkedIn keyword sweep ({len(LINKEDIN_QUERIES)} searches)…")
    for keyword in LINKEDIN_QUERIES:
        jobs = fetch_linkedin(keyword)
        new  = [j for j in jobs if j["id"] not in seen]
        if new:
            print(f"   ✨ '{keyword}': {len(new)} new")
        for j in new:
            seen.add(j["id"])
            all_new.append(j)
        time.sleep(2)

    save_seen(seen)

    # Deduplicate & sort (direct first)
    seen_ids: set = set()
    deduped: list[dict] = []
    for job in all_new:
        if job["id"] not in seen_ids:
            seen_ids.add(job["id"])
            deduped.append(job)
    deduped.sort(key=lambda j: (0 if "Direct" in j["source"] else 1, j["company"]))

    direct_n   = sum(1 for j in deduped if "Direct" in j["source"])
    linkedin_n = sum(1 for j in deduped if "LinkedIn" in j["source"])
    print(f"\n📊 {len(deduped)} unique new jobs — {direct_n} direct, {linkedin_n} LinkedIn")

    if deduped:
        send_email(deduped)
    else:
        print("📭 No new jobs. No email sent.")

    print("Done.\n")

if __name__ == "__main__":
    main()

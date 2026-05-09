"""
Ireland Tech Company Auto-Discovery Engine
───────────────────────────────────────────────────────────────────────────
Discovers tech companies operating in Ireland from multiple sources:
  1. BuiltIn Dublin  — curated list of tech companies in Ireland
  2. Wellfound       — startup/tech company directory filtered to Ireland
  3. Silicon Republic — Ireland tech news company mentions
  4. IDA Ireland     — foreign direct investment companies list

For each discovered company, auto-detects their ATS:
  → Greenhouse (boards-api.greenhouse.io)
  → Lever       (api.lever.co)
  → Personio    ({company}.jobs.personio.de)
  → Unknown     (falls back to LinkedIn)

Saves results to data/discovered_companies.json
Run this separately (or weekly) to keep the company list fresh.
"""

import json
import time
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

DISCOVERED_FILE = Path("data/discovered_companies.json")

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_discovered() -> dict:
    if DISCOVERED_FILE.exists():
        with open(DISCOVERED_FILE) as f:
            data = json.load(f)
            # Support both list (old) and dict (new) format
            if isinstance(data, list):
                return {c["name"]: c for c in data}
            return data
    return {}

def save_discovered(companies: dict):
    DISCOVERED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DISCOVERED_FILE, "w") as f:
        json.dump(companies, f, indent=2)
    print(f"  💾 Saved {len(companies)} companies to {DISCOVERED_FILE}")

def slugify(name: str) -> str:
    """Convert company name to likely ATS slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]", "", slug)
    return slug

def detect_ats(company_name: str, website: str = "") -> dict:
    """
    Try to detect which ATS a company uses by probing known endpoints.
    Returns dict with 'ats' and 'slug' keys.
    """
    slug = slugify(company_name)
    
    # Common slug variations to try
    slugs_to_try = [slug]
    # Add hyphenated version
    hyphen_slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    if hyphen_slug != slug:
        slugs_to_try.append(hyphen_slug)
    
    for s in slugs_to_try:
        # Try Greenhouse
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
                headers=HEADERS, timeout=8
            )
            if r.status_code == 200 and "jobs" in r.json():
                return {"ats": "greenhouse", "slug": s}
        except Exception:
            pass
        
        time.sleep(0.3)
        
        # Try Lever
        try:
            r = requests.get(
                f"https://api.lever.co/v0/postings/{s}?mode=json",
                headers=HEADERS, timeout=8
            )
            if r.status_code == 200 and isinstance(r.json(), list):
                return {"ats": "lever", "slug": s}
        except Exception:
            pass

        time.sleep(0.3)

        # Try Personio
        try:
            r = requests.get(
                f"https://{s}.jobs.personio.de/xml",
                headers=HEADERS, timeout=8
            )
            if r.status_code == 200 and "xml" in r.headers.get("content-type", "").lower():
                return {"ats": "personio", "slug": s}
        except Exception:
            pass

        time.sleep(0.3)

    return {"ats": "unknown", "slug": slug}


# ── Source 1: BuiltIn Dublin ──────────────────────────────────────────────────

def scrape_builtin_dublin() -> list[dict]:
    """Scrape BuiltIn Dublin for tech companies in Ireland."""
    companies = []
    urls = [
        "https://builtindublin.ie/companies",
        "https://builtin.com/articles/tech-companies-in-ireland",
    ]
    
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            
            # BuiltIn company cards
            for card in soup.find_all(["h2", "h3", "a"], class_=re.compile(r"company|employer|org", re.I)):
                name = card.get_text(strip=True)
                if name and 3 < len(name) < 60:
                    companies.append({"name": name, "source": "builtin"})
            
            time.sleep(2)
        except Exception as e:
            print(f"  [WARN] BuiltIn: {e}")
    
    return companies


# ── Source 2: Wellfound (AngelList) ──────────────────────────────────────────

def scrape_wellfound() -> list[dict]:
    """Scrape Wellfound for Ireland tech startups."""
    companies = []
    try:
        r = requests.get(
            "https://wellfound.com/startups/location/ireland",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Wellfound company names
        for el in soup.find_all(["h2", "h3", "span"], class_=re.compile(r"name|title|company", re.I)):
            name = el.get_text(strip=True)
            if name and 3 < len(name) < 60:
                companies.append({"name": name, "source": "wellfound"})
        
    except Exception as e:
        print(f"  [WARN] Wellfound: {e}")
    
    return companies


# ── Source 3: Silicon Republic company mentions ───────────────────────────────

def scrape_silicon_republic() -> list[dict]:
    """Scrape Silicon Republic jobs section for company names."""
    companies = []
    try:
        r = requests.get(
            "https://www.siliconrepublic.com/jobs",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Look for company names in job listings
        for el in soup.find_all(["span", "a", "p"], class_=re.compile(r"company|employer|recruiter", re.I)):
            name = el.get_text(strip=True)
            if name and 3 < len(name) < 60:
                companies.append({"name": name, "source": "silicon_republic"})
        
    except Exception as e:
        print(f"  [WARN] Silicon Republic: {e}")
    
    return companies


# ── Source 4: IDA Ireland company listing ─────────────────────────────────────

def scrape_ida_ireland() -> list[dict]:
    """
    Scrape IDA Ireland company listing.
    IDA has 1700+ foreign companies — this is the mother lode.
    """
    companies = []
    
    # Try the IDA company search API (they use an internal search)
    try:
        # IDA has a JSON API for their company search
        r = requests.get(
            "https://www.idaireland.com/doing-business-here/company-listing",
            headers={**HEADERS, "Accept": "application/json, text/html"},
            timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Parse company names from the listing page
        for el in soup.find_all(["h2", "h3", "h4", "a", "span"],
                                 class_=re.compile(r"company|name|title|listing", re.I)):
            name = el.get_text(strip=True)
            if name and 3 < len(name) < 80:
                companies.append({"name": name, "source": "ida_ireland"})

    except Exception as e:
        print(f"  [WARN] IDA Ireland: {e}")

    # Also try the GeoHive open dataset (IDA companies at local authority level)
    try:
        r = requests.get(
            "https://rdm.geohive.ie/datasets/e2cb07545b20476a98758455e558e8b7_0.geojson",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                name = props.get("Company_Name") or props.get("NAME") or props.get("company")
                if name and 3 < len(name) < 80:
                    companies.append({"name": name, "source": "ida_geohive"})
    except Exception as e:
        print(f"  [WARN] GeoHive IDA: {e}")

    return companies


# ── Source 5: Jobs.ie / IrishJobs ─────────────────────────────────────────────

def scrape_irish_job_boards() -> list[dict]:
    """Scrape Irish job boards for tech company names."""
    companies = []
    
    tech_roles = ["software+engineer", "data+engineer", "devops", "cloud+engineer"]
    
    for role in tech_roles[:2]:  # limit to avoid rate limits
        try:
            r = requests.get(
                f"https://www.irishjobs.ie/ShowResults.aspx?Keywords={role}&SortType=1",
                headers=HEADERS, timeout=15
            )
            soup = BeautifulSoup(r.text, "html.parser")
            
            for el in soup.find_all(class_=re.compile(r"company|employer|recruiter", re.I)):
                name = el.get_text(strip=True)
                if name and 3 < len(name) < 60 and not name.startswith("http"):
                    companies.append({"name": name, "source": "irishjobs"})
            
            time.sleep(2)
        except Exception as e:
            print(f"  [WARN] IrishJobs ({role}): {e}")
    
    return companies


# ── Dedup & Clean ─────────────────────────────────────────────────────────────

NOISE_WORDS = {
    "jobs", "careers", "hiring", "now", "apply", "view", "all", "see", "more",
    "learn", "about", "us", "company", "team", "culture", "benefits", "location",
    "remote", "hybrid", "full", "time", "part", "open", "positions", "roles",
    "engineering", "design", "product", "sales", "marketing", "finance", "legal",
    "ireland", "dublin", "cork", "galway", "limerick", "", "n/a", "tbd",
}

def clean_company_name(name: str) -> str | None:
    name = name.strip()
    # Remove common suffixes
    name = re.sub(r"\s+(Ltd|Limited|Inc|Corp|LLC|GmbH|plc|PLC|DAC)\.?$", "", name, flags=re.I)
    name = name.strip()
    
    if len(name) < 2 or len(name) > 60:
        return None
    if name.lower() in NOISE_WORDS:
        return None
    if re.match(r"^[\d\s\W]+$", name):
        return None
    
    return name


# ── Main discovery runner ─────────────────────────────────────────────────────

def discover_companies(detect_ats_for_new: bool = True):
    """
    Run all discovery sources, detect ATS for new companies, save results.
    """
    print(f"\n🔍 Ireland Tech Company Discovery — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    existing = load_discovered()
    print(f"  📂 Loaded {len(existing)} existing companies")

    # Gather from all sources
    raw = []
    print("\n📡 Scraping sources...")

    print("  → BuiltIn Dublin...")
    raw += scrape_builtin_dublin()

    print("  → Wellfound...")
    raw += scrape_wellfound()

    print("  → Silicon Republic...")
    raw += scrape_silicon_republic()

    print("  → IDA Ireland...")
    raw += scrape_ida_ireland()

    print("  → IrishJobs...")
    raw += scrape_irish_job_boards()

    # Clean and deduplicate by name
    seen_names = set(existing.keys())
    new_companies = []

    for entry in raw:
        name = clean_company_name(entry["name"])
        if not name:
            continue
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)
        new_companies.append({"name": name, "source": entry["source"]})

    print(f"\n✨ Found {len(new_companies)} new companies to investigate")

    # Detect ATS for each new company
    if detect_ats_for_new and new_companies:
        print(f"\n🔎 Detecting ATS for new companies (this takes a while)...")
        for i, company in enumerate(new_companies, 1):
            name = company["name"]
            print(f"  [{i}/{len(new_companies)}] {name}...", end=" ", flush=True)
            ats_info = detect_ats(name)
            company.update(ats_info)
            existing[name.lower()] = company
            print(f"→ {ats_info['ats']} ({ats_info['slug']})")
            time.sleep(0.5)
    else:
        for company in new_companies:
            company["ats"] = "unknown"
            company["slug"] = slugify(company["name"])
            existing[company["name"].lower()] = company

    save_discovered(existing)

    # Summary
    ats_counts = {}
    for c in existing.values():
        ats = c.get("ats", "unknown")
        ats_counts[ats] = ats_counts.get(ats, 0) + 1

    print(f"\n📊 ATS breakdown across {len(existing)} companies:")
    for ats, count in sorted(ats_counts.items(), key=lambda x: -x[1]):
        print(f"   {ats}: {count}")

    return existing


if __name__ == "__main__":
    discover_companies(detect_ats_for_new=True)

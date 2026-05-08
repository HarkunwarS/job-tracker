# 🇮🇪 Ireland Tech Job Tracker v2

Monitors **30 top tech companies** in Ireland across three sources and sends you a single daily email digest — runs **free on GitHub Actions**.

---

## How it works

| Source | Companies covered | Method |
|--------|-------------------|--------|
| **Greenhouse ATS** (direct) | Cloudflare, Workhuman, Intercom, HubSpot, MongoDB, Tines, Teamwork, Indeed, Arctic Wolf, Shopify | Free public JSON API — no auth needed |
| **Lever ATS** (direct) | Stripe, Revolut, SAP | Free public JSON API — no auth needed |
| **LinkedIn** (broad sweep) | Google, Meta, Microsoft, Apple, Amazon/AWS, IBM, Oracle, Salesforce, Workday, Accenture, Deloitte, PwC, TCS, PayPal, Mastercard, JP Morgan, Qualcomm + all others | Public job search filtered to Ireland, last 24h, entry level |

**Direct listings (Greenhouse/Lever) are especially valuable** — they appear there before LinkedIn, sometimes by 24–48 hours.

---

## 📁 Project Structure

```
job-tracker-v2/
├── scraper.py                     # Main scraper
├── requirements.txt
├── data/
│   └── seen_jobs.json             # Auto-updated list of seen job IDs
└── .github/
    └── workflows/
        └── job_tracker.yml
```

---

## 🚀 Setup (same as v1, ~10 minutes)

### Step 1 — Create a GitHub repo & upload files
Go to [github.com](https://github.com) → New repo → drag and drop all files.

### Step 2 — Gmail App Password
- Google Account → Security → App Passwords
- Create one for "Mail / Other (Job Tracker)"
- Copy the 16-character password

### Step 3 — Add 3 GitHub Secrets
Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|--------|-------|
| `EMAIL_SENDER` | your Gmail (e.g. you@gmail.com) |
| `EMAIL_PASSWORD` | 16-char App Password from Step 2 |
| `EMAIL_RECIPIENT` | where alerts go (can be same Gmail) |

### Step 4 — Enable write permissions
Settings → Actions → General → Workflow permissions → **Read and write** → Save

### Step 5 — Test it
Actions tab → "Ireland Tech Job Tracker v2" → Run workflow → check your inbox!

---

## ⏰ Schedule
- **7:00 AM IST** — morning sweep
- **1:00 PM IST** — afternoon sweep

---

## ➕ Adding more companies

**If the company uses Greenhouse** (check: `https://boards.greenhouse.io/COMPANYNAME`):
```python
GREENHOUSE_COMPANIES = {
    ...
    "New Company": "newcompanyslug",  # add here
}
```

**If the company uses Lever** (check: `https://jobs.lever.co/COMPANYNAME`):
```python
LEVER_COMPANIES = {
    ...
    "New Company": "newcompanyslug",  # add here
}
```

**If neither**, it'll be picked up via LinkedIn automatically.

---

## 📧 What the email looks like

- Direct listings (Greenhouse/Lever) appear first with a green "Direct" badge
- LinkedIn listings appear below with a blue "LinkedIn" badge
- Each card shows: job title, company, location, source, posted date, and apply link
- No email is sent if there are zero new jobs (no spam!)

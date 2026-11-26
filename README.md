# Wealth Management Engine (WME)

**[Live Demo](https://wealth-management-engine.onrender.com)** *(hosted on Render's free tier; initial load can take up to a minute)*







![WME multi-bank CSV demo](https://github.com/user-attachments/assets/3f2520c9-f59c-4423-aa89-d6d6f3c0304c)






## Overview

**Identified use case:** Consolidating fragmented multi-account and multi-institution bank statements.

WME automates the cleansing, validation, and deduplication of messy CSV schemas entirely in memory, giving users a complete, private view of their net cash flow and liquid balances without storing financial data or requiring banking credentials.

## What can it do?

### Multi-Institution Support (TD, Wealthsimple, Scotiabank)
Maps each bank's CSV layout (header or no header, different column orders, and four date formats) into one normalized schema. Internal transfers are excluded from income and spending using TD's `TFR-FR` / `TFR-TO` descriptions and Wealthsimple's `EFT` / `EFTOUT` transaction types. Transfers to Wealthsimple are tracked separately as "Sent to Wealthsimple."

### Overlap-Safe Deduplication
Overlapping exports (for example, an October CSV and a Sept-Nov CSV from the same account) need to be deduplicated without erasing legitimate same-day repeats, like buying a $3.50 Starbucks coffee at 9 AM and another at 2 PM on the same day where the CSV rows look identical.

WME handles this in three ways:
1. **Running-balance keys (when available):** Including the running account balance in each transaction's fingerprint keeps identical same-day purchases distinct because the balance shifts after the first coffee.
2. **Per-file `Counter` logic:** For each uploaded file, WME tallies transaction frequencies and keeps the **maximum count seen in any single file**, rather than summing across files or collapsing duplicates to one.
3. **Account grouping (union-find):** Files that share overlapping transactions are grouped as the same logical account, so overlapping exports with different filenames produce one ending balance instead of two.

### Privacy-First & Exact Validation
All monetary amounts are parsed using `decimal.Decimal` to avoid floating-point rounding drift, and unreadable or oversized values are rejected on upload. Files are processed strictly in memory per request, with no database, no disk persistence, and no third-party bank logins.

## User-Backed Adjustments

WME is currently in ongoing user testing (5 early testers to date) against real personal bank exports. Tester feedback directly drove **Scotiabank** schema support and ongoing institution expansion, paired with automated test coverage for malformed-CSV edge cases.

## Limitations

- Tested on TD, Wealthsimple, and Scotiabank exports only.
- TD labels all Wealthsimple transfers as `WS Investments`, so "Sent to Wealthsimple" may include funds moved into a Wealthsimple spending account.

## Tech Stack

- **Backend & ETL:** Python, FastAPI, `csv`, `decimal.Decimal`, `collections.Counter`
- **Frontend:** f-string HTML
- **Testing:** `pytest` + FastAPI `TestClient` (`tests/test_upload.py`)
- **Deployment:** Render

## Running Locally

```bash
# Clone the repo
git clone https://github.com/ParkerWtnb/wealth-management-engine.git
cd wealth-management-engine

# Install dependencies
pip install -r requirements.txt

# Start the local server
uvicorn main:app --app-dir src --reload
```

### Run the Test Suite

```bash
pip install pytest httpx
python -m pytest tests -q
```

import csv
import html
import io
import itertools
import logging
import math
import os
from collections import Counter
from datetime import datetime

from fastapi import FastAPI, File, UploadFile # Framework for building APIs
from fastapi.responses import HTMLResponse # Allows backend to send HTML responses to frontend
import uvicorn # Server for running FastAPI applications

logger = logging.getLogger("wealth_engine") # Never log row contents or filenames: statements are private

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory of this script
SAMPLE_FILES = ("td_sept_chequing.csv", "td_sept_savings.csv", "ws_sept_spend_card.csv")
MAX_FILE_BYTES = 2 * 1024 * 1024 # 2 MB per uploaded CSV
DATE_FORMATS = ("%Y-%m-%d", "%m-%d-%Y")
TRANSFER_MARKERS = ("TFR-FR", "TFR-TO", "EFTOUT") # Substrings in a description that mean an internal transfer
TRANSFER_TYPES = {"EFT", "EFTOUT"} # Wealthsimple transaction types for moves to/from a linked bank account

# Schema for headerless TD exports: date, description, withdrawal, deposit, balance
TD_COLUMNS = {'date': 0, 'transaction_desc': 1, 'withdrawal': 2, 'deposit': 3, 'balance': 4}
# Columns a file with a header row (e.g. Wealthsimple) must have, with user-facing names
REQUIRED_HEADER_COLUMNS = {"date": "date", "transaction_desc": "description", "amount": "amount", "balance": "balance"}


class StatementFormatError(Exception):
    """A file that doesn't look like a supported bank statement.
    Messages may name the file and row number but must never include cell values."""


def _parse_date(cell):
    # Handle different date formats (YYYY-MM-DD and MM-DD-YYYY)
    cell = cell.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cell, fmt)
        except ValueError:
            pass
    raise ValueError("unrecognized date") # Deliberately no cell value in the message


def _parse_amount(cell):
    cell = cell.strip().replace(',', '')
    if not cell:
        return 0.0
    value = float(cell) # May raise ValueError; callers re-raise without the original message
    if not math.isfinite(value): # float() happily accepts "nan" and "inf"
        raise ValueError("non-finite amount")
    return value


def _is_date(cell):
    try:
        _parse_date(cell)
        return True
    except ValueError:
        return False


# Transaction class to hold transaction data and transform CSV data.
class Transaction:
    def __init__(self, date, transaction_desc, withdrawal, deposit, balance, account_type):
        self.date = _parse_date(date)
        self.transaction_desc = transaction_desc.strip()
        self.withdrawal = _parse_amount(withdrawal)
        self.deposit = _parse_amount(deposit)
        self.balance = _parse_amount(balance)
        self.account = account_type # Source account (from the filename); never changes
        self.account_type = account_type # Classification; may become internal_transfer / ws_investments


def _map_header(header):
    """Map a header row to column indexes, or raise StatementFormatError if required columns are missing."""
    column_map = {}
    for i, cell in enumerate(header):
        cell = cell.strip().lower()
        if "date" in cell:
            column_map["date"] = i
        elif "description" in cell:
            column_map["transaction_desc"] = i
        elif "transaction" in cell: # e.g. Wealthsimple's "transaction" column holds EFT / EFTOUT / SPEND
            column_map["transaction_type"] = i
        elif "amount" in cell:
            column_map["amount"] = i
        elif "balance" in cell:
            column_map["balance"] = i

    if "transaction_desc" not in column_map and "transaction_type" in column_map:
        column_map["transaction_desc"] = column_map["transaction_type"] # Only a "transaction" column: use it as the description

    missing = [label for key, label in REQUIRED_HEADER_COLUMNS.items() if key not in column_map]
    if missing:
        raise StatementFormatError(f"the header row is missing these columns: {', '.join(missing)}")
    return column_map


def parse_csv(filename, text):
    """Parse one CSV statement into a list of Transactions. Uses only local state.
    Raises StatementFormatError (with a value-free message) if the file isn't a supported statement."""
    try:
        return _parse_rows(filename, text)
    except StatementFormatError as exc:
        raise StatementFormatError(f"{os.path.basename(filename)}: {exc}") from None
    except csv.Error:
        raise StatementFormatError(f"{os.path.basename(filename)}: this isn't a readable CSV file") from None


def _parse_rows(filename, text):
    account = os.path.splitext(os.path.basename(filename))[0]
    reader = csv.reader(io.StringIO(text))

    first_row = next(reader, None) # Check top row for header or not (TD CSV format has no header)
    while first_row is not None and not any(cell.strip() for cell in first_row):
        first_row = next(reader, None) # Skip leading blank lines
    if first_row is None:
        raise StatementFormatError("the file is empty")

    if _is_date(first_row[0]): # Assume TD CSV format (no header row)
        column_map = TD_COLUMNS
        rows = itertools.chain([first_row], reader) # First row is data, so process it like every other row
    else: # If the first row is a header, map columns based on name.
        column_map = _map_header(first_row)
        rows = reader
    min_width = max(column_map.values()) + 1

    transactions = []
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue # Skip blank lines
        line = reader.line_num
        if len(row) < min_width:
            raise StatementFormatError(f"row {line} has {len(row)} columns but at least {min_width} are expected")

        withdraw_amt = ""
        deposit_amt = ""
        if "amount" in column_map:
            amount = row[column_map["amount"]].strip()
            if amount.startswith('-'):
                withdraw_amt = amount[1:]  # Remove the negative
            else:
                deposit_amt = amount
        else:
            withdraw_amt = row[column_map["withdrawal"]]
            deposit_amt = row[column_map["deposit"]]

        # Instantiation; `from None` drops the ValueError, whose message would contain the raw cell value
        try:
            transaction = Transaction(
                row[column_map['date']],
                row[column_map['transaction_desc']],
                withdraw_amt,
                deposit_amt,
                row[column_map['balance']],
                account
            )
        except ValueError:
            raise StatementFormatError(f"row {line} has a date or amount that couldn't be read") from None

        # Classify using the description plus the transaction-type column when the file has one
        desc_upper = transaction.transaction_desc.upper()
        txn_type = row[column_map["transaction_type"]].strip().upper() if "transaction_type" in column_map else ""

        if txn_type in TRANSFER_TYPES or any(marker in desc_upper for marker in TRANSFER_MARKERS):
            transaction.account_type = "internal_transfer"
        elif "WS INVESTMENTS" in desc_upper:
            transaction.account_type = "ws_investments"
        transactions.append(transaction)

    if not transactions:
        raise StatementFormatError("no transactions were found")
    return transactions


class FinancialTotals:
    def __init__(self):
        self.total_income = 0.0
        self.total_spending = 0.0
        self.total_ws_investments = 0.0
        self.latest_balances = {}

    @property
    def liquid_cash(self):
        return sum(self.latest_balances.values()) # Total liquid cash across all accounts

    def transaction_processor(self, transactions):
        """transactions must already be sorted oldest -> newest."""
        for transac in transactions:
            # Every row carries the account's running balance, transfers included, so the last one seen is the ending balance
            self.latest_balances[transac.account] = transac.balance

            if transac.account_type == "ws_investments": # Track investments but do not count them as income or spending.
                self.total_ws_investments += transac.withdrawal #Money isn't leaving TFSA ever.

            elif transac.account_type != "internal_transfer": #Internal transfers aren't income or spending.
                self.total_income += transac.deposit
                self.total_spending += transac.withdrawal


def _dedup_key(t):
    """Identity of a transaction across overlapping exports. The running balance keeps same-day
    identical purchases distinct; the filename is deliberately not part of the key."""
    description = " ".join(t.transaction_desc.split()).upper() # Ignore spacing/case differences between exports
    amount = round(t.deposit - t.withdrawal, 2)
    return (t.date, description, amount, round(t.balance, 2))


def deduplicate(parsed_files):
    """parsed_files: list of transaction lists, one per file.
    A key that appears n times in one file is a real repeat, so keep n copies; across files keep the
    max count, taken from the single file that had the most, so overlapping exports count once."""
    best_count = Counter() # key -> highest count seen in any one file
    owner = {} # key -> index of the file whose copies we keep
    first_file = {} # key -> first file it appeared in, used to spot overlapping exports
    group = list(range(len(parsed_files))) # Union-find: files that share a transaction are the same account

    def find(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i, transactions in enumerate(parsed_files):
        for key, n in Counter(map(_dedup_key, transactions)).items():
            if n > best_count[key]:
                best_count[key] = n
                owner[key] = i
            j = first_file.setdefault(key, i)
            if j != i:
                group[find(i)] = find(j) # Overlap => same account, even if the filenames differ

    kept = []
    for i, transactions in enumerate(parsed_files):
        root = find(i)
        canonical = parsed_files[root][0].account if parsed_files[root] else None
        for t in transactions:
            if owner[_dedup_key(t)] == i:
                t.account = canonical or t.account # One ending balance per real account, not per file
                kept.append(t)
    return kept


def build_report(files):
    """files: iterable of (filename, csv_text). Everything lives in this call; nothing is shared between requests."""
    parsed_files = [parse_csv(filename, text) for filename, text in files]
    transactions = deduplicate(parsed_files)

    # Timsort is stable, so same-day rows keep their file order and the last balance of the day wins
    transactions.sort(key=lambda t: t.date)

    totals = FinancialTotals()
    totals.transaction_processor(transactions)
    return totals


# Frontend:

app = FastAPI() # Initialize FastAPI app

UPLOAD_FORM = """
            <form action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="files" accept=".csv" multiple required>
                <button type="submit">Upload statements</button>
            </form>
            <form action="/sample" method="post">
                <button type="submit">Try with sample data</button>
            </form>
"""


def render_dashboard(totals=None, error=None):
    if error is not None:
        results = f"""
            <div style="background-color: #fdecea; color: #611a15; padding: 15px; border-radius: 8px; max-width: 480px;">
                <p><strong>That doesn't look like a bank statement we can read.</strong></p>
                <p>{html.escape(error)}</p>
                <p>Please upload the CSV export from TD or Wealthsimple without editing it.</p>
            </div>"""
    elif totals is None:
        results = "<p>Upload one or more CSV statements to see your totals.</p>"
    else:
        results = f"""
            <div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px; width: 300px;">
                <p><strong>Total Income:</strong> ${totals.total_income:,.2f}</p>
                <p><strong>Total Spending:</strong> ${totals.total_spending:,.2f}</p>
                <p><strong>Total Invested (WS):</strong> ${totals.total_ws_investments:,.2f}</p>
                <hr>
                <p><strong>Liquid Cash:</strong> ${totals.liquid_cash:,.2f}</p>
            </div>"""

    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>Wealth Management Engine</h2>
            {UPLOAD_FORM}
            {results}
        </body>
    </html>
    """


# Visual dashboard route: empty state with the upload form
@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    return render_dashboard()


# Upload route: parse the uploaded CSVs for this request only and return the dashboard
@app.post("/upload", response_class=HTMLResponse)
async def upload_statements(files: list[UploadFile] = File(...)):
    decoded = []
    for upload in files:
        name = os.path.basename(upload.filename or "upload.csv")
        raw = await upload.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return _error_response(f"{name}: each file must be 2 MB or smaller", 413)
        try:
            decoded.append((name, raw.decode("utf-8-sig")))
        except UnicodeDecodeError:
            return _error_response(f"{name}: this isn't a UTF-8 text CSV file", 400)

    try:
        totals = build_report(decoded)
    except StatementFormatError as exc:
        return _error_response(str(exc), 400)
    except Exception as exc: # Unexpected bug: log only the exception type so no statement data reaches the logs
        logger.error("upload processing failed (%s)", type(exc).__name__)
        return _error_response("something went wrong while reading your files", 500)
    return render_dashboard(totals)


def _error_response(message, status_code):
    logger.info("upload rejected (HTTP %d)", status_code) # No filename or message: both can carry personal data
    return HTMLResponse(render_dashboard(error=message), status_code=status_code)


# Sample route: run the bundled anonymized CSVs through the same pipeline
@app.post("/sample", response_class=HTMLResponse)
def sample_statements():
    files = []
    for filename in SAMPLE_FILES:
        with open(os.path.join(SCRIPT_DIR, filename), encoding="utf-8") as f:
            files.append((filename, f.read()))
    return render_dashboard(build_report(files))


# Run the FastAPI server
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000) # Run the FastAPI server on localhost at port 8000

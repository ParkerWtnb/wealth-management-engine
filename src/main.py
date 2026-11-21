import csv
import io
import itertools
import os
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile # Framework for building APIs
from fastapi.responses import HTMLResponse # Allows backend to send HTML responses to frontend
import uvicorn # Server for running FastAPI applications

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory of this script
SAMPLE_FILES = ("td_sept_chequing.csv", "td_sept_savings.csv", "ws_sept_spend_card.csv")
MAX_FILE_BYTES = 2 * 1024 * 1024 # 2 MB per uploaded CSV
DATE_FORMATS = ("%Y-%m-%d", "%m-%d-%Y")
TRANSFER_MARKERS = ("TFR-FR", "TFR-TO", "EFTOUT") # Substrings in a description that mean an internal transfer
TRANSFER_TYPES = {"EFT", "EFTOUT"} # Wealthsimple transaction types for moves to/from a linked bank account

# Transaction class to hold transaction data and transform CSV data.
class Transaction:
    def __init__(self, date, transaction_desc, withdrawal, deposit, balance, account_type):

        date = date.strip()
        # Handle different date formats (YYYY-MM-DD and MM-DD-YYYY)
        try:
            self.date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            self.date = datetime.strptime(date, "%m-%d-%Y")

        self.transaction_desc = transaction_desc.strip()
        self.withdrawal = float(withdrawal.strip().replace(',','')) if withdrawal.strip() else 0.0
        self.deposit = float(deposit.strip().replace(',','')) if deposit.strip() else 0.0
        self.balance = float(balance.strip().replace(',','')) if balance.strip() else 0.0
        self.account = account_type # Source account (from the filename); never changes
        self.account_type = account_type # Classification; may become internal_transfer / ws_investments


def _is_date(cell):
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(cell.strip(), fmt)
            return True
        except ValueError:
            pass
    return False


def parse_csv(filename, text):
    """Parse one CSV statement into a list of Transactions. Uses only local state."""
    account = os.path.splitext(os.path.basename(filename))[0]
    reader = csv.reader(io.StringIO(text))

    first_row = next(reader, None) # Check top row for header or not (TD CSV format has no header)
    if not first_row:
        return []

    if _is_date(first_row[0]): # Assume TD CSV format (no header row)
        column_map = {'date': 0, 'transaction_desc': 1, 'withdrawal': 2, 'deposit': 3, 'balance': 4}
        rows = itertools.chain([first_row], reader) # First row is data, so process it like every other row
    else: # If the first row is a header, map columns based on name.
        column_map = {}
        for i, cell in enumerate(first_row):
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
        rows = reader

    transactions = []
    for row in rows:
        if not row or len(row) < 4:
            continue # Skip empty or insufficient rows
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

        # Instantiation
        transaction = Transaction(
            row[column_map['date']],
            row[column_map['transaction_desc']],
            withdraw_amt,
            deposit_amt,
            row[column_map['balance']],
            account
        )

        # Classify using the description plus the transaction-type column when the file has one
        desc_upper = transaction.transaction_desc.upper()
        txn_type = row[column_map["transaction_type"]].strip().upper() if "transaction_type" in column_map else ""

        if txn_type in TRANSFER_TYPES or any(marker in desc_upper for marker in TRANSFER_MARKERS):
            transaction.account_type = "internal_transfer"
        elif "WS INVESTMENTS" in desc_upper:
            transaction.account_type = "ws_investments"
        transactions.append(transaction)

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


def build_report(files):
    """files: iterable of (filename, csv_text). Everything lives in this call; nothing is shared between requests."""
    transactions = []
    for filename, text in files:
        transactions.extend(parse_csv(filename, text))

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


def render_dashboard(totals=None):
    if totals is None:
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
        raw = await upload.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Each file must be 2 MB or smaller.")
        try:
            decoded.append((upload.filename or "upload.csv", raw.decode("utf-8-sig")))
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Files must be UTF-8 CSV exports.") from None
    return render_dashboard(build_report(decoded))


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

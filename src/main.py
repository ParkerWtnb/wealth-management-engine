import csv
# import JSON
import os
from datetime import date, datetime
from platform import node

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
        self.account_type = account_type

# Node class for the BST
class Node:
    def __init__(self, transaction):
        self.transaction = transaction
        self.left = None
        self.right = None

# Binary Search Tree class to hold transactions in order (chronologically)
class BST:
    def __init__(self):
        self.root = None

    def insert(self, transaction):
        if self.root is None:
            self.root = Node(transaction)
        else:
            self._insert_recursive(self.root, transaction)
    # Recursive helper to insert based on transaction date
    def _insert_recursive(self, node, transaction):
        if transaction.date < node.transaction.date:
            if node.left is None:
                node.left = Node(transaction)
            elif node.left is not None:
                self._insert_recursive(node.left, transaction)
        else:
            if node.right is None:
                node.right = Node(transaction)
            else:
                self._insert_recursive(node.right, transaction)

bst = BST() # Initialize the BST to hold transactions
script_dir = os.path.dirname(os.path.abspath(__file__)) # Get directory of current script

# Read and process CSV files
csv_files = ["td_sept_chequing.csv", "td_sept_savings.csv", "ws_sept_spend_card.csv"]
for filename in csv_files:
    file_path = os.path.join(script_dir, filename)

    current_file = filename.replace(".csv", "")

    with open(file_path, mode = 'r', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)

        first_row = next(reader, None) # Check top row for header or not (TD CSV format has no header)
        if not first_row:
            continue # Skip empty
        column_map = {} # Map column names to dictionary keys for indexing
        is_header = False # Assume no header until proven otherwise
        try:
            datetime.strptime(first_row[0].strip(), "%Y-%m-%d")
        except ValueError: # Check multiple date formats 
            try:
                datetime.strptime(first_row[0].strip(), "%m-%d-%Y")
            except ValueError:
                is_header = True # If the first column isn't a date, assume it's a header row

        if is_header: # If the first row is a header, map columns based on name.
            i = 0
            for i in range(len(first_row)):
                cell = first_row[i].strip().lower()
                if "date" in cell:
                    column_map["date"] = i
                elif "transaction" in cell or "description" in cell:
                    column_map["transaction_desc"] = i
                elif "amount" in cell:
                    column_map["amount"] = i
                elif "balance" in cell:
                    column_map["balance"] = i
        else: # Assume TD CSV format (no header row)
            column_map = {'date': 0, 'transaction_desc': 1, 'withdrawal': 2, 'deposit': 3, 'balance': 4}
            # Immediately process the first row as a transaction given no header
            first_transaction = Transaction(
                first_row[column_map['date']],
                first_row[column_map['transaction_desc']],
                first_row[column_map['withdrawal']],
                first_row[column_map['deposit']],
                first_row[column_map['balance']],
                current_file
            )
            # Determine account type for the first transaction and insert into BST
            if not ("TFR-FR" in first_transaction.transaction_desc.upper() or "TFR-TO" in first_transaction.transaction_desc.upper()):
                if "ws investments" in first_transaction.transaction_desc.lower():
                    first_transaction.account_type = "ws_investments"
                bst.insert(first_transaction)

        # The rest of the file gets processed in the loop below.
        for row in reader:
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
                current_file
            )
            desc_upper = transaction.transaction_desc.upper()
            if "TFR-FR" in desc_upper or "TFR-TO" in desc_upper or "EFTOUT" in desc_upper:
                transaction.account_type = "internal_transfer"
                bst.insert(transaction)
            elif "ws investments" in transaction.transaction_desc.lower():
                transaction.account_type = "ws_investments"
                bst.insert(transaction)
            else:
                bst.insert(transaction)


class FinancialTotals:
    def __init__(self):
        self.total_income = 0.0
        self.total_spending = 0.0
        self.total_ws_investments = 0.0
        self.latest_balances = {}

    def transaction_processor(self, node):
        if node is not None:
            self.transaction_processor(node.left) #Left first (oldest transactions)
            transac = node.transaction
            if transac.account_type not in ["internal_transfer", "ws_investments"]: # Regular transactions
                self.latest_balances[transac.account_type] = transac.balance # Update the latest balance for the account type


            if transac.account_type == "ws_investments": # Track investments but do not count them as income or spending.
                self.total_ws_investments += transac.withdrawal #Money isn't leaving TFSA ever.

            elif transac.account_type != "internal_transfer": #TFR-FR and TFR-TO are internal transfers, so we don't count them as income or spending.
                self.total_income += transac.deposit
                self.total_spending += transac.withdrawal

            self.transaction_processor(node.right) #Right last (newer transactions)

processor = FinancialTotals()
processor.transaction_processor(bst.root)


# Testing (temporary pre frontend implementation)
def in_order_print(node):
    if node is not None:
        in_order_print(node.left)
        print(f"Date: {node.transaction.date}, Description: {node.transaction.transaction_desc}, Withdrawal: {node.transaction.withdrawal}, Deposit: {node.transaction.deposit}, Balance: {node.transaction.balance}, Account Type: {node.transaction.account_type}")
        in_order_print(node.right)

in_order_print(bst.root)


# Frontend:

from fastapi import FastAPI # Framework for building APIs
from fastapi.middleware.cors import CORSMiddleware # Allows frontend to acccess backend information
from fastapi.responses import HTMLResponse # Allows backend to send HTML responses to frontend
import uvicorn # Server for running FastAPI applications

app = FastAPI() # Initialize FastAPI app

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow any local frontend to request data from backend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Endpoint 1: JSON API route for financial metrics
@app.get("/api/metrics") # Tells app object to look for get requests.
def get_financial_metrics():
    liquid_cash = sum(processor.latest_balances.values()) # Calculate total liquid cash across all accounts

# Serialize dictionary to JSON format for frontend.

    return {
        "total_income": processor.total_income,
        "total_spending": processor.total_spending,
        "total_ws_investments": processor.total_ws_investments,
        "latest_balances": processor.latest_balances,
        "liquid_cash": liquid_cash
    }

# Endpoint 2: Visual dashboard route using python's built-in HTMLResponse
@app.get("/dashboard", response_class=HTMLResponse) # Route for the dashboard page
def get_dashboard():
    liquid_cash = sum(processor.latest_balances.values()) # Calculate total liquid cash across all accounts

    # HTML content for the dashboard page
    html_content = f"""
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>Wealth Management Engine</h2>
            
            <div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px; width: 300px;">
                <p><strong>Total Income:</strong> ${processor.total_income:,.2f}</p>
                <p><strong>Total Spending:</strong> ${processor.total_spending:,.2f}</p>
                <p><strong>Total Invested (WS):</strong> ${processor.total_ws_investments:,.2f}</p>
                <hr>
                <p><strong>Liquid Cash:</strong> ${liquid_cash:,.2f}</p>
            </div>
        </body>
    </html>
    """
    return html_content


    


# Run the FastAPI server
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000) # Run the FastAPI server on localhost at port 8000

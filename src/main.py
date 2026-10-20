import csv
# import JSON
import os
from datetime import date, datetime
from platform import node

# Transaction class to hold transaction data and transform CSV data.
class Transaction:
    def __init__(self, date, transaction_desc, withdrawal, deposit, balance, account_type):

        date = date.strip()
        try:
            self.date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            self.date = datetime.strptime(date, "%m-%d-%Y")


        self.transaction_desc = transaction_desc.strip()
        self.withdrawal = float(withdrawal.strip().replace(',','')) if withdrawal.strip() else 0.0
        self.deposit = float(deposit.strip().replace(',','')) if deposit.strip() else 0.0
        self.balance = float(balance.strip().replace(',','')) if balance.strip() else 0.0
        self.account_type = account_type

# Node class for the binary search tree
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

bst = BST()
script_dir = os.path.dirname(os.path.abspath(__file__))
# TODO: Implement csv.DictReader to handle misaligned columns across WS and TD schemas
csv_files = ["td_sept_chequing.csv", "td_sept_savings.csv", "ws_sept_spend_card.csv"]
for filename in csv_files:
    file_path = os.path.join(script_dir, filename)

    current_file = filename.replace(".csv", "")

    with open(file_path, mode = 'r', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)

        first_row = next(reader, None) #check top row
        if not first_row:
            continue # Skip empty
        column_map = {}
        is_header = False
        try:
            datetime.strptime(first_row[0].strip(), "%Y-%m-%d")
        except ValueError:
            try:
                datetime.strptime(first_row[0].strip(), "%m-%d-%Y")
            except ValueError:
                is_header = True

        if is_header:
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


            if transac.account_type == "ws_investments":
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
print(f"Total Income: {processor.total_income}")
print(f"Total Spending: {processor.total_spending}")
print(f"Total WS Investments: {processor.total_ws_investments}")
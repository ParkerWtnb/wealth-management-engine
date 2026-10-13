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

        first_row = next(reader) #check top row
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
            if row is  None or len(row) < 4:
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
            if "TFR-FR" in transaction.transaction_desc.upper() or "TFR-TO" in transaction.transaction_desc.upper():
                continue
            elif "ws investments" in transaction.transaction_desc.lower():
                transaction.account_type = "ws_investments"
                bst.insert(transaction)
            else:
                bst.insert(transaction)

# Testing
def in_order_print(node):
    if node is not None:
        in_order_print(node.left)
        print(f"Date: {node.transaction.date}, Description: {node.transaction.transaction_desc}, Withdrawal: {node.transaction.withdrawal}, Deposit: {node.transaction.deposit}, Balance: {node.transaction.balance}, Account Type: {node.transaction.account_type}")
        in_order_print(node.right)

in_order_print(bst.root)
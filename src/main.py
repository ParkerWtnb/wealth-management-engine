import csv
# import JSON
import os
from datetime import datetime
from platform import node
script_dir = os.path.dirname(os.path.abspath(__file__))
print(f"Python is looking in: {script_dir}")
print("Files found in this folder:", os.listdir(script_dir))
file_path = os.path.join(script_dir, 'savings_account.csv')
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

    
class Node:
    def __init__(self, transaction):
        self.transaction = transaction
        self.left = None
        self.right = None

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
with open(file_path, mode = 'r', encoding='utf-8') as csvfile:
    reader = csv.reader(csvfile)

    for row in reader:
        if row is not None:
            transaction = Transaction(row[0], row[1], row[2], row[3], row[4], "savings_account")
            if "TFR-FR" in transaction.transaction_desc.upper() or "TFR.TO" in transaction.transaction_desc.upper():
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
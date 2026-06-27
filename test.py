import sqlite3
from decimal import Decimal, InvalidOperation

db_path = "indexer_cache.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()

c.execute("SELECT COUNT(DISTINCT client) FROM JobCreated WHERE client = evaluator AND evaluator != '0x0000000000000000000000000000000000000000'")
print("unique_self_evaluators:", c.fetchone()[0])

c.execute("SELECT amount FROM PaymentReleased")
total_amount = Decimal(0)
for (amount_str,) in c.fetchall():
    if not amount_str:
        continue
    try:
        total_amount += Decimal(str(amount_str))
    except (InvalidOperation, ValueError, TypeError):
        continue
print("total_usdc_volume:", total_amount / Decimal("1000000"))

conn.close()

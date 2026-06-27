"""
query.py — simple SQL CLI wrapper for indexer_cache.db
Usage:
  python query.py "SELECT COUNT(*) FROM JobCreated"
  python query.py  # без аргументов — покажет список таблиц
No quoting hell. Works on Windows PowerShell, CMD, Linux, Mac.
"""

import os
import sqlite3
import sys


DB_PATH = os.environ.get("DB_PATH", "indexer_cache.db")


def run(sql: str, db_path: str = DB_PATH) -> None:
    if not os.path.exists(db_path):
        print(f"Error: database not found at '{db_path}'", file=sys.stderr)
        print("Run indexer.py first to build the database.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute(sql)
        rows = cursor.fetchall()

        if cursor.description:
            headers = [col[0] for col in cursor.description]
            col_widths = [
                max(len(h), max((len(str(r[i])) for r in rows), default=0))
                for i, h in enumerate(headers)
            ]
            fmt = " | ".join(f"{{:<{w}}}" for w in col_widths)
            print(fmt.format(*headers))
            print("-+-".join("-" * w for w in col_widths))
            for row in rows:
                print(fmt.format(*[str(x) for x in row]))
            print(f"\n{len(rows)} row(s)")
        else:
            print(f"OK — rows affected: {cursor.rowcount}")
    except sqlite3.OperationalError as e:
        print(f"SQL error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nAvailable tables:")
        if not os.path.exists(DB_PATH):
            print(f"Error: database not found at '{DB_PATH}'", file=sys.stderr)
            sys.exit(1)
        conn = sqlite3.connect(DB_PATH)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        conn.close()
        for (t,) in tables:
            print(f"  {t}")
        sys.exit(0)

    sql = " ".join(sys.argv[1:])
    run(sql)

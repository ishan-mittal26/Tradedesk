from core.local_db import get_connection

conn = get_connection()

count = conn.execute("SELECT COUNT(*) FROM support_resistance").fetchone()[0]
print("SR zones in DB:", count)

tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("All tables:", [t[0] for t in tables])
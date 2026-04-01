import sqlite3
import os

db = r'c:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge\data\history.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'h_%'")
tables = cur.fetchall()
print(f"分表数量: {len(tables)}")
for t in tables[:10]:
    name = t[0]
    cur2 = conn.cursor()
    cur2.execute(f"SELECT COUNT(*) FROM [{name}]")
    cnt = cur2.fetchone()[0]
    print(f"  {name}: {cnt} 条")

conn.close()
print(f"文件大小: {os.path.getsize(db)} bytes")

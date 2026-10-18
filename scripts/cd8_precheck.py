#!/usr/bin/env python3
"""CD-⑧ 事前只读检查：生产 knowledge 重复对现状（auto-dedup 安全前置）。"""
import json
import sqlite3
import sys
import urllib.request
import uuid

sys.path.insert(0, "/home/climbing/opensoul")
from src.services.auth import create_access_token  # noqa: E402

db = sqlite3.connect("/home/climbing/opensoul/data/opensoul.db")
row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
n = db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
db.close()
token = create_access_token(uuid.UUID(str(row[0])))
req = urllib.request.Request("http://127.0.0.1:8090/api/dedup/duplicates")
req.add_header("Authorization", f"Bearer {token}")
with urllib.request.urlopen(req, timeout=20) as r:
    d = json.loads(r.read().decode())
print("knowledge rows:", n)
print("total_pairs (pre-existing):", d["total_pairs"])
for p in d["duplicates"][:5]:
    print("  pair:", json.dumps(p, ensure_ascii=False)[:150])

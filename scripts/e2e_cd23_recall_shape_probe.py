#!/usr/bin/env python3
"""CD-㉓ live 探针：POST /api/agent/recall 真源响应形状对账（{answer,sources}、元素 {id,score}）。"""
import json
import sqlite3
import sys
import urllib.request
import uuid

sys.path.insert(0, "/home/climbing/opensoul")
from src.services.auth import create_access_token  # noqa: E402

db = sqlite3.connect("/home/climbing/opensoul/data/opensoul.db")
row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
db.close()
token = create_access_token(uuid.UUID(str(row[0])))
req = urllib.request.Request(
    "http://127.0.0.1:8090/api/agent/recall",
    method="POST",
    data=json.dumps({"question": "测试检索面板真源形状 CD23", "top_k": 3}).encode(),
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.loads(r.read().decode())
print("status 200, keys:", sorted(body.keys()))
print("sources is list:", isinstance(body.get("sources"), list), "len:", len(body.get("sources", [])))
for s in body.get("sources", [])[:3]:
    print("  element keys:", sorted(s.keys()))
assert set(body.keys()) == {"answer", "sources"}, body.keys()
print("SHAPE OK")

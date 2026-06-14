import requests
import time

URL = "https://api.karotter.com/api"

# セッションを使う（接続プールを使い回す）
session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

print("Waiting 15 seconds before starting...")
time.sleep(15)

print("Step 1: Login")
r = session.post(f"{URL}/auth/login", json={"identifier": "kbot", "password": "@3756437564", "gender": "other"}, timeout=30)
print(f"  Login status: {r.status_code}")
if r.status_code == 200:
    token = r.json().get("accessToken")
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("  Token acquired")
else:
    print("  Login failed, aborting")
    exit(1)

print("Waiting 10 seconds...")
time.sleep(10)

print("Step 2: Get notifications")
try:
    r = session.get(f"{URL}/notifications?limit=5", timeout=30)
    print(f"  Notifications status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            notifs = data.get("notifications", [])
        else:
            notifs = data if isinstance(data, list) else []
        print(f"  Count: {len(notifs)}")
        for n in notifs[:3]:
            ntype = n.get("type", "?")
            post = n.get("post") or {}
            content = post.get("content", "")[:60]
            author = (post.get("author") or {}).get("username", "?")
            print(f"    type={ntype} from=@{author} content={content}")
    else:
        print(f"  Body: {r.text[:300]}")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("Done!")

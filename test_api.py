import requests
import json
import time

KAROTTER_INTERNAL_URL = "https://api.karotter.com/api"
USERNAME = "kbot"
PASSWORD = "@3756437564"

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

payload = {"identifier": USERNAME, "password": PASSWORD, "gender": "other"}
print("Logging in...")
r = session.post(f"{KAROTTER_INTERNAL_URL}/auth/login", json=payload, timeout=20)
if r.status_code == 200:
    token = r.json().get("accessToken")
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("✅ Login Success")
else:
    print(f"❌ Login failed: {r.status_code}")
    exit(1)

print("\nTesting /search/users...")
try:
    res = session.get(f"{KAROTTER_INTERNAL_URL}/search/users?q=a&limit=50", timeout=20)
    print(f"Status: {res.status_code}")
    print(f"Data: {json.dumps(res.json(), ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"Search API Error: {e}")

print("\nTesting /users/vbot...")
try:
    res = session.get(f"{KAROTTER_INTERNAL_URL}/users/vbot", timeout=20)
    print(f"Status: {res.status_code}")
    print(f"Data: {json.dumps(res.json(), ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"Users API Error: {e}")

print("\nTesting /users/recommended...")
try:
    res = session.get(f"{KAROTTER_INTERNAL_URL}/users/recommended?limit=5", timeout=20)
    print(f"Status: {res.status_code}")
    print(f"Data: {json.dumps(res.json(), ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"Recommended API Error: {e}")

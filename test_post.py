import requests
import json

URL = "https://api.karotter.com/api"

# Login
r = requests.post(f"{URL}/auth/login", json={"identifier": "kbot", "password": "@3756437564", "gender": "other"}, timeout=20)
token = r.json().get("accessToken")
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 投稿テスト (通常のカロート)
payload = {
    "content": "テスト投稿です！ #kbot",
    "isAiGenerated": False,
    "isPromotional": False,
    "visibility": "PUBLIC",
    "replyRestriction": "EVERYONE"
}
r2 = requests.post(f"{URL}/posts", headers=headers, json=payload, timeout=20)
print(f"Post test status: {r2.status_code}")
if r2.status_code in [200, 201]:
    post_id = r2.json().get("id") or r2.json().get("post", {}).get("id")
    print(f"Posted successfully! ID: {post_id}")
else:
    print(f"Error: {r2.text[:200]}")

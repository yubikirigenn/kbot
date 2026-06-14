import requests

KAROTTER_DEV_API_URL = "https://api.karotter.com/developer/api"
API_KEY = "kar_live_Szu4JiqreUxsyccCDKLzSCZX1PerqSliexOo0M2PpsA" # ユーザー提供のAPIキー

headers = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json"
}

# 適当な既存の投稿（1124686）に対してリプライをテスト
payload = {
    "content": "Developer APIからのリプライテストです。 #kbot",
    "parentId": "1124686",
    "replyId": "1124686",
    "isAiGenerated": False,
    "isPromotional": False,
    "visibility": "PUBLIC",
    "replyRestriction": "EVERYONE"
}

print("Posting reply via Developer API...")
res = requests.post(f"{KAROTTER_DEV_API_URL}/posts", headers=headers, json=payload, timeout=20)
print(f"Status: {res.status_code}")
if res.status_code in [200, 201]:
    data = res.json()
    print(f"Success! ID: {data.get('id') or data.get('post', {}).get('id')}")
else:
    print(f"Error: {res.text[:300]}")

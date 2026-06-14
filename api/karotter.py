# -*- coding: utf-8 -*-
"""Karotter API通信（読み取り + 投稿作成）"""
import time
import requests
from config import (
    KAROTTER_INTERNAL_URL, KAROTTER_DEV_API_URL,
    KAROTTER_API_KEY, API_SLEEP, USERNAME
)


class KarotterAPI:
    def __init__(self, auth_manager):
        self.auth = auth_manager
        self.headers_dev = {
            "x-api-key": KAROTTER_API_KEY,
            "Content-Type": "application/json"
        }
        self._last_request_time = 0

    def _throttle(self):
        """レート制限回避のためリクエスト間隔を確保"""
        elapsed = time.time() - self._last_request_time
        if elapsed < API_SLEEP:
            time.sleep(API_SLEEP - elapsed)
        self._last_request_time = time.time()

    # === ユーザー情報 ===

    def get_user_detail(self, username):
        """ユーザー詳細を取得（postsCount, followersCount, createdAt含む）"""
        self._throttle()
        res = self.auth.request("GET", f"/users/{username}")
        if res and res.status_code == 200:
            data = res.json()
            return data.get("user", data)
        return None

    def search_users(self, query, limit=100, page=1):
        """ユーザー検索（認証不要だが、セッション経由で送信）"""
        self._throttle()
        res = self.auth.request(
            "GET", f"/search/users?q={query}&limit={limit}&page={page}"
        )
        if res and res.status_code == 200:
            data = res.json()
            return data.get("users", []), data.get("pagination", {})
        return [], {}

    def get_recommended_users(self):
        """推奨ユーザーリストを取得"""
        self._throttle()
        res = self.auth.request("GET", "/users/recommended")
        if res and res.status_code == 200:
            data = res.json()
            return data.get("users", [])
        return []

    # === 通知 ===

    def get_notifications(self, limit=20):
        """通知を取得"""
        self._throttle()
        res = self.auth.request("GET", f"/notifications?limit={limit}")
        if res and res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data.get("notifications", [])
            if isinstance(data, list):
                return data
        return []

    # === 投稿 ===

    def post_reply(self, text, parent_id):
        """返信を投稿"""
        self._throttle()
        payload = {
            "content": text,
            "parentId": parent_id,
            "replyId": parent_id,
            "isAiGenerated": False,
            "isPromotional": False,
            "visibility": "PUBLIC",
            "replyRestriction": "EVERYONE"
        }
        res = self.auth.request("POST", "/posts", json=payload)
        if res and res.status_code in [200, 201]:
            resp_json = res.json()
            new_id = str(resp_json.get("id") or resp_json.get("post", {}).get("id") or "")
            print(f"✅ 返信投稿成功 (ID: {new_id}): {text[:50]}...")
            return new_id
        else:
            code = res.status_code if res else "Unknown"
            print(f"❌ 返信投稿失敗: HTTP {code}")
        return None

    def post_karoto(self, text):
        """通常投稿（カロート）"""
        self._throttle()
        payload = {
            "content": text,
            "isAiGenerated": False,
            "isPromotional": False,
            "visibility": "PUBLIC",
            "replyRestriction": "EVERYONE"
        }
        res = self.auth.request("POST", "/posts", json=payload)
        if res and res.status_code in [200, 201]:
            resp_json = res.json()
            new_id = str(resp_json.get("id") or resp_json.get("post", {}).get("id") or "")
            print(f"✅ 投稿成功 (ID: {new_id}): {text[:50]}...")
            return new_id
        return None

    # === Developer API ===

    def dev_get_post(self, post_id):
        """Developer APIで投稿詳細を取得"""
        self._throttle()
        try:
            res = requests.get(
                f"{KAROTTER_DEV_API_URL}/posts/{post_id}",
                headers=self.headers_dev, timeout=15
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("post") or data
        except Exception as e:
            print(f"⚠️ Developer API エラー: {e}")
        return None

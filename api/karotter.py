# -*- coding: utf-8 -*-
"""Karotter API通信（読み取り + 投稿作成）"""
import os
import io
import time
import uuid
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

    def upload_media(self, image_bytes, filename=None):
        """Supabase Storageに直接アップロードし、公開URLを返す"""
        if filename is None:
            filename = f"kbot_{uuid.uuid4().hex}.png"
        print(f"[API] Uploading media to Supabase ({filename})...")
        supabase_url = os.environ.get("SUPABASE_URL", "https://idsowzwvvhiwjxaxtjvl.supabase.co")
        supabase_key = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imlkc293end2dmhpd2p4YXh0anZsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDc0MzM0OSwiZXhwIjoyMDk2MzE5MzQ5fQ.k1V3XaemOAgzmJgxiDLITjkYaV0E3boWctjHJu68aSM")

        url = f"{supabase_url}/storage/v1/object/bot_media/{filename}"
        headers = {
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": "image/png"
        }
        try:
            res = requests.post(url, headers=headers, data=image_bytes, timeout=30)
            if res.status_code in [200, 201]:
                public_url = f"{supabase_url}/storage/v1/object/public/bot_media/{filename}"
                print(f"[API] Upload success: {public_url}")
                return public_url
            elif "Duplicate" in res.text:
                public_url = f"{supabase_url}/storage/v1/object/public/bot_media/{filename}"
                print(f"[API] File already exists: {public_url}")
                return public_url
            else:
                print(f"[API] Supabase upload failed: {res.status_code} {res.text}")
        except Exception as e:
            print(f"[API] Media upload error: {e}")
        return None

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

    def post_reply(self, text, parent_id, media_urls=None):
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
        if media_urls:
            payload["mediaUrls"] = media_urls

        res = self.auth.request("POST", "/posts", json=payload)
        if res and res.status_code in [200, 201]:
            print(f"[API] Reply sent successfully to post {parent_id}")
            return True
        else:
            status = res.status_code if res else "Unknown"
            print(f"[API] Reply failed: HTTP {status}")
            return False

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
            print(f"[API] Post success (ID: {new_id}): {text[:50]}...")
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
            print(f"[API] Developer API error: {e}")
        return None

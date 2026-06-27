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
        if res:
            if res.status_code == 200:
                data = res.json()
                return data.get("user", data)
            elif res.status_code == 404:
                return {"is_deleted": True}
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

    # === 画像ホスティング（自サーバー配信） ===
    # メモリ上に画像を保持し、kbot自身のHTTPサーバーから配信する
    _image_store = {}  # {filename: bytes}

    @classmethod
    def store_image(cls, image_bytes, filename=None):
        """画像をメモリに保存し、配信用のパスを返す"""
        if filename is None:
            filename = f"kbot_{uuid.uuid4().hex}.png"
        cls._image_store[filename] = image_bytes
        # 古い画像を削除（最大20枚まで保持）
        if len(cls._image_store) > 20:
            oldest = list(cls._image_store.keys())[0]
            del cls._image_store[oldest]
        return filename

    @classmethod
    def get_image(cls, filename):
        """保存済み画像のバイト列を返す"""
        return cls._image_store.get(filename)

    def upload_media(self, image_bytes, filename=None):
        """画像を自サーバーに保存し、公開URLを返す"""
        if filename is None:
            filename = f"kbot_{uuid.uuid4().hex}.png"
        
        KarotterAPI.store_image(image_bytes, filename)
        
        # Render上では RENDER_EXTERNAL_URL (https://kbot-xxxx.onrender.com) が設定される
        external_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        if external_url:
            public_url = f"{external_url.rstrip('/')}/images/{filename}"
        else:
            # ローカル開発時
            port = os.environ.get("PORT", "8080")
            public_url = f"http://localhost:{port}/images/{filename}"
        
        print(f"[API] Image stored: {public_url}")
        return public_url

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

    def post_reply(self, text, parent_id, media_files=None, as_rekarot=False):
        """返信を投稿（media_filesがあればFormDataで画像を添付）
        
        Args:
            text: 返信テキスト
            parent_id: 返信先の投稿ID
            media_files: list of bytes (画像バイナリデータ) or None
            as_rekarot: Trueの場合、通常のリプライではなく引用リカロートで投稿する
        """
        self._throttle()
        
        if media_files:
            # FormData方式（画像添付あり）
            import io
            from requests_toolbelt import MultipartEncoder
            
            fields = {
                "content": text,
                "isAiGenerated": "false",
                "isPromotional": "false",
                "visibility": "PUBLIC",
                "replyRestriction": "EVERYONE",
            }
            
            if as_rekarot:
                fields.update({
                    "quotedPostId": str(parent_id),
                    "quoteId": str(parent_id),
                    "renoteId": str(parent_id),
                    "isQuote": "true",
                    "type": "QUOTE",
                })
            else:
                fields.update({
                    "parentId": str(parent_id),
                    "replyId": str(parent_id),
                })
            
            # 画像ファイルを追加
            parts = []
            for key, val in fields.items():
                parts.append((key, val))
            
            for i, img_bytes in enumerate(media_files):
                filename = f"ranking_{i}.png"
                parts.append(("media", (filename, io.BytesIO(img_bytes), "image/png")))
            
            encoder = MultipartEncoder(fields=parts)
            headers = {"Content-Type": encoder.content_type}
            
            res = self.auth.request(
                "POST", "/posts", 
                data=encoder, 
                headers=headers
            )
        else:
            # JSON方式（テキストのみ）
            payload = {
                "content": text,
                "isAiGenerated": False,
                "isPromotional": False,
                "visibility": "PUBLIC",
                "replyRestriction": "EVERYONE"
            }
            
            if as_rekarot:
                payload.update({
                    "quotedPostId": parent_id,
                    "quoteId": parent_id,
                    "renoteId": parent_id,
                    "isQuote": True,
                    "type": "QUOTE",
                })
            else:
                payload.update({
                    "parentId": parent_id,
                    "replyId": parent_id,
                })
                
            res = self.auth.request("POST", "/posts", json=payload)
        
        if res and res.status_code in [200, 201]:
            print(f"[API] Reply sent successfully to post {parent_id}")
            return True
        else:
            status = res.status_code if res else "Unknown"
            body = ""
            try:
                body = res.text[:200] if res else ""
            except:
                pass
            print(f"[API] Reply failed: HTTP {status} {body}")
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

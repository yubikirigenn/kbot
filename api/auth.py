# -*- coding: utf-8 -*-
"""ログイン・トークン管理・401自動再認証"""
import time
import requests
from config import KAROTTER_INTERNAL_URL, USERNAME, PASSWORD


class AuthManager:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.token = None
        self.last_login_time = 0

    def login(self):
        """ログインしてBearerトークンを取得"""
        payload = {"identifier": USERNAME, "password": PASSWORD, "gender": "other"}
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{KAROTTER_INTERNAL_URL}/auth/login",
                    json=payload, timeout=20
                )
                if r.status_code == 200:
                    self.token = r.json().get("accessToken")
                    self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                    self.last_login_time = time.time()
                    print(f"✅ ログイン成功 (@{USERNAME})")
                    return True
                else:
                    print(f"⚠️ ログイン失敗 (HTTP {r.status_code})")
            except Exception as e:
                print(f"⚠️ ログインエラー (リトライ {attempt+1}/3): {e}")
                time.sleep(10 * (attempt + 1))
        return False

    def ensure_login(self):
        """トークンの有効性を確認し、必要に応じて再ログイン"""
        if time.time() - self.last_login_time > 300:
            self.login()

    def request(self, method, endpoint, retries=3, **kwargs):
        """認証付きリクエスト。エラー時はリトライし、401時は自動再ログインしてリトライ"""
        url = f"{KAROTTER_INTERNAL_URL}{endpoint}"
        kwargs.setdefault("timeout", 20)
        
        for attempt in range(retries):
            try:
                res = self.session.request(method, url, **kwargs)
                if res.status_code == 401:
                    print(f"⚠️ 401検知({endpoint})。再ログインしてリトライ...")
                    if self.login():
                        res = self.session.request(method, url, **kwargs)
                return res
            except Exception as e:
                print(f"⚠️ API通信エラー (リトライ {attempt+1}/{retries} - {endpoint}): {e}")
                time.sleep(5 * (attempt + 1))
        
        print(f"❌ API通信エラー ({endpoint}): リトライ上限到達")
        return None

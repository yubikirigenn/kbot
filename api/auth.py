# -*- coding: utf-8 -*-
"""ログイン・トークン管理・401自動再認証"""
import time
import requests
from config import KAROTTER_INTERNAL_URL, USERNAME, PASSWORD


class AuthManager:
    def __init__(self, username=None, password=None):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.token = None
        self.last_login_time = 0
        self.username = username or USERNAME
        self.password = password or PASSWORD

    def login(self):
        """ログインしてBearerトークンを取得"""
        payload = {"identifier": self.username, "password": self.password, "gender": "other"}
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
                    print(f"[AUTH] Login success (@{self.username})")
                    return True
                else:
                    print(f"[AUTH] Login failed (HTTP {r.status_code})")
            except Exception as e:
                print(f"[AUTH] Login error (retry {attempt+1}/3): {e}")
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
        
        # FormData送信時はセッションのContent-Typeを一時的に除去
        custom_headers = kwargs.get("headers")
        original_ct = self.session.headers.get("Content-Type")
        if custom_headers and "Content-Type" in custom_headers:
            self.session.headers.pop("Content-Type", None)

        try:
            for attempt in range(retries):
                try:
                    res = self.session.request(method, url, **kwargs)
                    if res.status_code == 401:
                        print(f"[AUTH] 401 detected ({endpoint}). Re-login...")
                        if self.login():
                            res = self.session.request(method, url, **kwargs)
                    elif res.status_code == 200:
                        # 200 OK 時の 0 検知による再ログイン安全策
                        try:
                            data = res.json()
                            user_data = data.get("user", data) if isinstance(data, dict) else {}
                            if isinstance(user_data, dict) and user_data.get("postsCount") == 0:
                                # 直近 30 秒以内にログイン成功している場合は、本当に 0 のユーザーとみなしてスキップ
                                if time.time() - self.last_login_time > 30:
                                    print(f"[AUTH] postsCount is 0 detected ({endpoint}). Suspecting token expiration. Re-login fallback...")
                                    if self.login():
                                        res = self.session.request(method, url, **kwargs)
                        except Exception:
                            pass
                    return res
                except Exception as e:
                    print(f"[AUTH] API error (retry {attempt+1}/{retries} - {endpoint}): {e}")
                    time.sleep(5 * (attempt + 1))

            print(f"[AUTH] API error ({endpoint}): max retries reached")
            return None
        finally:
            # Content-Typeを元に戻す
            if original_ct:
                self.session.headers["Content-Type"] = original_ct

# -*- coding: utf-8 -*-
"""ログイン・トークン管理・401自動再認証"""
import time
import threading
import requests
from config import KAROTTER_INTERNAL_URL, USERNAME, PASSWORD


class AuthManager:
    def __init__(self, username=None, password=None):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._lock = threading.RLock()
        self.token = None
        self.last_login_time = 0
        self.username = username or USERNAME
        self.password = password or PASSWORD

    def login(self):
        with self._lock:
            return self._login_locked()

    def _login_locked(self):
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
                    if not self.token:
                        print("[AUTH] Login failed: access token was not returned")
                        continue
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
        with self._lock:
            if not self.token or time.time() - self.last_login_time > 300:
                return self._login_locked()
            return True

    def request(self, method, endpoint, retries=3, **kwargs):
        with self._lock:
            return self._request_locked(method, endpoint, retries=retries, **kwargs)

    def _request_locked(self, method, endpoint, retries=3, **kwargs):
        """認証付きリクエスト。エラー時はリトライし、401時は自動再ログインしてリトライ"""
        url = f"{KAROTTER_INTERNAL_URL}{endpoint}"
        kwargs.setdefault("timeout", 20)

        # POST はサーバー側で処理済みでも応答だけが失われることがある。
        # 自動再送すると同じ返信を二重投稿するため、ここで再試行するのは
        # 安全な読み取りリクエストだけにする。401/403後の再認証送信は維持する。
        can_retry = method.upper() in {"GET", "HEAD", "OPTIONS"}
        attempts = retries if can_retry else 1
        
        # FormData送信時はセッションのContent-Typeを一時的に除去
        custom_headers = kwargs.get("headers")
        original_ct = self.session.headers.get("Content-Type")
        if (custom_headers and "Content-Type" in custom_headers) or "files" in kwargs:
            self.session.headers.pop("Content-Type", None)

        last_error = None
        try:
            for attempt in range(attempts):
                try:
                    # リトライ時にファイルオブジェクトのシーク位置を最初に戻す (seek(0))
                    if "files" in kwargs:
                        for file_item in kwargs["files"]:
                            if isinstance(file_item, tuple) and len(file_item) >= 2:
                                val = file_item[1]
                                if isinstance(val, tuple) and len(val) >= 2:
                                    fileobj = val[1]
                                    if hasattr(fileobj, "seek"):
                                        try:
                                            fileobj.seek(0)
                                        except Exception:
                                            pass

                    res = self.session.request(method, url, **kwargs)
                    if res.status_code in (401, 403):
                        print(f"[AUTH] {res.status_code} detected ({endpoint}). Re-login...")
                        if self._login_locked():
                            # 再ログイン後も、リトライのため念のため seek(0) を行う
                            if "files" in kwargs:
                                for file_item in kwargs["files"]:
                                    if isinstance(file_item, tuple) and len(file_item) >= 2:
                                        val = file_item[1]
                                        if isinstance(val, tuple) and len(val) >= 2:
                                            fileobj = val[1]
                                            if hasattr(fileobj, "seek"):
                                                try:
                                                    fileobj.seek(0)
                                                except Exception:
                                                    pass
                            res = self.session.request(method, url, **kwargs)

                    if res.status_code == 200:
                        # 200 OK 時の 0 検知による再ログイン安全策
                        try:
                            data = res.json()
                            user_data = data.get("user", data) if isinstance(data, dict) else {}
                            if isinstance(user_data, dict) and user_data.get("postsCount") == 0:
                                # endpoint が /users/xxx の形式であるかチェックし、ユーザー名を特定
                                import re
                                import os
                                match = re.match(r"^/users/([^/?#]+)", endpoint)
                                if match:
                                    target_username = match.group(1)
                                    # キャッシュファイルを参照し、過去に投稿実績（postsCount > 0）があったか確認
                                    has_previous_posts = False
                                    try:
                                        from config import USER_CACHE_FILE
                                        if os.path.exists(USER_CACHE_FILE):
                                            with open(USER_CACHE_FILE, "r", encoding="utf-8") as f:
                                                cache_data = json.load(f)
                                                prev_user = cache_data.get(target_username)
                                                if prev_user and prev_user.get("postsCount", 0) > 0:
                                                    has_previous_posts = True
                                    except Exception:
                                        pass
                                    
                                    # 過去に投稿があるユーザーが0件で返ってきた場合のみ、安全策を作動させる
                                    if has_previous_posts:
                                        if time.time() - self.last_login_time > 30:
                                            print(f"[AUTH] postsCount is 0 detected for active user @{target_username} ({endpoint}). Suspecting token expiration. Re-login fallback...")
                                            if self.login():
                                                # 再ログイン後も、念のため seek(0) を行う
                                                if "files" in kwargs:
                                                    for file_item in kwargs["files"]:
                                                        if isinstance(file_item, tuple) and len(file_item) >= 2:
                                                            val = file_item[1]
                                                            if isinstance(val, tuple) and len(val) >= 2:
                                                                fileobj = val[1]
                                                                if hasattr(fileobj, "seek"):
                                                                    try:
                                                                        fileobj.seek(0)
                                                                    except Exception:
                                                                        pass
                                                res = self.session.request(method, url, **kwargs)
                        except Exception:
                            pass

                    if (
                        can_retry
                        and res.status_code in (429, 500, 502, 503, 504)
                        and attempt < attempts - 1
                    ):
                        retry_after = res.headers.get("Retry-After", "")
                        try:
                            delay = max(float(retry_after), 1.0)
                        except (TypeError, ValueError):
                            delay = 5 * (attempt + 1)
                        print(f"[AUTH] HTTP {res.status_code} ({endpoint}). Retrying in {delay:.0f}s ({attempt + 1}/{retries})...")
                        time.sleep(delay)
                        continue
                    return res
                except Exception as e:
                    last_error = e
                    if not can_retry:
                        print(f"[AUTH] API error (POST will not be retried - {endpoint}): {e}")
                        return None
                    print(f"[AUTH] API error (retry {attempt+1}/{attempts} - {endpoint}): {e}")
                    time.sleep(5 * (attempt + 1))

            print(f"[AUTH] API error ({endpoint}): max retries reached. Last error: {last_error}")
            return None
        finally:
            # Content-Typeを元に戻す
            if original_ct:
                self.session.headers["Content-Type"] = original_ct

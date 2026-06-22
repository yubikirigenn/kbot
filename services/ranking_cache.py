# -*- coding: utf-8 -*-
"""ユーザーデータのキャッシュ管理とランキング計算"""
import os
import json
import time
import threading
from datetime import datetime, timezone
from config import USER_CACHE_FILE


class RankingCache:
    def __init__(self):
        self._lock = threading.RLock()
        with self._lock:
            self.users = {}  # {username: {postsCount, followersCount, followingCount, createdAt, rate, updatedAt}}
            self._ensure_data_dir()
            self.load()

    def _ensure_data_dir(self):
        os.makedirs(os.path.dirname(USER_CACHE_FILE), exist_ok=True)

    def load(self):
        """キャッシュファイルからユーザーデータを読み込み"""
        with self._lock:
            if os.path.exists(USER_CACHE_FILE):
                try:
                    with open(USER_CACHE_FILE, "r", encoding="utf-8") as f:
                        self.users = json.load(f)
                    print(f"📂 キャッシュ読み込み完了: {len(self.users)}ユーザー")
                except Exception as e:
                    print(f"⚠️ キャッシュ読み込みエラー: {e}")
                    self.users = {}

    def save(self):
        """キャッシュファイルに保存"""
        with self._lock:
            from utils.anomaly_detector import detector
            detector.trace("SAVE_BEFORE", "save", cache_obj=self)

            try:
                self._ensure_data_dir()
                with open(USER_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.users, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠️ キャッシュ保存エラー: {e}")

            detector.trace("SAVE_AFTER", "save", cache_obj=self)

    def update_user(self, username, user_data):
        """ユーザーデータを更新"""
        if not user_data or not username:
            return

        with self._lock:
            from utils.anomaly_detector import detector
            detector.trace("CACHE_UPDATE_BEFORE", f"update_user_{username}", cache_obj=self, extra={"update_data": {"postsCount": user_data.get("postsCount")}})

            old_data = self.users.get(username, {})

            # API側が負荷等で0や空を返すことがあるため、古いデータがある場合は欠損を補完・保護する
            new_posts = user_data.get("postsCount", 0)
            posts_count = max(old_data.get("postsCount", 0), new_posts) if new_posts == 0 else new_posts

            new_followers = user_data.get("followersCount", 0)
            followers_count = max(old_data.get("followersCount", 0), new_followers) if new_followers == 0 else new_followers

            new_following = user_data.get("followingCount", 0)
            following_count = max(old_data.get("followingCount", 0), new_following) if new_following == 0 else new_following

            created_at = user_data.get("createdAt", "") or old_data.get("createdAt", "")
            
            is_bot = user_data.get("isBotAccount", old_data.get("isBot", False))
            is_private = user_data.get("isPrivate", old_data.get("isPrivate", False))
            
            display_name = user_data.get("displayName") or user_data.get("name") or old_data.get("displayName") or username
            avatar_url = user_data.get("avatarUrl") or user_data.get("profileImageUrl") or old_data.get("avatarUrl") or ""

            # 内部キャッシュ用レート計算（表示・ソート用は後で動的計算）
            rate = 0.0
            if created_at and posts_count > 0:
                try:
                    created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    hours = (now - created_dt).total_seconds() / 3600
                    hours = max(hours, 1.0) # 1時間未満は1時間に丸めて無限レートを防ぐ
                    if hours > 0:
                        rate = round(posts_count / hours, 4)
                except Exception:
                    pass

            self.users[username] = {
                "postsCount": posts_count,
                "followersCount": followers_count,
                "followingCount": following_count,
                "createdAt": created_at,
                "rate": rate,
                "isBot": is_bot,
                "isPrivate": is_private,
                "displayName": display_name,
                "avatarUrl": avatar_url,
                "updatedAt": datetime.now(timezone.utc).isoformat()
            }
            from utils.anomaly_detector import detector
            detector.trace("CACHE_UPDATE_AFTER", f"update_user_{username}", cache_obj=self)

    def update_user_from_search(self, user_data):
        """検索APIの結果からユーザーデータを部分更新（followersCountのみ）"""
        username = user_data.get("username", "")
        if not username:
            return
        with self._lock:
            if username not in self.users:
                self.users[username] = {
                    "postsCount": 0,
                    "followersCount": 0,
                    "followingCount": 0,
                    "createdAt": "",
                    "rate": 0.0,
                    "isBot": user_data.get("isBotAccount", False),
                    "isPrivate": user_data.get("isPrivate", False),
                    "updatedAt": ""
                }
            self.users[username]["followersCount"] = user_data.get("followersCount", 0)
            self.users[username]["followingCount"] = user_data.get("followingCount", 0)
            self.users[username]["isBot"] = user_data.get("isBotAccount", False)
            self.users[username]["isPrivate"] = user_data.get("isPrivate", self.users[username].get("isPrivate", False))

    def _get_dynamic_rate(self, data, now):
        """現在時刻での動的レート計算"""
        posts_count = data.get("postsCount", 0)
        created_at = data.get("createdAt")
        if not created_at or posts_count <= 0:
            return 0.0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            hours = (now - created_dt).total_seconds() / 3600
            hours = max(hours, 1.0)
            return round(posts_count / hours, 4)
        except Exception:
            return 0.0

    def get_active_users(self):
        """アクティブユーザー（Bot・非公開を除く、投稿数1以上）をフィルタリング"""
        now = datetime.now(timezone.utc)
        with self._lock:
            result = {}
            for username, data in self.users.items():
                if not data.get("isBot", False) and not data.get("isPrivate", False) and data.get("postsCount", 0) > 0:
                    user_copy = data.copy()
                    user_copy["rate"] = self._get_dynamic_rate(data, now)
                    result[username] = user_copy
            return result

    def get_all_users_for_followers(self):
        """フォロワーランキング用ユーザー（Bot除外、投稿なくてもOK）"""
        now = datetime.now(timezone.utc)
        with self._lock:
            result = {}
            for username, data in self.users.items():
                if not data.get("isBot", False) and not data.get("isPrivate", False):
                    user_copy = data.copy()
                    user_copy["rate"] = self._get_dynamic_rate(data, now)
                    result[username] = user_copy
            return result

    def get_ranking(self, sort_key, username):
        """指定キーでソートして、指定ユーザーの順位を返す"""
        with self._lock:
            if sort_key == "followers":
                self._lock.release()
                try:
                    pool = self.get_all_users_for_followers()
                finally:
                    self._lock.acquire()
            else:
                self._lock.release()
                try:
                    pool = self.get_active_users()
                finally:
                    self._lock.acquire()

            if sort_key == "rate":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("rate", 0), reverse=True)
            elif sort_key == "posts":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("postsCount", 0), reverse=True)
            elif sort_key == "followers":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("followersCount", 0), reverse=True)
            else:
                return None, 0

            rank = 1
            for uname, _ in sorted_users:
                if uname == username:
                    return rank, len(sorted_users)
                rank += 1
            return None, len(sorted_users)

    def get_top_n(self, sort_key, n=10):
        """上位N件を返す"""
        with self._lock:
            if sort_key == "followers":
                # get_all_users_for_followers は RLock なのでそのまま呼び出せる
                pool = self.get_all_users_for_followers()
            else:
                pool = self.get_active_users()

            if sort_key == "rate":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("rate", 0), reverse=True)
            elif sort_key == "posts":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("postsCount", 0), reverse=True)
            elif sort_key == "followers":
                sorted_users = sorted(pool.items(), key=lambda x: x[1].get("followersCount", 0), reverse=True)
            else:
                return []
            return sorted_users[:n]

    def get_user(self, username):
        """特定ユーザーのキャッシュデータを取得"""
        with self._lock:
            return self.users.get(username)

    def delete_user(self, username):
        """ユーザーをキャッシュから削除"""
        with self._lock:
            if username in self.users:
                del self.users[username]
                print(f"🗑️ キャッシュからユーザーを削除しました: {username}")
                return True
        return False

    def user_count(self):
        with self._lock:
            return len(self.users)

    def active_user_count(self):
        with self._lock:
            return len(self.get_active_users())

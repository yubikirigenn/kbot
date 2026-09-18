# -*- coding: utf-8 -*-
"""ユーザーデータのキャッシュ管理とランキング計算。

username は変更・再利用されるため、API の安定 ID を同一人物判定に使う。
公開インターフェースは従来どおり username ベースだが、更新時は userId を
優先して改名を追跡する。
"""
import copy
import json
import os
import threading
from datetime import datetime, timezone

from config import USER_CACHE_FILE, EXCLUDED_USERS_FILE


def _as_user_id(value):
    if value is None or value == "":
        return ""
    return str(value)


class RankingCache:
    def __init__(self):
        self._lock = threading.RLock()
        self.users = {}
        self.excluded_users = set()
        self._id_to_username = {}
        self._casefold_to_username = {}
        self._ensure_data_dir()
        self.load()
        self.load_excluded_users()

    def _ensure_data_dir(self):
        directory = os.path.dirname(USER_CACHE_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _record_score(data):
        posts = data.get("postsCount")
        sampled = data.get("sampledAt") or data.get("updatedAt") or ""
        return (
            1 if _as_user_id(data.get("userId") or data.get("id")) else 0,
            1 if posts not in (None, 0) else 0,
            sampled,
        )

    @staticmethod
    def _same_legacy_identity(left, right):
        left_id = _as_user_id(left.get("userId") or left.get("id"))
        right_id = _as_user_id(right.get("userId") or right.get("id"))
        if left_id and right_id:
            return left_id == right_id
        left_created = left.get("createdAt") or ""
        right_created = right.get("createdAt") or ""
        return bool(left_created and right_created and left_created == right_created)

    def _normalize_loaded_users_locked(self):
        """大文字小文字だけ異なる重複を一つにし、索引を再構築する。"""
        normalized = {}
        folded_to_key = {}
        for raw_name, raw_data in self.users.items():
            if not isinstance(raw_data, dict):
                continue
            name = str(raw_data.get("username") or raw_name).strip()
            if not name:
                continue
            data = dict(raw_data)
            data["username"] = name
            user_id = _as_user_id(data.get("userId") or data.get("id"))
            if user_id:
                data["userId"] = user_id
            data.pop("id", None)

            folded = name.casefold()
            previous_key = folded_to_key.get(folded)
            if previous_key is None:
                normalized[name] = data
                folded_to_key[folded] = name
                continue

            previous = normalized[previous_key]
            winner_name, winner, loser = previous_key, previous, data
            if self._record_score(data) > self._record_score(previous):
                winner_name, winner, loser = name, data, previous
                del normalized[previous_key]
                normalized[winner_name] = winner
                folded_to_key[folded] = winner_name

            if self._same_legacy_identity(winner, loser):
                for key, value in loser.items():
                    if winner.get(key) in (None, "") and value not in (None, ""):
                        winner[key] = value

        self.users = normalized
        self._rebuild_indexes_locked()

    def _rebuild_indexes_locked(self):
        self._id_to_username = {}
        self._casefold_to_username = {}
        duplicate_ids = []
        for username, data in self.users.items():
            self._casefold_to_username[username.casefold()] = username
            user_id = _as_user_id(data.get("userId"))
            if not user_id:
                continue
            previous = self._id_to_username.get(user_id)
            if previous is None:
                self._id_to_username[user_id] = username
            else:
                keep, drop = previous, username
                if self._record_score(data) > self._record_score(self.users[previous]):
                    keep, drop = username, previous
                    self._id_to_username[user_id] = keep
                duplicate_ids.append((keep, drop))

        for keep, drop in duplicate_ids:
            if keep == drop or drop not in self.users:
                continue
            kept = self.users[keep]
            removed = self.users.pop(drop)
            for key, value in removed.items():
                if kept.get(key) in (None, "") and value not in (None, ""):
                    kept[key] = value

        if duplicate_ids:
            self._id_to_username = {}
            self._casefold_to_username = {}
            for username, data in self.users.items():
                self._casefold_to_username[username.casefold()] = username
                user_id = _as_user_id(data.get("userId"))
                if user_id:
                    self._id_to_username[user_id] = username

    def _resolve_key_locked(self, username):
        if not username:
            return None
        return self._casefold_to_username.get(str(username).casefold())

    def load(self):
        with self._lock:
            if not os.path.exists(USER_CACHE_FILE):
                return
            try:
                with open(USER_CACHE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.users = loaded.get("users", {}) if isinstance(loaded, dict) and "users" in loaded else loaded
                if not isinstance(self.users, dict):
                    raise ValueError("users cache must be a JSON object")
                self._normalize_loaded_users_locked()
                print(f"📂 キャッシュ読み込み完了: {len(self.users)}ユーザー")
            except Exception as e:
                print(f"⚠️ キャッシュ読み込みエラー: {e}")
                self.users = {}
                self._rebuild_indexes_locked()

    def save(self):
        """一時ファイルを使い、途中終了でJSONを壊さない。"""
        with self._lock:
            from utils.anomaly_detector import detector
            detector.trace("SAVE_BEFORE", "save", cache_obj=self)
            temp_path = f"{USER_CACHE_FILE}.tmp"
            try:
                self._ensure_data_dir()
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.users, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, USER_CACHE_FILE)
            except Exception as e:
                print(f"⚠️ キャッシュ保存エラー: {e}")
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass
            detector.trace("SAVE_AFTER", "save", cache_obj=self)

    @staticmethod
    def _identity_mismatch(old_data, new_user_id, new_created_at):
        old_id = _as_user_id(old_data.get("userId"))
        if old_id and new_user_id:
            return old_id != new_user_id
        old_created = old_data.get("createdAt") or ""
        return bool(old_created and new_created_at and old_created != new_created_at)

    def update_user(self, username, user_data):
        """詳細APIの結果を更新し、APIが返した正規usernameを返す。"""
        if not user_data or not username:
            return None
        canonical = str(user_data.get("username") or username).strip()
        if not canonical:
            return None
        new_user_id = _as_user_id(user_data.get("id") or user_data.get("userId"))
        new_created_at = user_data.get("createdAt") or ""
        sampled_at = datetime.now(timezone.utc).isoformat()

        with self._lock:
            from utils.anomaly_detector import detector
            detector.trace(
                "CACHE_UPDATE_BEFORE", f"update_user_{username}", cache_obj=self,
                extra={"update_data": {"postsCount": user_data.get("postsCount"), "userId": new_user_id}},
            )
            identity_key = self._id_to_username.get(new_user_id) if new_user_id else None
            requested_key = self._resolve_key_locked(username)
            canonical_key = self._resolve_key_locked(canonical)
            source_key = identity_key or canonical_key or requested_key
            old_data = dict(self.users.get(source_key, {})) if source_key else {}

            if old_data and self._identity_mismatch(old_data, new_user_id, new_created_at):
                old_data = {}

            new_posts = user_data.get("postsCount")
            posts_count = old_data.get("postsCount") if new_posts is None else new_posts
            new_followers = user_data.get("followersCount")
            followers_count = old_data.get("followersCount", 0) if new_followers is None else new_followers
            new_following = user_data.get("followingCount")
            following_count = old_data.get("followingCount", 0) if new_following is None else new_following
            created_at = new_created_at or old_data.get("createdAt", "")
            user_id = new_user_id or _as_user_id(old_data.get("userId"))

            rate = 0.0
            if created_at and posts_count is not None and posts_count > 0:
                try:
                    created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    hours = max((datetime.now(timezone.utc) - created_dt).total_seconds() / 3600, 1.0)
                    rate = round(posts_count / hours, 4)
                except Exception:
                    pass

            record = {
                "userId": user_id,
                "username": canonical,
                "postsCount": posts_count,
                "followersCount": followers_count,
                "followingCount": following_count,
                "createdAt": created_at,
                "rate": rate,
                "isBot": user_data.get("isBotAccount", old_data.get("isBot", False)),
                "isPrivate": user_data.get("isPrivate", old_data.get("isPrivate", False)),
                "displayName": user_data.get("displayName") or user_data.get("name") or old_data.get("displayName") or canonical,
                "avatarUrl": user_data.get("avatarUrl") or user_data.get("profileImageUrl") or old_data.get("avatarUrl") or "",
                "sampledAt": sampled_at,
                "updatedAt": sampled_at,
                "fail_count": 0,
            }

            keys_to_remove = set()
            for candidate in (source_key, requested_key, canonical_key):
                if candidate and candidate != canonical:
                    keys_to_remove.add(candidate)
            if user_id:
                keys_to_remove.update(
                    key for key, value in self.users.items()
                    if key != canonical and _as_user_id(value.get("userId")) == user_id
                )
            for key in keys_to_remove:
                self.users.pop(key, None)
            self.users[canonical] = record
            self._rebuild_indexes_locked()
            detector.trace("CACHE_UPDATE_AFTER", f"update_user_{canonical}", cache_obj=self)
            return canonical

    def update_user_from_search(self, user_data):
        """検索結果をID対応の部分レコードとして取り込む。"""
        username = str(user_data.get("username") or "").strip()
        if not username:
            return None
        user_id = _as_user_id(user_data.get("id") or user_data.get("userId"))
        with self._lock:
            identity_key = self._id_to_username.get(user_id) if user_id else None
            name_key = self._resolve_key_locked(username)
            source_key = identity_key or name_key
            old_data = dict(self.users.get(source_key, {})) if source_key else {}
            incoming_created = user_data.get("createdAt") or ""
            if old_data and self._identity_mismatch(old_data, user_id, incoming_created):
                old_data = {}

            record = dict(old_data)
            record.update({
                "userId": user_id or _as_user_id(old_data.get("userId")),
                "username": username,
                "followersCount": user_data.get("followersCount", old_data.get("followersCount", 0)),
                "followingCount": user_data.get("followingCount", old_data.get("followingCount", 0)),
                "isBot": user_data.get("isBotAccount", old_data.get("isBot", False)),
                "isPrivate": user_data.get("isPrivate", old_data.get("isPrivate", False)),
                "displayName": user_data.get("displayName") or old_data.get("displayName") or username,
                "avatarUrl": user_data.get("avatarUrl") or old_data.get("avatarUrl") or "",
                "createdAt": incoming_created or old_data.get("createdAt", ""),
                "postsCount": old_data.get("postsCount"),
                "rate": old_data.get("rate", 0.0),
                "updatedAt": old_data.get("updatedAt", ""),
                "sampledAt": old_data.get("sampledAt", old_data.get("updatedAt", "")),
                "fail_count": old_data.get("fail_count", 0),
            })
            if source_key and source_key != username:
                self.users.pop(source_key, None)
            self.users[username] = record
            self._normalize_loaded_users_locked()
            return username

    def mark_fetch_failure(self, username):
        with self._lock:
            key = self._resolve_key_locked(username)
            if key:
                self.users[key]["fail_count"] = self.users[key].get("fail_count", 0) + 1
                self.users[key]["lastFailureAt"] = datetime.now(timezone.utc).isoformat()

    def should_retry(self, username, cooldown_hours=6):
        """連続失敗ユーザーを毎巡回で叩かず、一定時間後にだけ再試行する。"""
        with self._lock:
            key = self._resolve_key_locked(username)
            if not key:
                return False
            data = self.users[key]
            if data.get("fail_count", 0) < 3:
                return True
            last_failure = data.get("lastFailureAt")
            if not last_failure:
                return False
            try:
                failed_at = datetime.fromisoformat(str(last_failure).replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - failed_at).total_seconds() >= cooldown_hours * 3600
            except (TypeError, ValueError):
                return False

    def get_users_snapshot(self):
        with self._lock:
            return copy.deepcopy(self.users)

    def _get_dynamic_rate(self, data, now):
        posts_count = data.get("postsCount") or 0
        created_at = data.get("createdAt")
        if not created_at or posts_count <= 0:
            return 0.0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            hours = max((now - created_dt).total_seconds() / 3600, 1.0)
            return round(posts_count / hours, 4)
        except Exception:
            return 0.0

    def load_excluded_users(self):
        with self._lock:
            if os.path.exists(EXCLUDED_USERS_FILE):
                try:
                    with open(EXCLUDED_USERS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.excluded_users = {str(u).casefold() for u in data} if isinstance(data, list) else set()
                except Exception as e:
                    print(f"⚠️ 除外ユーザーリスト読み込みエラー: {e}")
                    self.excluded_users = set()
            else:
                self.excluded_users = set()

    def is_excluded(self, username):
        if not username:
            return False
        with self._lock:
            return str(username).casefold() in self.excluded_users

    def get_active_users(self):
        now = datetime.now(timezone.utc)
        with self._lock:
            self.load_excluded_users()
            result = {}
            for username, data in self.users.items():
                if not self.is_excluded(username) and not data.get("isBot", False) and not data.get("isPrivate", False) and (data.get("postsCount") or 0) > 0:
                    user_copy = data.copy()
                    user_copy["rate"] = self._get_dynamic_rate(data, now)
                    result[username] = user_copy
            return result

    def get_all_users_for_followers(self):
        now = datetime.now(timezone.utc)
        with self._lock:
            self.load_excluded_users()
            result = {}
            for username, data in self.users.items():
                if not self.is_excluded(username) and not data.get("isBot", False) and not data.get("isPrivate", False):
                    user_copy = data.copy()
                    user_copy["rate"] = self._get_dynamic_rate(data, now)
                    result[username] = user_copy
            return result

    def get_ranking(self, sort_key, username):
        with self._lock:
            pool = self.get_all_users_for_followers() if sort_key == "followers" else self.get_active_users()
            key_name = {"rate": "rate", "posts": "postsCount", "followers": "followersCount"}.get(sort_key)
            if not key_name:
                return None, 0
            sorted_users = sorted(pool.items(), key=lambda x: x[1].get(key_name) or 0, reverse=True)
            target = str(username).casefold()
            for rank, (uname, _) in enumerate(sorted_users, 1):
                if uname.casefold() == target:
                    return rank, len(sorted_users)
            return None, len(sorted_users)

    def get_top_n(self, sort_key, n=10):
        with self._lock:
            pool = self.get_all_users_for_followers() if sort_key == "followers" else self.get_active_users()
            key_name = {"rate": "rate", "posts": "postsCount", "followers": "followersCount"}.get(sort_key)
            if not key_name:
                return []
            return sorted(pool.items(), key=lambda x: x[1].get(key_name) or 0, reverse=True)[:n]

    def get_user(self, username):
        with self._lock:
            key = self._resolve_key_locked(username)
            return self.users.get(key) if key else None

    def delete_user(self, username):
        with self._lock:
            key = self._resolve_key_locked(username)
            if not key:
                return False
            del self.users[key]
            self._rebuild_indexes_locked()
            print(f"🗑️ キャッシュからユーザーを削除しました: {key}")
            return True

    def user_count(self):
        with self._lock:
            return len(self.users)

    def active_user_count(self):
        return len(self.get_active_users())

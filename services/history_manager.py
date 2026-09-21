# -*- coding: utf-8 -*-
"""日間・週間スナップショットの管理と、IDベースの差分計算。"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from config import (
    HISTORY_COVERAGE_MIN_USERS,
    HISTORY_MAX_SAMPLE_AGE_HOURS,
    HISTORY_MIN_FRESH_COVERAGE,
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DAILY_HISTORY_FILE = os.path.join(DATA_DIR, "history_daily.json")
WEEKLY_HISTORY_FILE = os.path.join(DATA_DIR, "history_weekly.json")
SNAPSHOT_SCHEMA_VERSION = 2


def _parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _user_id(data):
    value = data.get("userId") or data.get("id")
    return "" if value is None or value == "" else str(value)


class HistoryManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.daily_snapshot = {}
        self.weekly_snapshot = {}
        self.daily_timestamp = None
        self.weekly_timestamp = None
        self.daily_schema_version = 1
        self.weekly_schema_version = 1
        self._ensure_data_dir()
        self.load()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def _load_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("users", {}), data.get("timestamp"), int(data.get("schemaVersion", 1))

    def load(self):
        with self._lock:
            if os.path.exists(DAILY_HISTORY_FILE):
                try:
                    self.daily_snapshot, self.daily_timestamp, self.daily_schema_version = self._load_file(DAILY_HISTORY_FILE)
                except Exception as e:
                    print(f"⚠️ 日間履歴読み込みエラー: {e}")
            if os.path.exists(WEEKLY_HISTORY_FILE):
                try:
                    self.weekly_snapshot, self.weekly_timestamp, self.weekly_schema_version = self._load_file(WEEKLY_HISTORY_FILE)
                except Exception as e:
                    print(f"⚠️ 週間履歴読み込みエラー: {e}")

    @staticmethod
    def _atomic_write(path, payload):
        temp_path = f"{path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _sample_age_hours(data, reference_time):
        sampled = _parse_datetime(data.get("sampledAt") or data.get("updatedAt"))
        if sampled is None:
            return None
        return max(0.0, (reference_time - sampled).total_seconds() / 3600.0)

    def save_snapshot(self, cache, period):
        """ロック下で取得したキャッシュの一貫したコピーを保存する。"""
        from utils.anomaly_detector import detector
        detector.trace("SNAPSHOT_SAVE_BEFORE", f"save_snapshot_{period}", cache_obj=cache)
        users = cache.get_users_snapshot()
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        snapshot = {}
        for username, data in users.items():
            age = self._sample_age_hours(data, now)
            snapshot[username] = {
                "userId": _user_id(data),
                "username": data.get("username") or username,
                "postsCount": data.get("postsCount"),
                "followersCount": data.get("followersCount"),
                "createdAt": data.get("createdAt", ""),
                "sampledAt": data.get("sampledAt") or data.get("updatedAt", ""),
                "baselineFresh": age is not None and age <= HISTORY_MAX_SAMPLE_AGE_HOURS,
            }

        save_data = {
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "timestamp": now_str,
            "maxSampleAgeHours": HISTORY_MAX_SAMPLE_AGE_HOURS,
            "users": snapshot,
        }
        eligible_count = sum(
            1
            for value in snapshot.values()
            if value.get("userId")
            and value.get("postsCount") is not None
            and value.get("followersCount") is not None
        )
        fresh_count = sum(
            1
            for value in snapshot.values()
            if value.get("baselineFresh")
            and value.get("userId")
            and value.get("postsCount") is not None
            and value.get("followersCount") is not None
        )
        save_data["eligibleUserCount"] = eligible_count
        save_data["freshBaselineCount"] = fresh_count
        coverage = fresh_count / eligible_count if eligible_count else 0.0
        if (
            eligible_count >= HISTORY_COVERAGE_MIN_USERS
            and coverage < HISTORY_MIN_FRESH_COVERAGE
        ):
            print(
                f"⚠️ {period} スナップショットを保留しました"
                f"（有効基準 {fresh_count}/{eligible_count} = {coverage:.1%}、"
                f"必要 {HISTORY_MIN_FRESH_COVERAGE:.0%}）。"
            )
            return False

        file_path = DAILY_HISTORY_FILE if period == "day" else WEEKLY_HISTORY_FILE
        try:
            self._atomic_write(file_path, save_data)
            with self._lock:
                if period == "day":
                    self.daily_snapshot = snapshot
                    self.daily_timestamp = now_str
                    self.daily_schema_version = SNAPSHOT_SCHEMA_VERSION
                else:
                    self.weekly_snapshot = snapshot
                    self.weekly_timestamp = now_str
                    self.weekly_schema_version = SNAPSHOT_SCHEMA_VERSION
            print(f"📂 {period} のスナップショットを保存しました（有効基準 {fresh_count}/{len(snapshot)}）。")
            return True
        except Exception as e:
            print(f"⚠️ {period} 履歴保存エラー: {e}")
            return False

    @staticmethod
    def _snapshot_indexes(snapshot):
        by_id = {}
        by_name = {}
        for key, data in snapshot.items():
            uid = _user_id(data)
            if uid:
                by_id[uid] = data
            name = str(data.get("username") or key)
            by_name[name.casefold()] = data
        return by_id, by_name

    def get_period_boundary(self, period):
        """Return the calendar boundary represented by the current snapshot."""
        with self._lock:
            raw_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp
        snapshot_dt = _parse_datetime(raw_timestamp)
        if snapshot_dt is None:
            return None
        jst = timezone(timedelta(hours=9))
        local = snapshot_dt.astimezone(jst)
        if period == "week":
            local = local - timedelta(days=local.weekday())
        boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return boundary.astimezone(timezone.utc)

    def snapshot_is_current(self, period, now=None):
        """Return whether the snapshot belongs to the current JST period."""
        with self._lock:
            raw_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp
            schema_version = (
                self.daily_schema_version if period == "day" else self.weekly_schema_version
            )
        snapshot_dt = _parse_datetime(raw_timestamp)
        if snapshot_dt is None or schema_version < SNAPSHOT_SCHEMA_VERSION:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        jst = timezone(timedelta(hours=9))
        snapshot_local = snapshot_dt.astimezone(jst)
        current_local = current.astimezone(jst)
        if period == "week":
            return snapshot_local.isocalendar()[:2] == current_local.isocalendar()[:2]
        return snapshot_local.date() == current_local.date()

    def get_deltas(self, cache, period):
        """差分を返す。古い標本からの推測値は作らず valid=False にする。"""
        with self._lock:
            snapshot = dict(self.daily_snapshot if period == "day" else self.weekly_snapshot)
            snapshot_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp
            schema_version = self.daily_schema_version if period == "day" else self.weekly_schema_version

        if not snapshot:
            self.save_snapshot(cache, period)
            with self._lock:
                snapshot = dict(self.daily_snapshot if period == "day" else self.weekly_snapshot)
                snapshot_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp
                schema_version = self.daily_schema_version if period == "day" else self.weekly_schema_version

        now = datetime.now(timezone.utc)
        snapshot_dt = _parse_datetime(snapshot_timestamp)
        hours_passed = max((now - snapshot_dt).total_seconds() / 3600.0, 1 / 60) if snapshot_dt else 1.0
        current_users = cache.get_users_snapshot()
        by_id, by_name = self._snapshot_indexes(snapshot)
        deltas = {}

        for username, current in current_users.items():
            uid = _user_id(current)
            past = by_id.get(uid) if uid else None
            # v2同士でIDがない旧アカウントだけusername照合を許可する。
            if past is None and not uid and schema_version >= SNAPSHOT_SCHEMA_VERSION:
                past = by_name.get(username.casefold())

            current_age = self._sample_age_hours(current, now)
            current_sample = _parse_datetime(current.get("sampledAt") or current.get("updatedAt"))
            baseline_sample = _parse_datetime(past.get("sampledAt")) if past else snapshot_dt
            baseline_age_minutes = None
            if snapshot_dt and baseline_sample:
                baseline_age_minutes = max(
                    0.0, (snapshot_dt - baseline_sample).total_seconds() / 60.0
                )
            current_usable = current_sample is not None
            reason = ""
            valid = True

            if past is None:
                created = _parse_datetime(current.get("createdAt"))
                if snapshot_dt and created and created > snapshot_dt and current_usable:
                    past_posts = 0
                    past_followers = 0
                else:
                    past_posts = current.get("postsCount") or 0
                    past_followers = current.get("followersCount") or 0
                    valid = False
                    reason = "no_identity_baseline"
            else:
                past_posts = past.get("postsCount")
                past_followers = past.get("followersCount")
                if past_posts is None or past_followers is None:
                    valid = False
                    reason = "missing_baseline_value"
                if schema_version < SNAPSHOT_SCHEMA_VERSION or not past.get("baselineFresh", False):
                    valid = False
                    reason = "stale_or_legacy_baseline"

            if not current_usable:
                valid = False
                reason = "missing_current_sample_time"
            elif baseline_sample and current_sample < baseline_sample:
                valid = False
                reason = "current_older_than_baseline"

            cur_posts = current.get("postsCount")
            cur_followers = current.get("followersCount")
            if cur_posts is None or cur_followers is None:
                valid = False
                reason = "missing_current_value"
            cur_posts = cur_posts or 0
            cur_followers = cur_followers or 0
            past_posts = past_posts or 0
            past_followers = past_followers or 0
            delta_posts = cur_posts - past_posts
            delta_followers = cur_followers - past_followers

            deltas[username] = {
                "postsCount": delta_posts if valid else 0,
                "followersCount": delta_followers if valid else 0,
                "rate": round(delta_posts / hours_passed, 4) if valid else 0.0,
                "valid": valid,
                "reason": reason,
                "userId": uid,
                "sampleAgeHours": current_age,
                "sampledAt": current_sample.isoformat() if current_sample else None,
                "baselineAgeMinutes": baseline_age_minutes,
            }
        return deltas

    def force_reset_snapshot(self, cache):
        print("[FORCE_RESET] 日間スナップショットを現在値へリセットします...")
        return self.save_snapshot(cache, "day")

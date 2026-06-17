# -*- coding: utf-8 -*-
"""履歴データ（日間・週間スナップショット）の管理と差分計算"""
import os
import json
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DAILY_HISTORY_FILE = os.path.join(DATA_DIR, "history_daily.json")
WEEKLY_HISTORY_FILE = os.path.join(DATA_DIR, "history_weekly.json")


class HistoryManager:
    def __init__(self):
        self.daily_snapshot = {}
        self.weekly_snapshot = {}
        self.daily_timestamp = None
        self.weekly_timestamp = None
        self._ensure_data_dir()
        self.load()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def load(self):
        """スナップショットの読み込み"""
        if os.path.exists(DAILY_HISTORY_FILE):
            try:
                with open(DAILY_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.daily_snapshot = data.get("users", {})
                    self.daily_timestamp = data.get("timestamp")
            except Exception as e:
                print(f"⚠️ 日間履歴読み込みエラー: {e}")

        if os.path.exists(WEEKLY_HISTORY_FILE):
            try:
                with open(WEEKLY_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.weekly_snapshot = data.get("users", {})
                    self.weekly_timestamp = data.get("timestamp")
            except Exception as e:
                print(f"⚠️ 週間履歴読み込みエラー: {e}")

    def save_snapshot(self, cache, period):
        """現在のキャッシュ状態をスナップショットとして保存"""
        snapshot = {}
        for username, data in cache.users.items():
            snapshot[username] = {
                "postsCount": data.get("postsCount", 0),
                "followersCount": data.get("followersCount", 0),
                "rate": data.get("rate", 0.0)
            }
        
        now_str = datetime.now(timezone.utc).isoformat()
        save_data = {
            "timestamp": now_str,
            "users": snapshot
        }

        file_path = DAILY_HISTORY_FILE if period == "day" else WEEKLY_HISTORY_FILE
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            
            if period == "day":
                self.daily_snapshot = snapshot
                self.daily_timestamp = now_str
            else:
                self.weekly_snapshot = snapshot
                self.weekly_timestamp = now_str
                
            print(f"📂 {period} のスナップショットを保存しました。")
        except Exception as e:
            print(f"⚠️ {period} 履歴保存エラー: {e}")

    def get_deltas(self, cache, period):
        """指定期間の差分（増加量）を計算して返す
        戻り値: {username: {"postsCount": delta, "followersCount": delta, "rate": delta}}
        """
        snapshot = self.daily_snapshot if period == "day" else self.weekly_snapshot
        snapshot_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp
        
        # スナップショットが空の場合は、現在をスナップショットとして保存し、差分0を返す
        if not snapshot:
            self.save_snapshot(cache, period)
            snapshot = self.daily_snapshot if period == "day" else self.weekly_snapshot
            snapshot_timestamp = self.daily_timestamp if period == "day" else self.weekly_timestamp

        now = datetime.now(timezone.utc)
        hours_passed = 1.0
        if snapshot_timestamp:
            try:
                st_dt = datetime.fromisoformat(snapshot_timestamp)
                hours_passed = (now - st_dt).total_seconds() / 3600.0
                if hours_passed <= 0:
                    hours_passed = 1.0
            except Exception:
                pass

        deltas = {}
        for username, current_data in cache.users.items():
            past_data = snapshot.get(username, {})
            
            past_posts = past_data.get("postsCount")
            past_followers = past_data.get("followersCount")

            cur_posts = current_data.get("postsCount", 0)
            cur_followers = current_data.get("followersCount", 0)
            created_at = current_data.get("createdAt")

            if past_posts is None:
                # スナップショットに存在しない場合、
                # アカウント作成日が「スナップショット作成日時」より後であれば新規登録者とみなし過去値を0とする
                # 昔からいるユーザーがBotに初めて認知されただけの場合は、現在の値を過去値として増分を0にする
                is_new_account = False
                if snapshot_timestamp and created_at:
                    try:
                        st_dt = datetime.fromisoformat(snapshot_timestamp)
                        c_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if c_dt > st_dt:
                            is_new_account = True
                    except Exception:
                        pass
                
                if is_new_account:
                    past_posts = 0
                    past_followers = 0
                else:
                    past_posts = cur_posts
                    past_followers = cur_followers

            delta_posts = max(0, cur_posts - past_posts)
            delta_followers = cur_followers - past_followers
            
            # レートは単純な差分ではなく、「期間内の純粋なレート（増分投稿数 ÷ 経過時間）」とする
            calc_rate = round(delta_posts / hours_passed, 4)

            deltas[username] = {
                "postsCount": delta_posts,
                "followersCount": delta_followers,
                "rate": calc_rate
            }
        
        return deltas

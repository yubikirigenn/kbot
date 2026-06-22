# -*- coding: utf-8 -*-
"""ヘビーユーザーの postsCount 監視・異常検知・詳細トレース機構"""
import os
import threading
import json
from datetime import datetime, timezone, timedelta

class AnomalyDetector:
    def __init__(self):
        self._lock = threading.Lock()
        self.targets = ['zc', 'gotoh', 'miyaaa_96']
        # ターゲットごとの状態管理: {username: {"last_val": int, "last_changed": datetime}}
        self.states = {}
        # 詳細ログモードの終了期限（UTC）
        self.detailed_log_until = None
        
    def check_value(self, username, current_posts):
        """postsCount の変化をチェックし、30分以上変化がなければ詳細ログモードを起動"""
        if username not in self.targets or current_posts is None or current_posts == 0:
            return
            
        with self._lock:
            now = datetime.now(timezone.utc)
            state = self.states.get(username)
            
            if not state:
                # 初回登録
                self.states[username] = {
                    "last_val": current_posts,
                    "last_changed": now
                }
                return
                
            if current_posts > state["last_val"]:
                # 値が増加した -> 正常に動いているのでタイムスタンプ更新
                state["last_val"] = current_posts
                state["last_changed"] = now
            elif current_posts < state["last_val"]:
                # 値が減少した（巻き戻り検知！） -> 即座に詳細ログモード起動
                self._log(f"⚠️ [ANOMALY_DECREASE] {username} の postsCount が減少しました！ ({state['last_val']} -> {current_posts})")
                self._activate_detailed_mode("value_decreased")
                state["last_val"] = current_posts
                state["last_changed"] = now
            else:
                # 値が変化していない
                duration = (now - state["last_changed"]).total_seconds() / 60.0
                if duration >= 30.0:
                    # 30分以上変化なし -> 異常検知（詳細ログモード起動）
                    if not self.is_detailed_mode_active():
                        self._log(f"⚠️ [ANOMALY_STAGNANT] {username} の postsCount が {duration:.1f}分間 固定されています。値: {current_posts}")
                        self._activate_detailed_mode(f"fixed_duration_{int(duration)}m")

    def _activate_detailed_mode(self, reason):
        now = datetime.now(timezone.utc)
        self.detailed_log_until = now + timedelta(hours=1)
        self._log(f"🔥 [DETAILED_LOG_MODE_START] 詳細ログモードを1時間有効にします。理由: {reason} (有効期限: {self.detailed_log_until.isoformat()})")

    def is_detailed_mode_active(self):
        if not self.detailed_log_until:
            return False
        now = datetime.now(timezone.utc)
        if now < self.detailed_log_until:
            return True
        self.detailed_log_until = None
        return False

    def _log(self, message):
        print(json.dumps({
            "event": "ANOMALY_EVENT",
            "time": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "message": message
        }), flush=True)

    def trace(self, event_name, caller, cache_obj=None, extra=None):
        """詳細ログモードが有効な場合、現在の対象ユーザーの値をJSONでトレース出力"""
        if not self.is_detailed_mode_active():
            return
            
        zc_val = None
        gotoh_val = None
        miyaaa_val = None
        
        if cache_obj:
            # cache は RankingCache インスタンスか、ユーザー辞書自体を想定
            users = cache_obj.users if hasattr(cache_obj, "users") else cache_obj
            if isinstance(users, dict):
                zc_val = users.get("zc", {}).get("postsCount")
                gotoh_val = users.get("gotoh", {}).get("postsCount")
                miyaaa_val = users.get("miyaaa_96", {}).get("postsCount")
        
        log_entry = {
            "event": f"TRACE_{event_name}",
            "time": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "caller": caller,
            "zc": zc_val,
            "gotoh": gotoh_val,
            "miyaaa_96": miyaaa_val,
        }
        if extra:
            log_entry["extra"] = extra
            
        print(json.dumps(log_entry), flush=True)

# シングルトンインスタンス
detector = AnomalyDetector()

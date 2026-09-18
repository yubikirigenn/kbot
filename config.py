# -*- coding: utf-8 -*-
"""kbot 設定管理"""
import os
try:
    from dotenv import load_dotenv
except ImportError:  # テスト環境など、環境変数だけで動かす場合
    def load_dotenv():
        return False

load_dotenv()

# === アカウント設定 ===
USERNAME = os.getenv("KBOT_USERNAME", "kbot")
PASSWORD = os.getenv("KBOT_PASSWORD", "")
KAROTTER_API_KEY = os.getenv("KAROTTER_API_KEY", "")
KAROTTER_ACCOUNTS = os.getenv("KAROTTER_ACCOUNTS", "")  # カンマ区切りの user:pass リスト
# 安全側を既定値にする。実運用で返信を有効化する時だけ false を明示する。
DISABLE_KAROTTER_WRITES = os.getenv("KBOT_DISABLE_WRITES", "true").lower() != "false"

# === API URL ===
KAROTTER_INTERNAL_URL = "https://api.karotter.com/api"
KAROTTER_DEV_API_URL = "https://karotter.karon.jp/api/developer"

# === コマンドエイリアス ===
COMMAND_ALIASES = {
    "rt": "rate",
    "ps": "posts",
    "flw": "followers",
    "rrt": "ranking_rate",
    "rps": "ranking_posts",
    "rflw": "ranking_followers",
    "rate": "rate",
    "posts": "posts",
    "followers": "followers",
    "ranking rate": "ranking_rate",
    "ranking posts": "ranking_posts",
    "ranking followers": "ranking_followers",
    "ranking": "ranking_help",
}

# === 動作設定 ===
POLL_INTERVAL = 5           # 通知ポーリング間隔（秒）
API_SLEEP = 2.5             # APIリクエスト間隔（秒）
USER_CACHE_FILE = "data/users_cache.json"
EXCLUDED_USERS_FILE = "data/excluded_users.json"
SEEN_FILE = "data/seen_notifications.txt"
CACHE_UPDATE_INTERVAL = 60  # ユーザーキャッシュ更新間隔（秒）= 1分
HISTORY_MAX_SAMPLE_AGE_HOURS = float(os.getenv("HISTORY_MAX_SAMPLE_AGE_HOURS", "3"))

# === ハッシュタグ ===
HASHTAG = "#kbot"
BOT_MENTION = f"@{USERNAME}"

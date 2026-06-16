# -*- coding: utf-8 -*-
"""kbot 設定管理"""
import os
from dotenv import load_dotenv

load_dotenv()

# === アカウント設定 ===
USERNAME = os.getenv("KBOT_USERNAME", "kbot")
PASSWORD = os.getenv("KBOT_PASSWORD", "")
KAROTTER_API_KEY = os.getenv("KAROTTER_API_KEY", "")

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
SEEN_FILE = "data/seen_notifications.txt"
CACHE_UPDATE_INTERVAL = 60  # ユーザーキャッシュ更新間隔（秒）= 1分

# === ハッシュタグ ===
HASHTAG = "#kbot"
BOT_MENTION = f"@{USERNAME}"

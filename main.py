# -*- coding: utf-8 -*-
"""
kbot - Karotter ランキングBot
vbot互換のコマンドベースBot

Render Free Tier (Web Service) 対応:
  - メインスレッド: HTTPサーバー（ヘルスチェック応答用）
  - サブスレッド: Bot処理（ログイン→データ収集→通知ポーリング）
"""
import os
import sys
import time
import threading
import http.server
import socketserver

from config import USERNAME, POLL_INTERVAL, CACHE_UPDATE_INTERVAL, SEEN_FILE
from api.auth import AuthManager
from api.karotter import KarotterAPI
from services.ranking_cache import RankingCache
from services.user_collector import UserCollector
from commands.dispatcher import parse_command
from commands.posts import handle_posts
from commands.rate import handle_rate
from commands.followers import handle_followers
from commands.ranking import (
    handle_ranking_posts, handle_ranking_rate,
    handle_ranking_followers, handle_ranking_help
)
from utils.formatter import format_general_info, format_ranking_help, format_error


# === グローバル状態 ===
bot_status = "starting"


# === 処理済み通知の管理 ===

def load_seen_ids():
    seen_dir = os.path.dirname(SEEN_FILE)
    if seen_dir:
        os.makedirs(seen_dir, exist_ok=True)
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_seen_id(item_id):
    if item_id:
        seen_dir = os.path.dirname(SEEN_FILE)
        if seen_dir:
            os.makedirs(seen_dir, exist_ok=True)
        with open(SEEN_FILE, "a", encoding="utf-8") as f:
            f.write(f"{item_id}\n")


# === コマンド実行 ===

def execute_command(command, author_username, api, cache, collector):
    """コマンドを実行して応答テキストを返す"""
    if command is None:
        collector.enrich_single_user(author_username)
        user_data = cache.get_user(author_username)
        if not user_data:
            return format_error(f"@{author_username} のデータを取得できませんでした。")
        posts_rank, posts_total = cache.get_ranking("posts", author_username)
        followers_rank, followers_total = cache.get_ranking("followers", author_username)
        ranks = {
            "posts": (posts_rank, posts_total),
            "followers": (followers_rank, followers_total),
        }
        return format_general_info(author_username, user_data, ranks)
    elif command == "posts":
        return handle_posts(author_username, api, cache, collector)
    elif command == "rate":
        return handle_rate(author_username, api, cache, collector)
    elif command == "followers":
        return handle_followers(author_username, api, cache, collector)
    elif command == "ranking_posts":
        return handle_ranking_posts(api, cache)
    elif command == "ranking_rate":
        return handle_ranking_rate(api, cache)
    elif command == "ranking_followers":
        return handle_ranking_followers(api, cache)
    elif command == "ranking_help":
        return handle_ranking_help()
    elif command == "unknown":
        return handle_ranking_help()
    return None


# === Bot処理スレッド ===

def bot_worker():
    """Bot本体の処理。別スレッドで実行される。"""
    global bot_status

    print("[BOT] ログイン試行中...")
    auth = AuthManager()

    # ログインを無限リトライ（Renderの初回起動時にAPI側が不安定な場合に対応）
    while True:
        if auth.login():
            print("[BOT] ログイン成功!")
            break
        print("[BOT] ログイン失敗。30秒後にリトライ...")
        time.sleep(30)

    api = KarotterAPI(auth)
    cache = RankingCache()
    collector = UserCollector(api, cache)
    seen_ids = load_seen_ids()

    # 初回データ収集（別スレッドで実行し、メンション監視をブロックしない）
    def initial_collection():
        print("[BOT] 初回ユーザーデータ収集をバックグラウンドで開始...")
        try:
            collector.full_collect()
        except Exception as e:
            print(f"[BOT] 初回収集でエラー（続行します）: {e}")

    collection_thread = threading.Thread(target=initial_collection, daemon=True)
    collection_thread.start()

    bot_status = "running"
    print(f"[BOT] 稼働開始！通知ポーリング間隔: {POLL_INTERVAL}秒")

    # 最後にインクリメンタル更新を行った時刻
    last_update_time = time.time()
    loop_count = 0

    while True:
        try:
            auth.ensure_login()
            loop_count += 1

            if loop_count % 60 == 0:
                print(f"[BOT] 監視中... (キャッシュ: {cache.user_count()}ユーザー)")

            # 定期的にインクリメンタル更新
            if time.time() - last_update_time > CACHE_UPDATE_INTERVAL:
                try:
                    collector.incremental_update()
                except Exception as e:
                    print(f"[BOT] インクリメンタル更新エラー: {e}")
                last_update_time = time.time()

            # 通知を取得
            notifications = api.get_notifications()

            for n in notifications:
                if not isinstance(n, dict):
                    continue

                notification_type = str(n.get("type", "")).upper()
                if notification_type not in ("MENTION", "REPLY"):
                    continue

                post_data = n.get("post") or {}
                content = post_data.get("content", "")
                post_id = str(n.get("postId") or post_data.get("id") or "")

                if post_id in seen_ids or not post_id:
                    continue

                # メンション確認
                if f"@{USERNAME}" not in content.lower() and notification_type != "REPLY":
                    seen_ids.add(post_id)
                    save_seen_id(post_id)
                    continue

                # 投稿者
                author_data = post_data.get("author") or post_data.get("user") or {}
                author_username = str(author_data.get("username") or "unknown")

                if author_username.lower() == USERNAME.lower():
                    seen_ids.add(post_id)
                    save_seen_id(post_id)
                    continue

                print(f"[BOT] メンション受信: @{author_username} -> {content[:80]}")

                command, _ = parse_command(content)
                print(f"[BOT] コマンド: {command or '(総合情報)'}")

                response = execute_command(command, author_username, api, cache, collector)
                if response:
                    api.post_reply(response, post_id)
                    print(f"[BOT] 返信完了")

                seen_ids.add(post_id)
                save_seen_id(post_id)

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[BOT] メインループエラー: {e}")
            time.sleep(10)


# === HTTPサーバー（メインスレッド） ===

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """Renderヘルスチェック用のHTTPハンドラ"""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"kbot is {bot_status}".encode("utf-8"))

    def log_message(self, format, *args):
        # ヘルスチェックのアクセスログを抑制（ログが埋まるのを防ぐ）
        pass


def main():
    print("=" * 50)
    print(f"kbot starting... (@{USERNAME})")
    print("=" * 50)

    # Bot処理をバックグラウンドスレッドで起動
    bot_thread = threading.Thread(target=bot_worker, daemon=True)
    bot_thread.start()

    # HTTPサーバーをメインスレッドで起動（Renderヘルスチェック対応）
    port = int(os.environ.get("PORT", 8080))
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        print(f"HTTP server listening on port {port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

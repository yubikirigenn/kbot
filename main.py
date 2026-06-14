# -*- coding: utf-8 -*-
"""
kbot - Karotter ランキングBot
vbot互換のコマンドベースBot
"""
import os
import sys
import io
import time
import threading

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
import http.server
import socketserver

# === ダミーWebサーバー（Render Free Tier対策） ===
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    # 単純な200 OKを返すハンドラ
    class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"kbot is running!")
            
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        print(f"🌐 ダミーWebサーバー起動 (ポート {port})")
        httpd.serve_forever()

# === 処理済み通知の管理 ===

def load_seen_ids():
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_seen_id(item_id):
    if item_id:
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        with open(SEEN_FILE, "a", encoding="utf-8") as f:
            f.write(f"{item_id}\n")


# === コマンド実行 ===

def execute_command(command, author_username, api, cache, collector):
    """コマンドを実行して応答テキストを返す"""
    if command is None:
        # コマンドなし → 総合情報表示
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


# === バックグラウンドでユーザーデータ更新 ===

def background_collector(collector, stop_event):
    """バックグラウンドでユーザーデータを定期更新"""
    while not stop_event.is_set():
        try:
            collector.incremental_update()
        except Exception as e:
            print(f"⚠️ バックグラウンド更新エラー: {e}")
        stop_event.wait(CACHE_UPDATE_INTERVAL)


# === メインループ ===

def main():
    print("=" * 50)
    print(f"🤖 kbot 起動中... (@{USERNAME})")
    print("=" * 50)
    
    # ダミーWebサーバーを別スレッドで起動
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    # 初期化
    auth = AuthManager()
    if not auth.login():
        print("❌ ログインに失敗しました。終了します。")
        sys.exit(1)

    api = KarotterAPI(auth)
    cache = RankingCache()
    collector = UserCollector(api, cache)
    seen_ids = load_seen_ids()

    # 初回ユーザーデータ収集
    print("\n📊 初回ユーザーデータ収集を開始...")
    collector.full_collect()

    # バックグラウンドで定期更新を開始
    stop_event = threading.Event()
    bg_thread = threading.Thread(
        target=background_collector,
        args=(collector, stop_event),
        daemon=True
    )
    bg_thread.start()
    print("🔄 バックグラウンド更新スレッドを開始しました")

    print(f"\n✅ kbot 稼働開始！通知をポーリングします (間隔: {POLL_INTERVAL}秒)")
    print("=" * 50)

    loop_count = 0

    while True:
        try:
            # 定期的に再ログイン
            auth.ensure_login()

            loop_count += 1
            if loop_count % 60 == 0:
                print(f"👀 監視中... (キャッシュ: {cache.user_count()}ユーザー)")

            # 通知を取得
            notifications = api.get_notifications()

            for n in notifications:
                if not isinstance(n, dict):
                    continue

                notification_type = str(n.get("type", "")).upper()

                # メンション以外は無視
                if notification_type not in ("MENTION", "REPLY"):
                    continue

                post_data = n.get("post") or {}
                content = post_data.get("content", "")
                post_id = str(n.get("postId") or post_data.get("id") or "")

                # 処理済みチェック
                if post_id in seen_ids or not post_id:
                    continue

                # 自分のメンションが含まれているか確認
                if f"@{USERNAME}" not in content.lower() and notification_type != "REPLY":
                    seen_ids.add(post_id)
                    save_seen_id(post_id)
                    continue

                # 投稿者情報
                author_data = post_data.get("author") or post_data.get("user") or {}
                author_username = str(author_data.get("username") or "unknown")

                # 自分自身は無視
                if author_username.lower() == USERNAME.lower():
                    seen_ids.add(post_id)
                    save_seen_id(post_id)
                    continue

                print(f"\n📩 メンション受信: @{author_username} -> {content[:80]}")

                # コマンド解析
                command, _ = parse_command(content)
                print(f"  コマンド: {command or '(なし→総合情報)'}")

                # コマンド実行
                response = execute_command(command, author_username, api, cache, collector)

                if response:
                    # 返信投稿
                    api.post_reply(response, post_id)
                    print(f"  ✅ 返信完了")

                # 処理済みとして記録
                seen_ids.add(post_id)
                save_seen_id(post_id)

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n\n🛑 kbot を停止します...")
            stop_event.set()
            bg_thread.join(timeout=5)
            cache.save()
            print("✅ 正常終了")
            break
        except Exception as e:
            print(f"⚠️ メインループエラー: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()

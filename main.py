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

# Render等でのログ遅延を防ぐため、標準出力を強制的にアンバッファリング（ラインバッファ）する
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

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


# === GitHub キャッシュ永続化 ===

def _download_file_from_github(repo, token, filepath, dest_path):
    import urllib.request, json, base64, os
    try:
        api_url = f"https://api.github.com/repos/{repo}/contents/{filepath}?ref=cache"
        req = urllib.request.Request(
            api_url,
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            file_info = json.loads(resp.read().decode("utf-8"))
        
        content_b64 = file_info.get("content", "")
        data = json.loads(base64.b64decode(content_b64).decode("utf-8"))
        if data:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[CACHE] Restored {filepath}")
            return data
    except Exception as e:
        print(f"[CACHE] Failed to restore {filepath}: {e}")
    return None

def restore_cache_from_github():
    """GitHub cache ブランチからキャッシュファイルをダウンロードして復元"""
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo = os.environ.get("GITHUB_REPO", "").strip()
    if not github_token or not github_repo:
        print("[CACHE] GITHUB_TOKEN/GITHUB_REPO not set, skipping restore")
        return False

    print(f"[CACHE] Restoring data from GitHub ({github_repo})...")
    
    # ユーザーキャッシュ
    users_data = _download_file_from_github(github_repo, github_token, "data/users_cache.json", "data/users_cache.json")
    
    # 日間・週間スナップショット
    _download_file_from_github(github_repo, github_token, "data/history_daily.json", "data/history_daily.json")
    _download_file_from_github(github_repo, github_token, "data/history_weekly.json", "data/history_weekly.json")

    return bool(users_data)


def _upload_file_to_github(repo, token, filepath, source_path, message):
    import urllib.request, json, base64, os
    if not os.path.exists(source_path):
        return False
        
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        api_url = f"https://api.github.com/repos/{repo}/contents/{filepath}"

        sha = None
        try:
            req = urllib.request.Request(
                f"{api_url}?ref=cache",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                sha = json.loads(resp.read().decode("utf-8")).get("sha")
        except Exception:
            pass

        payload = {"message": message, "content": content_b64, "branch": "cache"}
        if sha: payload["sha"] = sha

        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json", "Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            pass
        return True
    except Exception as e:
        print(f"[CACHE] Failed to backup {filepath}: {e}")
        return False

def backup_cache_to_github(cache):
    """GitHub API を使って cache ブランチにキャッシュファイルをバックアップ"""
    import os
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo = os.environ.get("GITHUB_REPO", "").strip()
    if not github_token or not github_repo:
        return False

    cache.save()
    
    success = False
    if _upload_file_to_github(github_repo, github_token, "data/users_cache.json", "data/users_cache.json", f"[auto] Cache backup ({cache.user_count()} users)"):
        success = True
    
    _upload_file_to_github(github_repo, github_token, "data/history_daily.json", "data/history_daily.json", "[auto] Daily history backup")
    _upload_file_to_github(github_repo, github_token, "data/history_weekly.json", "data/history_weekly.json", "[auto] Weekly history backup")

    return success


# === コマンド実行 ===

def execute_command(parsed, author_username, api, cache, collector, history_manager=None):
    """
    コマンドを実行して (応答テキスト, media_urls or None) を返す。
    """
    command = parsed.get("cmd")
    target_username = parsed.get("target")

    # 対象ユーザーのリアルタイム情報を取得（指定ユーザーまたは送信者本人）
    effective_user = target_username or author_username

    if command is None:
        # 総合情報表示
        if not collector.enrich_single_user(effective_user):
            # リアルタイム取得に失敗 → キャッシュデータがあればそれを使う
            user_data = cache.get_user(effective_user)
            if not user_data:
                return format_error(f"@{effective_user} のデータを取得できませんでした。"), None
            # キャッシュから返す旨をメッセージに含める（取得失敗時のみ）
        
        user_data = cache.get_user(effective_user)
        if not user_data:
            return format_error(f"@{effective_user} のデータを取得できませんでした。"), None
        posts_rank, posts_total = cache.get_ranking("posts", effective_user)
        followers_rank, followers_total = cache.get_ranking("followers", effective_user)
        ranks = {
            "posts": (posts_rank, posts_total),
            "followers": (followers_rank, followers_total),
        }
        return format_general_info(effective_user, user_data, ranks), None

    elif command == "posts":
        return handle_posts(effective_user, api, cache, collector), None
    elif command == "rate":
        return handle_rate(effective_user, api, cache, collector), None
    elif command == "followers":
        return handle_followers(effective_user, api, cache, collector), None
    elif command == "ranking_posts":
        return handle_ranking_posts(api, cache, parsed, history_manager)
    elif command == "ranking_rate":
        return handle_ranking_rate(api, cache, parsed, history_manager)
    elif command == "ranking_followers":
        return handle_ranking_followers(api, cache, parsed, history_manager)
    elif command == "compare":
        from commands.compare import handle_compare
        return handle_compare(api, cache, collector, parsed)
    elif command == "ranking_help":
        return handle_ranking_help()
    elif command == "unknown":
        return handle_ranking_help()
    return None, None


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

    # 収集用に別のAPIインスタンスを作成（スロットリングが干渉しないように）
    collector_auth = AuthManager()
    collector_auth.login()
    collector_api = KarotterAPI(collector_auth)

    # GitHubからキャッシュを復元（再起動時のゼロダウンタイム化）
    restore_cache_from_github()

    cache = RankingCache()
    
    from services.history_manager import HistoryManager
    history_manager = HistoryManager()
    
    collector = UserCollector(collector_api, cache)  # 収集用APIを使用
    seen_ids = load_seen_ids()

    # キャッシュに既にデータがあれば即座に稼働開始、バックグラウンドで更新
    if cache.user_count() > 50:
        bot_status = "running"
        print(f"[BOT] キャッシュから {cache.user_count()} ユーザーを復元済み。即座に稼働開始！")
    else:
        bot_status = "collecting"

    # 初回データ収集（別スレッドで実行し、メンション監視をブロックしない）
    def initial_collection():
        global bot_status
        if cache.user_count() > 50:
            print("[BOT] 十分なキャッシュがあるため、起動時の全体収集をスキップします。")
            bot_status = "running"
            return

        print("[BOT] キャッシュが少ないため、ユーザーデータの全体収集をバックグラウンドで開始...")
        try:
            collector.full_collect()
        except Exception as e:
            print(f"[BOT] 収集でエラー（続行します）: {e}")
        finally:
            bot_status = "running"
            print("[BOT] データ収集が完了しました！")

    collection_thread = threading.Thread(target=initial_collection, daemon=True)
    collection_thread.start()

    # === 過去の通知を無視するための初期化 ===
    print("[BOT] 過去の通知履歴をスキップしています...")
    initial_notifications = api.get_notifications(limit=30)
    for n in initial_notifications:
        if isinstance(n, dict):
            post_id = str(n.get("postId") or (n.get("post") or {}).get("id") or "")
            if post_id:
                seen_ids.add(post_id)
                save_seen_id(post_id)
    print(f"[BOT] 過去の通知 {len(initial_notifications)}件 をスキップしました。")

    print(f"[BOT] 稼働開始！通知ポーリング間隔: {POLL_INTERVAL}秒")

    # 最後にインクリメンタル更新を行った時刻
    last_update_time = time.time()
    last_backup_time = time.time()
    loop_count = 0
    BACKUP_INTERVAL = 3600  # 1時間ごとにGitHubにバックアップ

    while True:
        try:
            auth.ensure_login()
            loop_count += 1

            if loop_count % 60 == 0:
                print(f"[BOT] 監視中... (キャッシュ: {cache.user_count()}ユーザー)")

            # 定期的にインクリメンタル更新（別スレッドで実行しポーリングをブロックしない）
            if time.time() - last_update_time > CACHE_UPDATE_INTERVAL:
                last_update_time = time.time()
                def run_update():
                    try:
                        collector.incremental_update()
                    except Exception as e:
                        print(f"[BOT] インクリメンタル更新エラー: {e}")
                threading.Thread(target=run_update, daemon=True).start()

            # 定期的にGitHubにバックアップ（別スレッド）
            if time.time() - last_backup_time > BACKUP_INTERVAL:
                last_backup_time = time.time()
                def run_backup():
                    try:
                        backup_cache_to_github(cache)
                    except Exception as e:
                        print(f"[BOT] バックアップエラー: {e}")
                threading.Thread(target=run_backup, daemon=True).start()

            # 日間・週間スナップショットの更新チェック
            from datetime import datetime, timezone, timedelta
            jst = timezone(timedelta(hours=9))
            now_jst = datetime.now(jst)
            
            # 日間チェック（日付が変わっていたらスナップショット更新）
            if history_manager.daily_timestamp:
                try:
                    last_daily_dt = datetime.fromisoformat(history_manager.daily_timestamp).astimezone(jst)
                    if last_daily_dt.date() < now_jst.date():
                        history_manager.save_snapshot(cache, "day")
                except Exception:
                    pass
            elif cache.user_count() > 0:
                history_manager.save_snapshot(cache, "day")
                
            # 週間チェック（月曜日になっていて、かつ週が新しければ更新）
            if history_manager.weekly_timestamp:
                try:
                    last_weekly_dt = datetime.fromisoformat(history_manager.weekly_timestamp).astimezone(jst)
                    # iso年とiso週番号を比較
                    if last_weekly_dt.isocalendar()[:2] < now_jst.isocalendar()[:2]:
                        history_manager.save_snapshot(cache, "week")
                except Exception:
                    pass
            elif cache.user_count() > 0:
                history_manager.save_snapshot(cache, "week")

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
                if f"@{USERNAME.lower()}" not in content.lower():
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

                # キャッシュが完全に空の場合のみ収集中メッセージを返す
                if cache.user_count() == 0:
                    print(f"[BOT] データ未収集のためメッセージ返信: @{author_username}")
                    api.post_reply("現在ランキングデータを収集中です。もうしばらくお待ちください！🙇‍♂️ #kbot", post_id)
                    seen_ids.add(post_id)
                    save_seen_id(post_id)
                    continue

                parsed = parse_command(content)
                command = parsed.get("cmd")
                target_user = parsed.get("target")
                
                print(f"[BOT] コマンド: {command or '(総合情報)'}, パラメータ: {parsed}")

                result = execute_command(parsed, author_username, api, cache, collector, history_manager)
                if result:
                    response_text, media_files = result
                    if response_text:
                        api.post_reply(response_text, post_id, media_files=media_files)
                        print(f"[BOT] 返信完了 (画像: {'あり' if media_files else 'なし'})")

                seen_ids.add(post_id)
                save_seen_id(post_id)

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[BOT] メインループエラー: {e}")
            time.sleep(10)


# === HTTPサーバー（メインスレッド） ===

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """Renderヘルスチェック + 画像配信用のHTTPハンドラ"""

    def do_GET(self):
        if self.path.startswith("/images/"):
            # 画像配信
            filename = self.path.split("/images/", 1)[1].split("?")[0]
            from api.karotter import KarotterAPI
            image_data = KarotterAPI.get_image(filename)
            if image_data:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(image_data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(image_data)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Image not found")
        else:
            # ヘルスチェック
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"kbot is {bot_status}".encode("utf-8"))

    def do_HEAD(self):
        if self.path.startswith("/images/"):
            filename = self.path.split("/images/", 1)[1].split("?")[0]
            from api.karotter import KarotterAPI
            image_data = KarotterAPI.get_image(filename)
            if image_data:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(image_data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()

    def log_message(self, format, *args):
        # アクセスログをすべて抑制するか、安全にチェックする
        # HTTPStatusオブジェクトが渡されることがあるため、文字列化してチェック
        try:
            req_line = str(args[0]) if args else ""
            if "/images/" in req_line:
                return
        except Exception:
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

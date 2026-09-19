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
from datetime import datetime, timezone

# Render等でのログ遅延を防ぐため、標準出力を強制的にアンバッファリング（ラインバッファ）する
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

from config import (
    USERNAME,
    POLL_INTERVAL,
    CACHE_UPDATE_INTERVAL,
    SEEN_FILE,
    DISABLE_KAROTTER_WRITES,
)
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
_backup_lock = threading.Lock()


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


def notification_post_id(notification):
    """Return the post ID while accepting both notification API shapes."""
    if not isinstance(notification, dict):
        return ""
    post = notification.get("post") or {}
    return str(notification.get("postId") or post.get("id") or "")


def notification_is_from_before_startup(notification, startup_time):
    """Keep a deployment from replaying notifications that predate its start.

    An unknown timestamp is skipped as well: avoiding a possible duplicate
    reply takes precedence over guessing that an unparseable item is new.
    """
    if not isinstance(notification, dict):
        return True
    post = notification.get("post") or {}
    raw_timestamp = notification.get("createdAt") or post.get("createdAt")
    if not raw_timestamp:
        return True
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if startup_time.tzinfo is None:
            startup_time = startup_time.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) <= startup_time.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return True


def mark_notification_seen(seen_ids, post_id):
    """Persist a notification only after it has been handled intentionally."""
    if post_id and post_id not in seen_ids:
        seen_ids.add(post_id)
        save_seen_id(post_id)


def claim_notification_for_reply(post_id):
    """Atomically reserve a reply notification across Render instances.

    The local seen file protects one process only.  Render deployments can
    briefly overlap, and an accidentally duplicated service has a separate
    filesystem altogether.  The cache branch is shared by those instances, so
    use GitHub's file SHA as a compare-and-swap lock before sending a reply.

    Returns True for a new claim, False when another instance already claimed
    it, and None when claiming could not be verified.  Only the False case may
    be marked handled without sending from this process.
    """
    if DISABLE_KAROTTER_WRITES:
        print(f"[BOT] Reply claim skipped because KBOT_DISABLE_WRITES is enabled: {post_id}")
        return None

    import base64
    import json
    import urllib.error
    import urllib.request

    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo = os.environ.get("GITHUB_REPO", "").strip()
    if not github_token or not github_repo:
        print("[BOT] Shared reply lock unavailable: GITHUB_TOKEN/GITHUB_REPO is not set")
        # 二重返信を避けるため、共有予約を確認できない時は発信しない。
        return None

    post_id = str(post_id)
    lock_path = "data/reply_claims.json"
    api_url = f"https://api.github.com/repos/{github_repo}/contents/{lock_path}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    saw_conflict = False

    for attempt in range(3):
        claims = []
        sha = None
        try:
            req = urllib.request.Request(f"{api_url}?ref=cache", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                file_info = json.loads(response.read().decode("utf-8"))
            sha = file_info.get("sha")
            raw = base64.b64decode(file_info.get("content", "")).decode("utf-8")
            saved = json.loads(raw)
            claims = [str(item) for item in saved.get("post_ids", [])]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                print(f"[BOT] Shared reply lock read failed: HTTP {error.code}")
                return None
        except Exception as error:
            print(f"[BOT] Shared reply lock read failed: {error}")
            return None

        if post_id in claims:
            print(f"[BOT] Reply already claimed by another instance: {post_id}")
            return False

        # Keep the remote lock small while retaining enough history for a
        # restart or a temporary deployment overlap.
        new_claims = (claims + [post_id])[-2000:]
        payload = {
            "message": f"[kbot] claim reply notification {post_id}",
            "content": base64.b64encode(
                json.dumps({"post_ids": new_claims}, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
            "branch": "cache",
        }
        if sha:
            payload["sha"] = sha

        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={**headers, "Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
            print(f"[BOT] Reply claimed: {post_id}")
            return True
        except urllib.error.HTTPError as error:
            if error.code in (409, 422):
                saw_conflict = True
                time.sleep(0.3 * (attempt + 1))
                continue
            print(f"[BOT] Shared reply lock write failed: HTTP {error.code}")
            return None
        except Exception as error:
            print(f"[BOT] Shared reply lock write failed: {error}")
            return None

    if saw_conflict:
        # A concurrent instance changed the file repeatedly.  Skipping is
        # safer than risking a duplicate reply; the user can mention again if
        # that instance later fails before posting.
        print(f"[BOT] Reply claim conflict; skipping to prevent duplicates: {post_id}")
        return None
    return None


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
    if os.environ.get("DISABLE_GITHUB_CACHE", "").lower() == "true":
        print("[CACHE] DISABLE_GITHUB_CACHE is set to true, skipping restore")
        return False

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
    if not _backup_lock.acquire(blocking=False):
        print("[CACHE] Backup already running; skipping overlapping backup")
        return False
    try:
        return _backup_cache_to_github_locked(cache)
    finally:
        _backup_lock.release()


def _backup_cache_to_github_locked(cache):
    """重複実行を排除した実際のバックアップ処理。"""
    if os.environ.get("DISABLE_GITHUB_CACHE", "").lower() == "true":
        print("[CACHE] DISABLE_GITHUB_CACHE is set to true, skipping backup")
        return False

    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo = os.environ.get("GITHUB_REPO", "").strip()
    if not github_token or not github_repo:
        return False

    from utils.anomaly_detector import detector
    detector.trace("GITHUB_BACKUP_BEFORE", "backup_cache_to_github", cache_obj=cache)

    cache.save()
    
    cache_ok = _upload_file_to_github(github_repo, github_token, "data/users_cache.json", "data/users_cache.json", f"[auto] Cache backup ({cache.user_count()} users)")
    daily_ok = _upload_file_to_github(github_repo, github_token, "data/history_daily.json", "data/history_daily.json", "[auto] Daily history backup")
    weekly_ok = _upload_file_to_github(github_repo, github_token, "data/history_weekly.json", "data/history_weekly.json", "[auto] Weekly history backup")
    success = cache_ok and daily_ok and weekly_ok

    detector.trace("GITHUB_BACKUP_AFTER", "backup_cache_to_github", cache_obj=cache, extra={"success": success})
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

    # コマンドの種類に関わらず、常に対象ユーザーの最新データを取得
    enrich_success = collector.enrich_single_user(effective_user)
    if isinstance(enrich_success, str) and enrich_success:
        effective_user = enrich_success
        if target_username:
            parsed = dict(parsed)
            parsed["target"] = effective_user

    if command is None:
        # 総合情報表示
        if not enrich_success:
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
    notification_started_at = datetime.now(timezone.utc)

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

    # 収集用にメインアカウントのAPIインスタンスを作成（優先更新専用）
    priority_apis = []
    collector_auth = AuthManager()
    if collector_auth.login():
        priority_apis.append(KarotterAPI(collector_auth))
        print("[BOT] 優先収集用メインアカウント ログイン成功")
    else:
        print("[BOT] 優先収集用メインアカウント ログイン失敗。通知監視用APIは収集に流用しません。")
    
    # さらにKAROTTER_ACCOUNTSが設定されていれば、サブアカウントを一般更新専用ワーカーとして追加
    from config import KAROTTER_ACCOUNTS
    normal_apis = []
    if KAROTTER_ACCOUNTS:
        accounts = [acc.strip() for acc in KAROTTER_ACCOUNTS.split(",") if ":" in acc]
        for i, acc in enumerate(accounts):
            u, p = acc.split(":", 1)
            c_auth = AuthManager(username=u, password=p)
            if c_auth.login():
                normal_apis.append(KarotterAPI(c_auth))
                print(f"[BOT] 一般収集用サブアカウント {i+1} ログイン成功 (@{u})")
            else:
                print(f"[BOT] 一般収集用サブアカウント {i+1} ログイン失敗 (@{u})")

    # GitHubからキャッシュを復元（再起動時のゼロダウンタイム化）
    restore_cache_from_github()

    cache = RankingCache()
    
    from utils.anomaly_detector import detector
    detector.trace("GITHUB_RESTORE", "bot_worker_init", cache_obj=cache)
    
    from services.history_manager import HistoryManager
    history_manager = HistoryManager()
    
    collector = UserCollector(priority_apis, normal_apis, cache, history_manager)  # 役割分離したAPIプールを使用



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
            collector.update_priority_users()
            collector.update_normal_users()
        except Exception as e:
            print(f"[BOT] 収集でエラー（続行します）: {e}")
        finally:
            bot_status = "running"
            print("[BOT] データ収集が完了しました！")

    collection_thread = threading.Thread(target=initial_collection, daemon=True)
    collection_thread.start()

    # 起動処理中に届いた新規通知も通常処理する一方、起動前の通知は再送しない。
    deferred_notification_ids = set()
    print(f"[BOT] 稼働開始！通知ポーリング間隔: {POLL_INTERVAL}秒")

    # 最後にインクリメンタル更新を行った時刻
    last_update_time = time.time()
    last_backup_time = time.time()
    loop_count = 0
    BACKUP_INTERVAL = 3600  # 1時間ごとにGitHubにバックアップ

    while True:
        try:
            from utils.anomaly_detector import detector
            for target in detector.targets:
                t_data = cache.get_user(target) or {}
                posts = t_data.get("postsCount")
                detector.check_value(target, posts)

            auth.ensure_login()
            loop_count += 1

            if loop_count % 60 == 0:
                print(f"[BOT] 監視中... (キャッシュ: {cache.user_count()}ユーザー)")

            # 定期的にインクリメンタル更新（別スレッドで実行しポーリングをブロックしない）
            if time.time() - last_update_time > CACHE_UPDATE_INTERVAL:
                last_update_time = time.time()
                
                def run_priority():
                    try:
                        for api in collector.priority_api_pool:
                            api.auth.ensure_login()
                        collector.update_priority_users()
                    except Exception as e:
                        print(f"[BOT] 優先更新エラー: {e}")
                        
                def run_normal():
                    try:
                        for api in (collector.normal_api_pool or collector.priority_api_pool):
                            api.auth.ensure_login()
                        collector.update_normal_users()
                    except Exception as e:
                        print(f"[BOT] 一般更新エラー: {e}")
                        
                threading.Thread(target=run_priority, daemon=True).start()
                if collector.normal_api_pool or collector.priority_api_pool:
                    threading.Thread(target=run_normal, daemon=True).start()

            # 定期的にGitHubにバックアップ（別スレッド）
            if time.time() - last_backup_time > BACKUP_INTERVAL:
                last_backup_time = time.time()
                def run_backup():
                    try:
                        success = backup_cache_to_github(cache)
                        if not success:
                            import sys
                            print("[ERROR] GITHUB BACKUP FAILED", file=sys.stderr, flush=True)
                    except Exception as e:
                        import sys
                        print(f"[ERROR] GITHUB BACKUP FAILED: {e}", file=sys.stderr, flush=True)
                threading.Thread(target=run_backup, daemon=True).start()

            # 日間・週間スナップショットの更新チェック
            from datetime import datetime, timezone, timedelta
            jst = timezone(timedelta(hours=9))
            now_jst = datetime.now(jst)
            
            # 日間/週間スナップショットの更新が必要かどうかのフラグ
            need_daily_snapshot = False
            need_weekly_snapshot = False
            
            if getattr(history_manager, "daily_schema_version", 1) < 2 and cache.user_count() > 0:
                need_daily_snapshot = True
            elif history_manager.daily_timestamp:
                try:
                    last_daily_dt = datetime.fromisoformat(history_manager.daily_timestamp).astimezone(jst)
                    if last_daily_dt.date() < now_jst.date():
                        need_daily_snapshot = True
                except Exception:
                    pass
            elif cache.user_count() > 0:
                need_daily_snapshot = True
                
            if getattr(history_manager, "weekly_schema_version", 1) < 2 and cache.user_count() > 0:
                need_weekly_snapshot = True
            elif history_manager.weekly_timestamp:
                try:
                    last_weekly_dt = datetime.fromisoformat(history_manager.weekly_timestamp).astimezone(jst)
                    if last_weekly_dt.isocalendar()[:2] < now_jst.isocalendar()[:2]:
                        need_weekly_snapshot = True
                except Exception:
                    pass
            elif cache.user_count() > 0:
                need_weekly_snapshot = True
                
            # 更新が必要なスナップショットがあれば、保存する前に同期更新を実行
            snapshot_updated = False
            if need_daily_snapshot or need_weekly_snapshot:
                try:
                    collector.enrich_top_users_for_snapshot()
                except Exception as e:
                    print(f"[BOT] スナップショット保存前の同期更新でエラー（続行します）: {e}")
                
                if need_daily_snapshot:
                    try:
                        history_manager.save_snapshot(cache, "day")
                        snapshot_updated = True
                    except Exception as e:
                        import sys
                        print(f"[FATAL] SNAPSHOT SAVE ERROR: {e}", file=sys.stderr, flush=True)
                        
                if need_weekly_snapshot:
                    try:
                        history_manager.save_snapshot(cache, "week")
                        snapshot_updated = True
                    except Exception as e:
                        import sys
                        print(f"[FATAL] SNAPSHOT SAVE ERROR: {e}", file=sys.stderr, flush=True)

            if snapshot_updated:
                def run_snapshot_backup():
                    try:
                        success = backup_cache_to_github(cache)
                        if not success:
                            import sys
                            print("[ERROR] GITHUB BACKUP FAILED", file=sys.stderr, flush=True)
                        else:
                            print("[BOT] スナップショットを即時バックアップしました。")
                    except Exception as e:
                        import sys
                        print(f"[ERROR] GITHUB BACKUP FAILED: {e}", file=sys.stderr, flush=True)
                threading.Thread(target=run_snapshot_backup, daemon=True).start()

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
                post_id = notification_post_id(n)

                if post_id in seen_ids or not post_id:
                    continue

                # デプロイ前の通知へ突然再返信しない。通知時刻を確認できない場合も
                # 二重返信防止を優先して処理対象外にする。
                if notification_is_from_before_startup(n, notification_started_at):
                    mark_notification_seen(seen_ids, post_id)
                    continue

                # メンション確認
                if f"@{USERNAME.lower()}" not in content.lower():
                    mark_notification_seen(seen_ids, post_id)
                    continue

                # 投稿者
                author_data = post_data.get("author") or post_data.get("user") or {}
                author_username = str(author_data.get("username") or "unknown")

                if author_username.lower() == USERNAME.lower():
                    mark_notification_seen(seen_ids, post_id)
                    continue

                # 保守・テスト用の書き込み禁止中は受信だけ行い、処理済みにしない。
                # 次回、書き込みを有効にして起動した時に期限内なら処理できる。
                if DISABLE_KAROTTER_WRITES:
                    if post_id not in deferred_notification_ids:
                        print(f"[BOT] メンション受信: @{author_username} -> {content[:80]}")
                        print(f"[BOT] 返信保留（KBOT_DISABLE_WRITES=true）: {post_id}")
                        deferred_notification_ids.add(post_id)
                    continue

                claim_result = claim_notification_for_reply(post_id)
                if claim_result is False:
                    # 共有予約済みなら、別インスタンスが担当済みなのでローカルでも完了扱い。
                    mark_notification_seen(seen_ids, post_id)
                    continue
                if claim_result is not True:
                    # 共有予約の確認不能時は発信せず、通知も消化しない。復旧後に再確認する。
                    if post_id not in deferred_notification_ids:
                        print(f"[BOT] メンション受信: @{author_username} -> {content[:80]}")
                        print(f"[BOT] 返信保留（共有予約を確認できません）: {post_id}")
                        deferred_notification_ids.add(post_id)
                    continue

                # 共有予約が取れた時点でローカルにも永続化する。以後に例外や
                # 応答不明が起きても同じメンションは自動再送しない。
                deferred_notification_ids.discard(post_id)
                mark_notification_seen(seen_ids, post_id)
                print(f"[BOT] メンション受信: @{author_username} -> {content[:80]}")

                # キャッシュが完全に空の場合のみ収集中メッセージを返す
                if cache.user_count() == 0:
                    print(f"[BOT] データ未収集のためメッセージ返信: @{author_username}")
                    if api.post_reply("現在ランキングデータを収集中です。もうしばらくお待ちください！🙇‍♂️ #kbot", post_id):
                        mark_notification_seen(seen_ids, post_id)
                    else:
                        # 通信エラー後でもサーバー側では投稿済みの可能性がある。
                        # 次回ポーリングでの二重返信を防ぐため、この通知は完了扱いにする。
                        mark_notification_seen(seen_ids, post_id)
                        print(f"[BOT] Initial reply failed; skipping retry to prevent duplicates: {post_id}")
                    continue

                parsed = parse_command(content)
                command = parsed.get("cmd")
                target_user = parsed.get("target")
                
                print(f"[BOT] コマンド: {command or '(総合情報)'}, パラメータ: {parsed}")

                result = execute_command(parsed, author_username, api, cache, collector, history_manager)
                reply_sent = True
                if result:
                    response_text, media_files = result
                    if response_text:
                        reply_sent = api.post_reply(response_text, post_id, media_files=media_files, as_rekarot=parsed.get("rekarot", False))
                        if reply_sent:
                            print(f"[BOT] 返信完了 (画像: {'あり' if media_files else 'なし'}, リカロート: {parsed.get('rekarot', False)})")

                if reply_sent:
                    mark_notification_seen(seen_ids, post_id)
                else:
                    # 応答が届かなくても、Karotter側で投稿だけ完了している場合がある。
                    # 同一メンションへの再送は二重返信になるため、自動再試行しない。
                    mark_notification_seen(seen_ids, post_id)
                    print(f"[BOT] Reply failed; skipping retry to prevent duplicates: {post_id}")

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


def bot_supervisor():
    """Restart the worker if initialization throws before its main loop."""
    global bot_status
    while True:
        try:
            bot_worker()
            print("[BOT] Worker exited unexpectedly. Restarting in 10 seconds.")
        except Exception as e:
            bot_status = "degraded"
            print(f"[BOT] Worker crashed: {e}. Restarting in 10 seconds.")
        time.sleep(10)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    print("=" * 50)
    print(f"kbot starting... (@{USERNAME})")
    print("=" * 50)

    # Bot処理をバックグラウンドスレッドで起動
    bot_thread = threading.Thread(target=bot_supervisor, name="bot-supervisor", daemon=True)
    bot_thread.start()

    # HTTPサーバーをメインスレッドで起動（Renderヘルスチェック対応）
    port = int(os.environ.get("PORT", 8080))
    with ThreadingHTTPServer(("", port), HealthCheckHandler) as httpd:
        print(f"HTTP server listening on port {port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ユーザー収集 - 検索APIでユーザーを巡回収集し、詳細データをキャッシュに保存"""
import time
import string


class UserCollector:
    def __init__(self, priority_api_pool, normal_api_pool, cache, history_manager=None):
        import threading
        import queue
        
        self.priority_api_pool = priority_api_pool if isinstance(priority_api_pool, list) else [priority_api_pool]
        self._priority_api_queue = queue.Queue()
        for api in self.priority_api_pool:
            self._priority_api_queue.put(api)

        self.normal_api_pool = normal_api_pool if isinstance(normal_api_pool, list) else [normal_api_pool]
        self._normal_api_queue = queue.Queue()
        for api in self.normal_api_pool:
            self._normal_api_queue.put(api)
            
        self.cache = cache
        self.history_manager = history_manager
        self._lock = threading.RLock()
        
        self._priority_run_lock = threading.Lock()
        self._normal_run_lock = threading.Lock()
        
        # 検索クエリ分割用
        self._search_queries = list(string.ascii_lowercase) + list(string.digits) + ["_"]
        self._search_index = 0

    def collect_from_search(self):
        """検索APIでユーザーを収集（分割して1回1クエリのみ実行）"""
        if not self._search_queries:
            return set()
            
        q = self._search_queries[self._search_index]
        self._search_index = (self._search_index + 1) % len(self._search_queries)
        
        print(f"[COLLECT] 検索APIでユーザーを収集中... (クエリ '{q}')")
        collected = set()
        
        api = self.normal_api_pool[0] if self.normal_api_pool else self.priority_api_pool[0]

        for page in range(1, 6):  # 最大5ページ
            users, pagination = api.search_users(q, limit=50, page=page)
            if not users:
                break
            for u in users:
                username = u.get("username", "")
                if username and username not in collected:
                    collected.add(username)
                    self.cache.update_user_from_search(u)
            total_pages = pagination.get("pages", 1)
            print(f"[COLLECT]   -> クエリ '{q}' ページ {page}/{total_pages} 完了 (計 {len(collected)}人発見)")
            if page >= total_pages:
                break

        self.cache.save()
        print(f"[COLLECT] 検索完了: クエリ '{q}' で {len(collected)}ユーザーを収集")
        return collected

    def collect_from_recommended(self):
        """推奨ユーザーからも収集"""
        print("[COLLECT] 推奨ユーザーを収集中...")
        api = self.normal_api_pool[0] if self.normal_api_pool else self.priority_api_pool[0]
        users = api.get_recommended_users()
        for u in users:
            username = u.get("username", "")
            if username:
                self.cache.update_user_from_search(u)
        self.cache.save()
        print(f"[COLLECT] 推奨ユーザー: {len(users)}件収集")

    def _enrich_user_details_with_pool(self, usernames, api_queue, pool_size, tag="COLLECT"):
        """
        指定されたユーザー詳細を指定されたAPIプールで並列取得
        """
        if not usernames:
            return 0
            
        print(f"[{tag}] ユーザー詳細データを取得中... ({len(usernames)}ユーザー)")
        
        import concurrent.futures
        
        enriched = 0
        total = len(usernames)
        
        def fetch_user(username):
            api = api_queue.get()
            try:
                user_data = api.get_user_detail(username)
                return username, user_data
            finally:
                api_queue.put(api)

        with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as executor:
            future_to_user = {executor.submit(fetch_user, uname): uname for uname in usernames}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_user)):
                username, user_data = future.result()
                
                with self._lock:
                    cache_before_posts = self.cache.users.get(username, {}).get("postsCount")
                    update_user_called = False
                    user_data_is_none = user_data is None
                    is_deleted = isinstance(user_data, dict) and user_data.get("is_deleted", False)

                    api_posts = None
                    if user_data and not is_deleted:
                        api_posts = user_data.get("postsCount")

                    from utils.anomaly_detector import detector
                    detector.trace("API_FETCH", f"fetch_{username}", cache_obj=self.cache, extra={
                        "target_username": username,
                        "api_posts": api_posts,
                        "user_data_is_none": user_data_is_none,
                        "is_deleted": is_deleted,
                        "tag": tag
                    })

                    if user_data:
                        if is_deleted:
                            self.cache.delete_user(username)
                        else:
                            self.cache.update_user(username, user_data)
                            enriched += 1
                            update_user_called = True
                    else:
                        pass
                    
                    cache_after_posts = self.cache.users.get(username, {}).get("postsCount") if username in self.cache.users else None

                    is_trace_target = username in ['zc', 'gotoh', 'miyaaa_96', 'DA', 'komone_neko222']
                    is_anomaly = False
                    event_type = ""

                    if user_data_is_none:
                        is_anomaly = True
                        event_type = "API_FAIL"
                    elif is_deleted:
                        is_anomaly = True
                        event_type = "USER_DELETED"
                    else:
                        if cache_after_posts != api_posts:
                            is_anomaly = True
                            event_type = "CACHE_NOT_UPDATED"
                        elif cache_before_posts is not None and api_posts is not None and api_posts < cache_before_posts:
                            is_anomaly = True
                            event_type = "API_VAL_ANOMALY"

                    if is_trace_target and not is_anomaly:
                        event_type = "TRACE"
                    
                    if is_anomaly or is_trace_target:
                        import json
                        import threading
                        from datetime import datetime, timezone
                        now_str = datetime.now(timezone.utc).isoformat()
                        log_entry = {
                            "trace_id": f"{now_str}-{username}",
                            "time": now_str,
                            "thread": threading.current_thread().name,
                            "source": tag,
                            "username": username,
                            "event": event_type,
                            "api_posts": api_posts,
                            "cache_before_posts": cache_before_posts,
                            "cache_after_posts": cache_after_posts,
                            "update_user_called": update_user_called,
                            "user_data_is_none": user_data_is_none,
                            "is_deleted": is_deleted
                        }
                        print(json.dumps(log_entry), flush=True)

                    if (i + 1) % 10 == 0:
                        self.cache.save()
                        print(f"[{tag}]   進捗: {i+1}/{total} ({enriched}件更新)")

        with self._lock:
            self.cache.save()
            
        print(f"[{tag}] 詳細データ取得完了: {enriched}/{total}件更新")
        return enriched

    def enrich_single_user(self, username):
        """特定のユーザーの詳細を即座に取得・更新し、正規化されたユーザー名を返す"""
        api = self.priority_api_pool[0]
        user_data = api.get_user_detail(username)
        if user_data:
            canonical_username = user_data.get("username", username)
            with self._lock:
                self.cache.update_user(canonical_username, user_data)
                self.cache.save()
            return canonical_username
        return username

    def update_priority_users(self):
        """上位層のユーザーを優先的に更新する（メインアカウント専用。最大15件制限）"""
        if not self._priority_run_lock.acquire(blocking=False):
            print("[PRIORITY] 既に優先更新が実行中のためスキップします。")
            return
            
        try:
            print("[PRIORITY] 優先ユーザー（上位層）の更新を開始します...")
            
            with self._lock:
                # Top30ユーザーの抽出（投稿数、フォロワー数、レート）
                top_posts = [u[0] for u in self.cache.get_top_n("posts", 30)] if hasattr(self.cache, 'get_top_n') else []
                top_followers = [u[0] for u in self.cache.get_top_n("followers", 30)] if hasattr(self.cache, 'get_top_n') else []
                top_rate = [u[0] for u in self.cache.get_top_n("rate", 30)] if hasattr(self.cache, 'get_top_n') else []
                top_users = set(top_posts + top_followers + top_rate)

                # さらに日間・週間の活動量上位ユーザーも優先更新対象に加える
                if self.history_manager:
                    try:
                        for period in ["day", "week"]:
                            deltas = self.history_manager.get_deltas(self.cache, period)
                            sorted_d = sorted(deltas.items(), key=lambda x: x[1].get("postsCount", 0), reverse=True)
                            for uname, _ in sorted_d[:30]:
                                top_users.add(uname)
                            sorted_r = sorted(deltas.items(), key=lambda x: x[1].get("rate", 0), reverse=True)
                            for uname, _ in sorted_r[:30]:
                                top_users.add(uname)
                    except Exception as e:
                        print(f"[PRIORITY] 日間/週間上位の抽出に失敗しました: {e}")

                # 優先度（更新日時 updatedAt が古い順）にソートして、上位15件のみを今回の更新対象とする
                target_users = [
                    (username, self.cache.users[username].get("updatedAt", ""))
                    for username in top_users
                    if username in self.cache.users
                ]
                target_users.sort(key=lambda x: x[1])  # updatedAt が古い順
                
                needs_enrichment = [username for username, _ in target_users[:15]]
                
            if needs_enrichment:
                self._enrich_user_details_with_pool(
                    needs_enrichment, 
                    self._priority_api_queue, 
                    len(self.priority_api_pool),
                    tag="PRIORITY"
                )
        finally:
            self._priority_run_lock.release()
            
    def update_normal_users(self):
        """一般ユーザーを地道に更新する（サブアカウント専用。データ欠損を最優先）"""
        if not self._normal_run_lock.acquire(blocking=False):
            print("[NORMAL] 既に一般更新が実行中のためスキップします。")
            return
            
        try:
            print("[NORMAL] 一般ユーザーのローテーション更新を開始します...")
            
            # 定期的な新規ユーザー検索もここで実行
            self.collect_from_search()
            self.collect_from_recommended()
            
            with self._lock:
                # 1. まずデータ欠損（createdAt/updatedAtなし）のユーザーを抽出
                missing_users = [
                    username for username, data in self.cache.users.items()
                    if not data.get("createdAt") or not data.get("updatedAt")
                ]
                
                # 2. それ以外の一般ユーザーを updatedAt が古い順に取得
                existing_users = [
                    (username, data.get("updatedAt", "")) 
                    for username, data in self.cache.users.items()
                    if username not in missing_users
                ]
                existing_users.sort(key=lambda x: x[1])
                
                # APIプールの数に応じて1度に更新する件数を決める（1アカウントあたり200件）
                rotation_count = 200 * len(self.normal_api_pool) if self.normal_api_pool else 200
                
                # 欠損ユーザーを最優先で詰め、足りない分を古いユーザーで補う
                needs_enrichment = missing_users[:rotation_count]
                if len(needs_enrichment) < rotation_count:
                    fill_count = rotation_count - len(needs_enrichment)
                    needs_enrichment.extend([username for username, _ in existing_users[:fill_count]])

            if needs_enrichment:
                # normal_api_pool が空の場合は fallback として priority を使う
                q = self._normal_api_queue if self.normal_api_pool else self._priority_api_queue
                size = len(self.normal_api_pool) if self.normal_api_pool else len(self.priority_api_pool)
                
                self._enrich_user_details_with_pool(
                    needs_enrichment, 
                    q, 
                    size,
                    tag="NORMAL"
                )
        finally:
            self._normal_run_lock.release()

    def enrich_top_users_for_snapshot(self):
        """スナップショット保存前に、最重要ユーザー（Top15）のデータを同期的に更新する"""
        print("[SNAPSHOT_SYNC] スナップショット作成前の重要ユーザー同期更新を開始...")
        
        with self._lock:
            # 投稿数、フォロワー数、レートの各上位15名の和集合を取得
            top_posts = [u[0] for u in self.cache.get_top_n("posts", 15)] if hasattr(self.cache, 'get_top_n') else []
            top_followers = [u[0] for u in self.cache.get_top_n("followers", 15)] if hasattr(self.cache, 'get_top_n') else []
            top_rate = [u[0] for u in self.cache.get_top_n("rate", 15)] if hasattr(self.cache, 'get_top_n') else []
            top_users = list(set(top_posts + top_followers + top_rate))

        # スレッドプールを使って同期更新を実行（優先プールを使用）
        if top_users:
            self._enrich_user_details_with_pool(
                top_users,
                self._priority_api_queue,
                len(self.priority_api_pool),
                tag="SNAPSHOT_SYNC"
            )
        print("[SNAPSHOT_SYNC] 同期更新が完了しました。")

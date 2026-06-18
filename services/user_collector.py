# -*- coding: utf-8 -*-
"""ユーザー収集 - 検索APIでユーザーを巡回収集し、詳細データをキャッシュに保存"""
import time
import string


class UserCollector:
    def __init__(self, api_pool, cache, history_manager=None):
        import threading
        import queue
        # api_poolがリストでない場合（単一インスタンスの場合）はリストにする
        self.api_pool = api_pool if isinstance(api_pool, list) else [api_pool]
        self._api_queue = queue.Queue()
        for api in self.api_pool:
            self._api_queue.put(api)
            
        self.cache = cache
        self.history_manager = history_manager
        self._lock = threading.Lock()
        
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

        for page in range(1, 6):  # 最大5ページ
            users, pagination = self.api_pool[0].search_users(q, limit=50, page=page)
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
        users = self.api_pool[0].get_recommended_users()
        for u in users:
            username = u.get("username", "")
            if username:
                self.cache.update_user_from_search(u)
        self.cache.save()
        print(f"[COLLECT] 推奨ユーザー: {len(users)}件収集")

    def enrich_user_details(self, usernames=None):
        """
        ユーザー詳細データ（postsCount, createdAt）を取得して
        キャッシュを充実させる
        """
        if usernames is None:
            usernames = list(self.cache.users.keys())

        print(f"[COLLECT] ユーザー詳細データを取得中... ({len(usernames)}ユーザー)")
        
        import concurrent.futures
        
        enriched = 0
        total = len(usernames)
        
        def fetch_user(username):
            api = self._api_queue.get()
            try:
                user_data = api.get_user_detail(username)
                return username, user_data
            finally:
                self._api_queue.put(api)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.api_pool)) as executor:
            future_to_user = {executor.submit(fetch_user, uname): uname for uname in usernames}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_user)):
                username, user_data = future.result()
                
                with self._lock:
                    if user_data:
                        self.cache.update_user(username, user_data)
                        enriched += 1
                    else:
                        if username in self.cache.users:
                            from datetime import datetime, timezone
                            self.cache.users[username]["updatedAt"] = datetime.now(timezone.utc).isoformat()
                            
                    if (i + 1) % 10 == 0:
                        self.cache.save()
                        print(f"[COLLECT]   進捗: {i+1}/{total} ({enriched}件更新)")

        with self._lock:
            self.cache.save()
            
        print(f"[COLLECT] 詳細データ取得完了: {enriched}/{total}件更新")
        return enriched

    def enrich_single_user(self, username):
        """特定のユーザーの詳細を即座に取得・更新"""
        user_data = self.api_pool[0].get_user_detail(username)
        if user_data:
            self.cache.update_user(username, user_data)
            self.cache.save()
            return True
        return False

    def full_collect(self):
        """全体のユーザーを収集"""
        if not self._lock.acquire(blocking=False):
            print("[COLLECT] 既に収集処理が実行中のため、スキップします。")
            return

        try:
            print("="*50)
            print("[COLLECT] ユーザーデータの全体収集を開始")
            print("="*50)

            self.collect_from_search()
            self.collect_from_recommended()

            # 全ユーザーの詳細データを取得
            needs_enrichment = [
                username for username, data in self.cache.users.items()
            ]
            if needs_enrichment:
                self.enrich_user_details(needs_enrichment)

            print(f"[COLLECT] 全体収集完了: {self.cache.user_count()}ユーザー "
                  f"(アクティブ: {self.cache.active_user_count()})")
        finally:
            self._lock.release()

    def incremental_update(self):
        """インクリメンタル更新（定期実行）"""
        if not self._lock.acquire(blocking=False):
            print("[COLLECT] 既に収集処理が実行中のため、インクリメンタル更新をスキップします。")
            return

        try:
            print("[COLLECT] ユーザーデータの差分更新中...")

            self.collect_from_search()
            self.collect_from_recommended()

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
                        # delta_posts上位30名
                        sorted_d = sorted(deltas.items(), key=lambda x: x[1].get("postsCount", 0), reverse=True)
                        for uname, _ in sorted_d[:30]:
                            top_users.add(uname)
                        # rate上位30名
                        sorted_r = sorted(deltas.items(), key=lambda x: x[1].get("rate", 0), reverse=True)
                        for uname, _ in sorted_r[:30]:
                            top_users.add(uname)
                except Exception as e:
                    print(f"[COLLECT] 日間/週間上位の抽出に失敗しました: {e}")

            # 1. 必須更新（データ欠損・上位ユーザー）
            needs_enrichment = [
                username for username, data in self.cache.users.items()
                if not data.get("createdAt") or not data.get("updatedAt")
                or username in top_users
            ]

            # 2. 定期ローテーション更新（古い順に最大200件ずつ更新）
            # 必須更新に含まれていないユーザーを対象とする
            existing_users = [
                (username, data.get("updatedAt", "")) 
                for username, data in self.cache.users.items() 
                if username not in needs_enrichment
            ]
            # updatedAt が古い順にソート（""は一番古くなる）
            existing_users.sort(key=lambda x: x[1])
            
            # api_poolの数に応じて1度に更新する件数を増やす（1アカウントあたり200件）
            rotation_count = 200 * len(self.api_pool)
            for username, _ in existing_users[:rotation_count]:
                needs_enrichment.append(username)

            if needs_enrichment:
                self.enrich_user_details(needs_enrichment)

            print(f"[COLLECT] 差分更新完了: {self.cache.user_count()}ユーザー")
        finally:
            self._lock.release()

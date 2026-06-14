# -*- coding: utf-8 -*-
"""ユーザー収集 - 検索APIでユーザーを巡回収集し、詳細データをキャッシュに保存"""
import time
import string


class UserCollector:
    def __init__(self, api, cache):
        self.api = api
        self.cache = cache

    def collect_from_search(self):
        """検索APIでユーザーを収集（フォロワー数のみ）"""
        print("[COLLECT] 検索APIでユーザーを収集中...")
        queries = list(string.ascii_lowercase) + list(string.digits) + ["_"]
        collected = set()

        for q in queries:
            for page in range(1, 6):  # 最大5ページ
                users, pagination = self.api.search_users(q, limit=50, page=page)
                if not users:
                    break
                for u in users:
                    username = u.get("username", "")
                    if username and username not in collected:
                        collected.add(username)
                        self.cache.update_user_from_search(u)
                total_pages = pagination.get("pages", 1)
                if page >= total_pages:
                    break

        self.cache.save()
        print(f"[COLLECT] 検索完了: {len(collected)}ユーザーを収集")
        return collected

    def collect_from_recommended(self):
        """推奨ユーザーからも収集"""
        print("[COLLECT] 推奨ユーザーを収集中...")
        users = self.api.get_recommended_users()
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
        enriched = 0

        for i, username in enumerate(usernames):
            user_data = self.api.get_user_detail(username)
            if user_data:
                self.cache.update_user(username, user_data)
                enriched += 1

            # 50件ごとに保存
            if (i + 1) % 50 == 0:
                self.cache.save()
                print(f"[COLLECT]   進捗: {i+1}/{len(usernames)} ({enriched}件更新)")

        self.cache.save()
        print(f"[COLLECT] 詳細データ取得完了: {enriched}/{len(usernames)}件更新")
        return enriched

    def enrich_single_user(self, username):
        """特定のユーザーの詳細を即座に取得・更新"""
        user_data = self.api.get_user_detail(username)
        if user_data:
            self.cache.update_user(username, user_data)
            self.cache.save()
            return True
        return False

    def full_collect(self):
        """フル収集（起動時に1回実行）"""
        print("=" * 50)
        print("[COLLECT] ユーザーデータの全体収集を開始")
        print("=" * 50)

        # Step 1: 検索APIでユーザー名を収集
        self.collect_from_search()

        # Step 2: 推奨ユーザーからも収集
        self.collect_from_recommended()

        # Step 3: 詳細データを取得
        needs_enrichment = [
            username for username, data in self.cache.users.items()
            if not data.get("createdAt") or not data.get("updatedAt")
        ]
        if needs_enrichment:
            self.enrich_user_details(needs_enrichment)

        print(f"[COLLECT] 全体収集完了: {self.cache.user_count()}ユーザー "
              f"(アクティブ: {self.cache.active_user_count()})")

    def incremental_update(self):
        """インクリメンタル更新（定期実行）"""
        print("[COLLECT] ユーザーデータの差分更新中...")

        self.collect_from_search()
        self.collect_from_recommended()

        needs_enrichment = [
            username for username, data in self.cache.users.items()
            if not data.get("createdAt") or not data.get("updatedAt")
        ]
        if needs_enrichment:
            self.enrich_user_details(needs_enrichment)

        print(f"[COLLECT] 差分更新完了: {self.cache.user_count()}ユーザー")

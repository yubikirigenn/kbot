# -*- coding: utf-8 -*-
"""posts / ps コマンド"""
from utils.formatter import format_posts, format_error


def handle_posts(username, api, cache, collector):
    """ユーザーの投稿数を表示"""
    # ユーザーデータを即時更新
    collector.enrich_single_user(username)
    user_data = cache.get_user(username)

    if not user_data or not user_data.get("postsCount"):
        return format_error(f"@{username} のデータを取得できませんでした。")

    posts_count = user_data["postsCount"]
    rank, total = cache.get_ranking("posts", username)

    if rank is None:
        rank = "?"
    return format_posts(username, posts_count, rank, total)

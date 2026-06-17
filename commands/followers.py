# -*- coding: utf-8 -*-
"""followers / flw コマンド"""
from utils.formatter import format_followers, format_error


def handle_followers(username, api, cache, collector):
    """ユーザーのフォロワー数を表示"""
    user_data = cache.get_user(username)

    if not user_data:
        return format_error(f"@{username} のデータを取得できませんでした。")

    followers_count = user_data.get("followersCount", 0)
    rank, total = cache.get_ranking("followers", username)

    if rank is None:
        rank = "?"
    return format_followers(username, followers_count, rank, total)

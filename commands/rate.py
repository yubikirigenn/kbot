# -*- coding: utf-8 -*-
"""rate / rt コマンド"""
from datetime import datetime, timezone
from utils.formatter import format_rate, format_error


def handle_rate(username, api, cache, collector):
    """ユーザーの投稿レートを表示"""
    user_data = cache.get_user(username)

    if not user_data or not user_data.get("createdAt"):
        return format_error(f"@{username} のデータを取得できませんでした。")

    posts_count = user_data.get("postsCount", 0)
    created_at = user_data["createdAt"]

    # 登録日数とレート計算
    try:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created_dt
        days = delta.days
        hours = delta.total_seconds() / 3600
        rate = posts_count / hours if hours > 0 else 0
    except Exception:
        return format_error("レートの計算に失敗しました。")

    rank, total = cache.get_ranking("rate", username)
    if rank is None:
        rank = "?"

    return format_rate(username, rate, days, posts_count, rank, total)

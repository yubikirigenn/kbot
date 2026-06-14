# -*- coding: utf-8 -*-
"""ranking コマンド群"""
from utils.formatter import (
    format_ranking_posts, format_ranking_rate,
    format_ranking_followers, format_ranking_help
)


def handle_ranking_posts(api, cache):
    """投稿数ランキング表示"""
    top = cache.get_top_n("posts", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。"
    return format_ranking_posts(top)


def handle_ranking_rate(api, cache):
    """投稿レートランキング表示"""
    top = cache.get_top_n("rate", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。"
    return format_ranking_rate(top)


def handle_ranking_followers(api, cache):
    """フォロワーランキング表示"""
    top = cache.get_top_n("followers", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。"
    return format_ranking_followers(top)


def handle_ranking_help():
    """ランキングヘルプ表示"""
    return format_ranking_help()

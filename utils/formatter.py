# -*- coding: utf-8 -*-
"""応答メッセージのフォーマッター（vbot互換）"""
from datetime import datetime, timezone
from config import HASHTAG, BOT_MENTION


def format_posts(username, posts_count, rank, total_users):
    """投稿数コマンドの応答"""
    lines = [
        f"@{username} の投稿数は…",
        f"{posts_count:,}件",
        f"現在 {rank}位（{total_users}人中）！",
        "📝",
        "🏆",
        HASHTAG
    ]
    return "\n".join(lines)


def format_rate(username, rate, days, posts_count, rank, total_users):
    """投稿レートコマンドの応答"""
    lines = [
        f"@{username} の投稿レートは…",
        f"{rate:.2f} カロート/h",
        f"（登録 {days}日 / 総投稿 {posts_count:,}）",
        f"現在 {rank}位（{total_users}人中）！",
        "⚡",
        "🏆",
        HASHTAG
    ]
    return "\n".join(lines)


def format_followers(username, followers_count, rank, total_users):
    """フォロワー数コマンドの応答"""
    lines = [
        f"@{username} のフォロワー数は…",
        f"{followers_count:,}人",
        f"現在 {rank}位（{total_users}人中）！",
        "👥",
        "🏆",
        HASHTAG
    ]
    return "\n".join(lines)


def format_ranking_posts(top_users):
    """投稿数ランキング表示"""
    now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
    lines = ["📝 投稿数ランキング", ""]
    for i, (username, data) in enumerate(top_users, 1):
        posts = data.get("postsCount", 0)
        lines.append(f"{i}. @{username} - {posts:,}件")
    lines.append("")
    lines.append(f"（{now_str} UTC 時点）")
    lines.append(HASHTAG)
    return "\n".join(lines)


def format_ranking_rate(top_users):
    """投稿レートランキング表示"""
    now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
    lines = ["⚡ 投稿レートランキング", ""]
    for i, (username, data) in enumerate(top_users, 1):
        rate = data.get("rate", 0)
        lines.append(f"{i}. @{username} - {rate:.2f} カロート/h")
    lines.append("")
    lines.append(f"（{now_str} UTC 時点）")
    lines.append(HASHTAG)
    return "\n".join(lines)


def format_ranking_followers(top_users):
    """フォロワーランキング表示"""
    now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
    lines = ["👥 フォロワーランキング", ""]
    for i, (username, data) in enumerate(top_users, 1):
        followers = data.get("followersCount", 0)
        lines.append(f"{i}. @{username} - {followers:,}人")
    lines.append("")
    lines.append(f"（{now_str} UTC 時点）")
    lines.append(HASHTAG)
    return "\n".join(lines)


def format_ranking_help():
    """ランキングヘルプ"""
    lines = [
        "ランキング一覧",
        "followers / rate / posts",
        f"ranking <種別> で詳細",
        "🏆",
        BOT_MENTION,
        HASHTAG
    ]
    return "\n".join(lines)


def format_general_info(username, data, ranks):
    """メンションのみ（コマンドなし）の総合情報"""
    posts_count = data.get("postsCount", 0)
    followers_count = data.get("followersCount", 0)

    posts_rank, posts_total = ranks.get("posts", (None, 0))
    followers_rank, followers_total = ranks.get("followers", (None, 0))

    lines = [
        f"@{username} のランキング情報",
        "",
        f"👥 フォロワー: {followers_count:,}人",
    ]
    if followers_rank:
        lines.append(f"   {followers_rank}位（{followers_total}人中）")

    lines.append(f"📝 投稿数: {posts_count:,}件")
    if posts_rank:
        lines.append(f"   {posts_rank}位（{posts_total}人中）")

    lines.append("")
    lines.append(HASHTAG)
    return "\n".join(lines)


def format_error(message):
    """エラーメッセージ"""
    return f"⚠️ {message}\n{HASHTAG}"

# -*- coding: utf-8 -*-
"""ranking コマンド群 - 画像生成対応"""
import io
from utils.formatter import format_ranking_help
from utils.image_generator import draw_ranking_image


def _build_ranking_data(top_users, value_key):
    """(username, data) のリストからimage_generatorの入力形式に変換"""
    result = []
    for i, (username, data) in enumerate(top_users, 1):
        result.append({
            "username": username,
            "name": data.get("displayName", username),
            "value": data.get(value_key, 0),
            "rank": i,
            "avatarUrl": data.get("avatarUrl", ""),
        })
    return result


def _generate_image_bytes(title, metric_name, ranking_data):
    """ランキング画像を生成しバイト列を返す"""
    img = draw_ranking_image(title, metric_name, ranking_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def handle_ranking_posts(api, cache):
    """投稿数ランキング表示（画像生成）"""
    top = cache.get_top_n("posts", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "postsCount")
    image_bytes = _generate_image_bytes("投稿数ランキング", "投稿件数", ranking_data)
    # media_files としてバイト列のリストを返す（post_replyがFormDataで送信する）
    return "📝 投稿数ランキング #kbot", [image_bytes]


def handle_ranking_rate(api, cache):
    """投稿レートランキング表示（画像生成）"""
    top = cache.get_top_n("rate", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "rate")
    image_bytes = _generate_image_bytes("投稿レートランキング", "カロート/h", ranking_data)
    return "⚡ 投稿レートランキング #kbot", [image_bytes]


def handle_ranking_followers(api, cache):
    """フォロワーランキング表示（画像生成）"""
    top = cache.get_top_n("followers", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "followersCount")
    image_bytes = _generate_image_bytes("フォロワーランキング", "フォロワー数", ranking_data)
    return "👥 フォロワーランキング #kbot", [image_bytes]


def handle_ranking_help():
    """ランキングヘルプ表示"""
    return format_ranking_help(), None

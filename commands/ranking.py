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


def handle_ranking_posts(api, cache):
    """投稿数ランキング表示（画像生成）"""
    top = cache.get_top_n("posts", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "postsCount")
    img = draw_ranking_image("投稿数ランキング", "投稿件数", ranking_data)

    # 画像をバイト列に変換
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()

    # Supabaseにアップロード
    media_url = api.upload_media(image_bytes)
    if media_url:
        return "📝 投稿数ランキング #kbot", [media_url]
    else:
        # 画像アップロード失敗時はテキストで返す
        from utils.formatter import format_ranking_posts as fmt
        return fmt(top), None


def handle_ranking_rate(api, cache):
    """投稿レートランキング表示（画像生成）"""
    top = cache.get_top_n("rate", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "rate")
    img = draw_ranking_image("投稿レートランキング", "カロート/h", ranking_data)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()

    media_url = api.upload_media(image_bytes)
    if media_url:
        return "⚡ 投稿レートランキング #kbot", [media_url]
    else:
        from utils.formatter import format_ranking_rate as fmt
        return fmt(top), None


def handle_ranking_followers(api, cache):
    """フォロワーランキング表示（画像生成）"""
    top = cache.get_top_n("followers", 10)
    if not top:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    ranking_data = _build_ranking_data(top, "followersCount")
    img = draw_ranking_image("フォロワーランキング", "フォロワー数", ranking_data)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()

    media_url = api.upload_media(image_bytes)
    if media_url:
        return "👥 フォロワーランキング #kbot", [media_url]
    else:
        from utils.formatter import format_ranking_followers as fmt
        return fmt(top), None


def handle_ranking_help():
    """ランキングヘルプ表示"""
    return format_ranking_help(), None

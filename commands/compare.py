# -*- coding: utf-8 -*-
"""compare (VS) コマンド"""
import io
from utils.image_generator import draw_comparison_image
from utils.formatter import format_error


def handle_compare(api, cache, collector, parsed):
    user_a = parsed.get("target")
    user_b = parsed.get("target2")

    if not user_a or not user_b:
        return format_error("比較する2人のユーザーを指定してください。(例: @kbot @userA vs @userB)"), None

    # 最新情報を取得
    collector.enrich_single_user(user_a)
    collector.enrich_single_user(user_b)

    data_a = cache.get_user(user_a)
    data_b = cache.get_user(user_b)

    if not data_a or not data_b:
        missing = []
        if not data_a: missing.append(f"{user_a}")
        if not data_b: missing.append(f"{user_b}")
        return format_error(f"{' と '.join(missing)} のデータを取得できませんでした。"), None

    # ランキング順位を取得
    ranks_a = {
        "followers": cache.get_ranking("followers", user_a)[0],
        "posts": cache.get_ranking("posts", user_a)[0]
    }
    ranks_b = {
        "followers": cache.get_ranking("followers", user_b)[0],
        "posts": cache.get_ranking("posts", user_b)[0]
    }

    img = draw_comparison_image(user_a, data_a, user_b, data_b, ranks_a, ranks_b)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()

    return f"🔥 {user_a} VS {user_b} #kbot", [image_bytes]

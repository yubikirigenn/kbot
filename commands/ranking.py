# -*- coding: utf-8 -*-
"""ranking コマンド群 - 画像生成対応"""
import io
from utils.formatter import format_ranking_help
from utils.image_generator import draw_ranking_image


def _build_ranking_data(sorted_list, start, end, value_key, target_user=None):
    """(username, value) もしくは (username, data) のリストから表示範囲を切り出してimage_generatorの入力形式に変換"""
    result = []
    
    # 全体から start-1 〜 end までの範囲を切り出し
    # start は 1-indexed
    slice_start = max(0, start - 1)
    slice_end = min(len(sorted_list), end)
    
    sliced = sorted_list[slice_start:slice_end]
    
    for i, item in enumerate(sliced, start):
        if isinstance(item, tuple) and len(item) == 2:
            username, data = item
            # data が dict（通常キャッシュ）か、単なる数値（差分）か
            if isinstance(data, dict):
                val = data.get(value_key, 0)
                name = data.get("displayName", username)
                avatar = data.get("avatarUrl", "")
            else:
                val = data
                # 差分ソート用の簡易タプルの場合はキャッシュから名前等を引く
                # 呼び出し側で (username, value, data) にする方が安全
                pass 
        elif isinstance(item, tuple) and len(item) == 3:
            username, val, data = item
            name = data.get("displayName", username)
            avatar = data.get("avatarUrl", "")
        else:
            continue
            
        result.append({
            "username": username,
            "name": name,
            "value": val,
            "rank": i,
            "avatarUrl": avatar,
            "is_target": (username.lower() == target_user.lower()) if target_user else False
        })
        
    return result


def _generate_image_bytes(title, metric_name, ranking_data):
    """ランキング画像を生成しバイト列を返す"""
    img = draw_ranking_image(title, metric_name, ranking_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _handle_generic_ranking(api, cache, parsed, history_manager, sort_key, title_base, metric_name):
    period = parsed.get("period")
    start = parsed.get("start", 1)
    end = parsed.get("end", 10)
    target_user = parsed.get("target")

    width = end - start + 1
    if width < 2 or width > 15:
        return f"⚠️ 範囲は2〜15人の幅で指定してください。(例: 11-20)", None

    pool = cache.get_all_users_for_followers() if sort_key == "followersCount" else cache.get_active_users()

    sorted_list = []
    
    if period in ("day", "week"):
        deltas = history_manager.get_deltas(cache, period)
        title_prefix = "【日間】" if period == "day" else "【週間】"
        metric_disp = metric_name + "増加"
        
        # 差分リストを作成 (username, delta_val, cache_data)
        for uname, udata in pool.items():
            dval = deltas.get(uname, {}).get(sort_key, 0)
            # 差分0以下のユーザーは省くか、そのままにするか
            # 増加ランキングなのでそのままにするが、マイナス対応も
            sorted_list.append((uname, dval, udata))
            
        sorted_list.sort(key=lambda x: x[1], reverse=True)
    else:
        title_prefix = ""
        metric_disp = metric_name
        for uname, udata in pool.items():
            val = udata.get(sort_key, 0)
            sorted_list.append((uname, val, udata))
            
        sorted_list.sort(key=lambda x: x[1], reverse=True)

    if not sorted_list:
        return "⚠️ ランキングデータがまだありません。しばらくお待ちください。", None

    if start > len(sorted_list):
        return f"⚠️ その範囲にはユーザーがいません。(全{len(sorted_list)}人)", None

    ranking_data = _build_ranking_data(sorted_list, start, end, sort_key, target_user)
    
    # ターゲットユーザーが範囲外にいる場合、リストの末尾に追加する
    if target_user and not any(r["username"].lower() == target_user.lower() for r in ranking_data):
        target_index = next((i for i, item in enumerate(sorted_list) if item[0].lower() == target_user.lower()), None)
        if target_index is not None:
            uname, val, udata = sorted_list[target_index]
            ranking_data.append({
                "username": uname,
                "name": udata.get("displayName", uname),
                "value": val,
                "rank": target_index + 1,
                "avatarUrl": udata.get("avatarUrl", ""),
                "is_target": True
            })

    title = f"{title_prefix}{title_base} ({start}-{end}位)"
    
    # 差分表示用のフラグをセット
    for r in ranking_data:
        if period in ("day", "week"):
            r["is_delta"] = True

    image_bytes = _generate_image_bytes(title, metric_disp, ranking_data)
    
    return f"{title} #kbot", [image_bytes]


def handle_ranking_posts(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "postsCount", "投稿数ランキング", "投稿件数")

def handle_ranking_rate(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "rate", "投稿レートランキング", "カロート/h")

def handle_ranking_followers(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "followersCount", "フォロワーランキング", "フォロワー数")

def handle_ranking_help():
    return format_ranking_help(), None

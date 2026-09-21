# -*- coding: utf-8 -*-
"""ranking コマンド群 - 画像生成対応"""
import io
from config import (
    HISTORY_EXACT_COUNT_MAX_CANDIDATES,
    HISTORY_EXACT_COUNT_MIN_BASELINE_AGE_MINUTES,
)
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


def _calibrate_period_post_counts(
    api, history_manager, period, deltas, sorted_list, target_user=None
):
    """Correct inflated period deltas for stale baselines using read-only posts.

    Snapshot deltas are upper bounds when the baseline was sampled before the
    calendar boundary.  Verify only candidates that can still reach the top 10,
    plus an explicitly requested user.
    """
    if not hasattr(api, "count_user_posts_since"):
        return sorted_list
    if period not in ("day", "week"):
        return sorted_list
    boundary = history_manager.get_period_boundary(period)
    if boundary is None:
        return sorted_list

    original = list(sorted_list)
    corrected = {}
    checked = set()
    max_candidates = min(len(original), HISTORY_EXACT_COUNT_MAX_CANDIDATES)

    def verify(item):
        username, approximate, _ = item
        key = username.casefold()
        if key in checked:
            return
        checked.add(key)
        delta = deltas.get(username, {})
        baseline_age = delta.get("baselineAgeMinutes")
        if baseline_age is None or baseline_age <= HISTORY_EXACT_COUNT_MIN_BASELINE_AGE_MINUTES:
            return
        try:
            exact = api.count_user_posts_since(username, boundary)
        except Exception as error:
            print(f"[RANKING] Exact period count failed for @{username}: {error}")
            return
        if exact is not None:
            corrected[key] = exact

    processed = 0
    while processed < max_candidates:
        verify(original[processed])
        processed += 1
        if processed >= 10:
            prefix_values = sorted(
                (
                    corrected.get(username.casefold(), value)
                    for username, value, _ in original[:processed]
                ),
                reverse=True,
            )
            tenth_value = prefix_values[9]
            next_upper_bound = original[processed][1] if processed < len(original) else None
            if next_upper_bound is None or next_upper_bound <= tenth_value:
                break

    if target_user:
        target_item = next(
            (item for item in original if item[0].casefold() == target_user.casefold()),
            None,
        )
        if target_item:
            verify(target_item)

    calibrated = [
        (username, corrected.get(username.casefold(), value), data)
        for username, value, data in original
    ]
    calibrated.sort(key=lambda item: item[1], reverse=True)
    return calibrated


def _handle_generic_ranking(api, cache, parsed, history_manager, sort_key, title_base, metric_name):
    period = parsed.get("period")
    start = parsed.get("start", 1)
    end = parsed.get("end", 10)
    target_user = parsed.get("target")
    target_note = ""

    width = end - start + 1
    if width < 2 or width > 15:
        return f"⚠️ 範囲は2〜15人の幅で指定してください。(例: 11-20)", None

    pool = cache.get_all_users_for_followers() if sort_key == "followersCount" else cache.get_active_users()

    sorted_list = []
    
    if period in ("day", "week"):
        if (
            hasattr(history_manager, "snapshot_is_current")
            and not history_manager.snapshot_is_current(period)
        ):
            label = "日間" if period == "day" else "週間"
            return (
                f"⚠️ {label}ランキングの期間基準を更新中です。"
                "不完全な人数・件数では返さず、集計準備が完了してから表示します。",
                None,
            )
        deltas = history_manager.get_deltas(cache, period)
        title_prefix = "【日間】" if period == "day" else "【週間】"
        metric_disp = metric_name + "増加"
        
        # 差分リストを作成 (username, delta_val, cache_data)
        for uname, udata in pool.items():
            delta = deltas.get(uname, {})
            # 古い観測値から推測した数はランキングへ混ぜない。
            if not delta.get("valid", False):
                continue
            dval = delta.get(sort_key, 0)
            sorted_list.append((uname, dval, udata))
            
        sorted_list.sort(key=lambda x: x[1], reverse=True)
        if sort_key == "postsCount":
            sorted_list = _calibrate_period_post_counts(
                api, history_manager, period, deltas, sorted_list, target_user
            )
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
        elif period in ("day", "week"):
            target_delta = next(
                (
                    data
                    for username, data in deltas.items()
                    if username.casefold() == target_user.casefold()
                ),
                None,
            )
            if target_delta and not target_delta.get("valid", False):
                reason_labels = {
                    "no_identity_baseline": "期間開始時の本人確認済み基準がありません",
                    "stale_or_legacy_baseline": "期間開始時の基準データが不完全です",
                    "missing_baseline_value": "期間開始時の件数が欠けています",
                    "missing_current_sample_time": "最新取得時刻を確認できません",
                    "current_older_than_baseline": "最新値が期間開始時の基準より古い状態です",
                    "missing_current_value": "最新件数が欠けています",
                }
                reason = reason_labels.get(
                    target_delta.get("reason"), "この期間の有効な差分を計算できません"
                )
                target_note = f"\n⚠️ @{target_user}: {reason}。"
            else:
                target_note = f"\n⚠️ @{target_user}: このランキングの集計対象外です。"

    title = f"{title_prefix}{title_base} ({start}-{end}位)"
    
    # 差分表示用のフラグをセット
    for r in ranking_data:
        if period in ("day", "week") and sort_key != "rate":
            r["is_delta"] = True

    if period in ("day", "week") and sort_key == "rate":
        metric_disp = metric_name
    elif period in ("day", "week"):
        metric_disp = metric_name + "増加"
    else:
        metric_disp = metric_name

    image_bytes = _generate_image_bytes(title, metric_disp, ranking_data)
    
    coverage = f"（集計対象 {len(sorted_list)}人）" if period in ("day", "week") else ""
    return f"{title}{coverage}{target_note} #kbot", [image_bytes]


def handle_ranking_posts(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "postsCount", "投稿数ランキング", "投稿件数")

def handle_ranking_rate(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "rate", "投稿レートランキング", "カロート/h")

def handle_ranking_followers(api, cache, parsed, history_manager):
    return _handle_generic_ranking(api, cache, parsed, history_manager, "followersCount", "フォロワーランキング", "フォロワー数")

def handle_ranking_help():
    return format_ranking_help(), None

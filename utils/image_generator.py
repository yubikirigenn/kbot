# -*- coding: utf-8 -*-
"""ランキング画像生成ユーティリティ"""
import os
import io
import urllib.request
import datetime
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "fonts")
FONT_PATH_MEDIUM = os.path.join(FONT_DIR, "NotoSansCJKjp-Medium.otf")
FONT_PATH_BOLD = os.path.join(FONT_DIR, "NotoSansCJKjp-Bold.otf")

_fonts_checked = False


def ensure_fonts():
    """フォントがなければダウンロード（Render上で実行される）"""
    global _fonts_checked
    if _fonts_checked:
        return
    _fonts_checked = True

    if os.path.exists(FONT_PATH_BOLD) and os.path.exists(FONT_PATH_MEDIUM):
        return

    os.makedirs(FONT_DIR, exist_ok=True)
    fonts = {
        FONT_PATH_MEDIUM: "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansCJKjp-Medium.otf",
        FONT_PATH_BOLD: "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansCJKjp-Bold.otf"
    }
    for path, url in fonts.items():
        if not os.path.exists(path):
            print(f"[IMG] Downloading font: {os.path.basename(path)}...")
            try:
                urllib.request.urlretrieve(url, path)
                print(f"[IMG] Font downloaded: {os.path.basename(path)}")
            except Exception as e:
                print(f"[IMG] Failed to download font ({os.path.basename(path)}): {e}")


def _load_font(path, size):
    """フォント読み込み。失敗時はデフォルトフォントを返す"""
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _text_width(font, text):
    """テキスト幅取得（互換性対応）"""
    try:
        return font.getlength(text)
    except AttributeError:
        try:
            return font.getsize(text)[0]
        except:
            return len(text) * 10


def fetch_avatar(url):
    """アバター画像をダウンロードしてPIL Imageとして返す"""
    try:
        if not url or "default" in url:
            return _default_avatar()
        import requests
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return Image.open(io.BytesIO(res.content)).convert("RGBA")
    except Exception:
        pass
    return _default_avatar()


def _default_avatar():
    """デフォルトアバター（グレーの円）"""
    size = 100
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([0, 0, size-1, size-1], fill=(200, 210, 220, 255))
    # 人アイコン風
    draw.ellipse([30, 15, 70, 50], fill=(160, 170, 180, 255))
    draw.ellipse([20, 55, 80, 100], fill=(160, 170, 180, 255))
    return img


def circle_avatar(img, size):
    """画像を丸くクリップしてリサイズ"""
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def _draw_rounded_rect(draw, coords, radius, fill=None, outline=None, width=1):
    """角丸四角形を描画（互換性対応）"""
    try:
        draw.rounded_rectangle(coords, radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        # 古いPillowバージョン用のフォールバック
        x1, y1, x2, y2 = coords
        draw.rectangle(coords, fill=fill, outline=outline, width=width)


def draw_ranking_image(title, metric_name, top_users):
    """
    ランキング画像を生成する

    Args:
        title: "投稿数ランキング" など
        metric_name: "投稿件数", "カロート/h", "フォロワー数" など
        top_users: list of dicts {"username", "name", "value", "avatarUrl", "rank"}

    Returns:
        PIL.Image
    """
    ensure_fonts()

    # === サイズ計算 ===
    width = 640
    padding = 20
    header_h = 65
    top3_item_h = 90
    normal_item_h = 55
    footer_h = 35

    top3_count = min(3, len(top_users))
    normal_count = max(0, len(top_users) - 3)
    separator_h = 15 if top3_count > 0 else 0

    content_h = (top3_count * top3_item_h) + separator_h + (normal_count * normal_item_h)
    height = header_h + padding + content_h + footer_h

    # === 描画開始 ===
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # フォント
    font_title = _load_font(FONT_PATH_BOLD, 26)
    font_badge = _load_font(FONT_PATH_MEDIUM, 13)
    font_name_lg = _load_font(FONT_PATH_BOLD, 19)
    font_uname_lg = _load_font(FONT_PATH_MEDIUM, 13)
    font_val_lg = _load_font(FONT_PATH_BOLD, 26)
    font_name_sm = _load_font(FONT_PATH_BOLD, 15)
    font_uname_sm = _load_font(FONT_PATH_MEDIUM, 11)
    font_val_sm = _load_font(FONT_PATH_BOLD, 16)
    font_rank = _load_font(FONT_PATH_BOLD, 14)
    font_footer = _load_font(FONT_PATH_MEDIUM, 11)

    # カラーパレット（青ベース）
    BLUE = (59, 130, 246)
    BLUE_LIGHT = (147, 197, 253)
    BLUE_BAR = (96, 165, 250)
    TEXT_DARK = (31, 41, 55)
    TEXT_GRAY = (136, 143, 155)
    BORDER = (229, 231, 235)
    GOLD = (212, 175, 55)
    SILVER = (180, 185, 192)
    BRONZE = (205, 150, 80)
    WHITE = (255, 255, 255)

    # === ヘッダー ===
    _draw_rounded_rect(draw, [0, 0, width, header_h], radius=0, fill=BLUE)
    draw.text((padding, 18), title, font=font_title, fill=WHITE)

    # メトリクスバッジ
    badge_text = metric_name
    bw = _text_width(font_badge, badge_text) + 24
    bh = 28
    bx = width - bw - padding
    by = 18
    _draw_rounded_rect(draw, [bx, by, bx + bw, by + bh], radius=14, fill=BLUE_LIGHT)
    draw.text((bx + 12, by + 5), badge_text, font=font_badge, fill=WHITE)

    # === 外枠 ===
    draw.rectangle([0, 0, width - 1, height - 1], outline=BLUE, width=3)

    if not top_users:
        draw.text((padding, header_h + 30), "データがありません", font=font_name_lg, fill=TEXT_DARK)
        return img

    max_val = float(max(u["value"] for u in top_users)) if top_users else 1.0
    if max_val == 0:
        max_val = 1.0

    cur_y = header_h + padding
    rank_colors = [GOLD, SILVER, BRONZE]

    for i, u in enumerate(top_users):
        rank = u["rank"]
        val = u["value"]

        # 値のフォーマット
        if isinstance(val, float):
            val_str = f"{val:.2f}"
        else:
            val_str = f"{val:,}件"

        avatar = fetch_avatar(u.get("avatarUrl"))

        if i < 3:
            # === Top 3: 大きめのカード表示 ===
            rc = rank_colors[i]

            # 順位メダル
            medal_x, medal_y = padding, cur_y + 22
            draw.ellipse([medal_x, medal_y, medal_x + 30, medal_y + 30], fill=rc)
            rank_str = str(rank)
            rw = _text_width(font_rank, rank_str)
            draw.text((medal_x + (30 - rw) / 2, medal_y + 6), rank_str, font=font_rank, fill=WHITE)

            # アバター
            av_size = 60
            av_x, av_y = padding + 40, cur_y + 10
            av = circle_avatar(avatar, av_size)
            img.paste(av, (av_x, av_y), av)
            # 青い枠
            draw.ellipse([av_x - 2, av_y - 2, av_x + av_size + 2, av_y + av_size + 2], outline=BLUE, width=2)

            # 名前
            name_x = av_x + av_size + 15
            display_name = u["name"][:12]
            draw.text((name_x, cur_y + 15), display_name, font=font_name_lg, fill=TEXT_DARK)
            draw.text((name_x, cur_y + 40), f"@{u['username']}", font=font_uname_lg, fill=TEXT_GRAY)

            # 値（右寄せ）
            vw = _text_width(font_val_lg, val_str)
            val_x = width - vw - padding - 5
            val_color = BLUE if i == 0 else TEXT_DARK
            draw.text((val_x, cur_y + 25), val_str, font=font_val_lg, fill=val_color)

            # バーチャート
            bar_max_w = val_x - name_x - 20
            bar_w = max(8, int((u["value"] / max_val) * bar_max_w))
            bar_y = cur_y + 48
            _draw_rounded_rect(draw, [val_x - bar_w - 10, bar_y, val_x - 10, bar_y + 8], radius=4, fill=BLUE_BAR)

            cur_y += top3_item_h

            # セパレーター
            if i < 2 or (i == 2 and normal_count > 0):
                draw.line([padding, cur_y, width - padding, cur_y], fill=BORDER, width=1)
                cur_y += 5

        else:
            # === 4位以降: コンパクト表示 ===
            # 順位番号
            rank_str = str(rank)
            rw = _text_width(font_rank, rank_str)
            draw.text((padding + 5, cur_y + 17), rank_str, font=font_rank, fill=TEXT_GRAY)

            # アバター
            av_size = 40
            av_x = padding + 35
            av = circle_avatar(avatar, av_size)
            img.paste(av, (av_x, cur_y + 7), av)

            # 名前
            name_x = av_x + av_size + 12
            display_name = u["name"][:12]
            draw.text((name_x, cur_y + 5), display_name, font=font_name_sm, fill=TEXT_DARK)
            draw.text((name_x, cur_y + 25), f"@{u['username']}", font=font_uname_sm, fill=TEXT_GRAY)

            # 値（右寄せ）
            vw = _text_width(font_val_sm, val_str)
            val_x = width - vw - padding - 5
            draw.text((val_x, cur_y + 15), val_str, font=font_val_sm, fill=TEXT_GRAY)

            # バーチャート
            bar_max_w = val_x - name_x - 20
            bar_w = max(5, int((u["value"] / max_val) * bar_max_w))
            bar_y = cur_y + 32
            _draw_rounded_rect(draw, [val_x - bar_w - 10, bar_y, val_x - 10, bar_y + 6], radius=3, fill=BORDER)

            cur_y += normal_item_h

    # === フッター ===
    now_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M 時点")
    tw = _text_width(font_footer, now_str)
    draw.text((width - tw - padding, height - footer_h + 8), now_str, font=font_footer, fill=TEXT_GRAY)

    return img

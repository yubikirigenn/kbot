# -*- coding: utf-8 -*-
"""コマンド解析・ディスパッチ"""
import re
from config import COMMAND_ALIASES, USERNAME


def parse_command(content):
    """
    メンションテキストからコマンドとターゲットユーザーを解析
    @kbot rt → ("rate", None)
    @kbot rps → ("ranking_posts", None)
    @kbot rt @someone → ("rate", "someone")
    @kbot ranking posts → ("ranking_posts", None)
    @kbot → (None, None)  # コマンドなし = 総合情報
    """
    # メンションを除去（@kbot 部分のみ）
    clean = re.sub(rf"(?i)@{USERNAME}\s*", "", content).strip()

    if not clean:
        return None, None  # コマンドなし→総合情報表示

    # "ranking XXX" パターンを先にチェック
    ranking_match = re.match(r"^ranking\s+(\w+)", clean, re.IGNORECASE)
    if ranking_match:
        sub = ranking_match.group(1).lower()
        full_cmd = f"ranking {sub}"
        if full_cmd in COMMAND_ALIASES:
            # ranking コマンドの後にユーザー指定があるか
            rest = clean[ranking_match.end():].strip()
            target = _extract_target_user(rest)
            return COMMAND_ALIASES[full_cmd], target

    # 単一コマンドをチェック
    parts = clean.split()
    cmd = parts[0].lower()
    if cmd in COMMAND_ALIASES:
        # コマンドの後の残りからユーザー指定を抽出
        rest = " ".join(parts[1:]).strip()
        target = _extract_target_user(rest)
        return COMMAND_ALIASES[cmd], target

    # 不明なコマンド → ヘルプ表示
    return "unknown", clean


def _extract_target_user(text):
    """テキストから @username を抽出してusernameを返す。なければNone"""
    if not text:
        return None
    # @つきのユーザー名を探す
    match = re.search(r"@(\w+)", text)
    if match:
        target = match.group(1)
        # 自分自身へのメンションは無視
        if target.lower() != USERNAME.lower():
            return target
    # @なしのユーザー名（1単語のみ）
    text = text.strip()
    if text and " " not in text and not text.startswith("#"):
        if text.lower() != USERNAME.lower():
            return text
    return None

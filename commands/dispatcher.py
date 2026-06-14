# -*- coding: utf-8 -*-
"""コマンド解析・ディスパッチ"""
import re
from config import COMMAND_ALIASES, USERNAME


def parse_command(content):
    """
    メンションテキストからコマンドを解析
    @kbot rt → ("rate", None)
    @kbot rps → ("ranking_posts", None)
    @kbot ranking posts → ("ranking_posts", None)
    @kbot → (None, None)  # コマンドなし = 総合情報
    """
    # メンションを除去
    clean = re.sub(rf"(?i)@{USERNAME}\s*", "", content).strip()

    if not clean:
        return None, None  # コマンドなし→総合情報表示

    # "ranking XXX" パターンを先にチェック
    ranking_match = re.match(r"^ranking\s+(\w+)", clean, re.IGNORECASE)
    if ranking_match:
        sub = ranking_match.group(1).lower()
        full_cmd = f"ranking {sub}"
        if full_cmd in COMMAND_ALIASES:
            return COMMAND_ALIASES[full_cmd], None

    # 単一コマンドをチェック
    cmd = clean.split()[0].lower()
    if cmd in COMMAND_ALIASES:
        return COMMAND_ALIASES[cmd], None

    # 不明なコマンド → ヘルプ表示
    return "unknown", clean

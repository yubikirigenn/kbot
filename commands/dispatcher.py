# -*- coding: utf-8 -*-
"""コマンド解析・ディスパッチ"""
import re
from config import COMMAND_ALIASES, USERNAME


def parse_command(content):
    """
    メンションテキストからコマンドと各種オプションを解析
    戻り値: dict
    """
    clean = re.sub(rf"(?i)@{USERNAME}\s*", "", content).strip()

    if not clean:
        return {"cmd": None, "target": None}

    # VSコマンドのチェック
    vs_match = re.search(r"@?(\w+)\s+vs\s+@?(\w+)", clean, re.IGNORECASE)
    if vs_match:
        return {
            "cmd": "compare",
            "target": vs_match.group(1),
            "target2": vs_match.group(2)
        }

    parts = clean.split()
    cmd_name = parts[0].lower()
    
    # ranking XXX パターン対応
    if cmd_name == "ranking" and len(parts) > 1:
        sub = parts[1].lower()
        full_cmd = f"ranking {sub}"
        if full_cmd in COMMAND_ALIASES:
            cmd_name = full_cmd
            parts = [cmd_name] + parts[2:]

    if cmd_name in COMMAND_ALIASES:
        resolved_cmd = COMMAND_ALIASES[cmd_name]
        
        period = None
        start = 1
        end = 10
        target = None
        
        # コマンド以降の引数を解析
        rest_parts = parts[1:] if cmd_name not in COMMAND_ALIASES or " " not in cmd_name else parts[1:] # space in ranking posts is already handled
        
        if " " in cmd_name: # ranking posts
            pass # parts is already updated
            
        for p in rest_parts:
            pl = p.lower()
            if pl in ("day", "week"):
                period = pl
            elif re.match(r"^\d+-\d+$", p):
                try:
                    s, e = map(int, p.split("-"))
                    start, end = min(s, e), max(s, e)
                except ValueError:
                    pass
            elif p.startswith("@"):
                t = _extract_target_user(p)
                if t: target = t
            else:
                # @なしでもターゲットとして抽出試行
                if not target:
                    t = _extract_target_user(p)
                    if t: target = t

        return {
            "cmd": resolved_cmd,
            "period": period,
            "start": start,
            "end": end,
            "target": target
        }

    return {"cmd": "unknown", "raw": clean}


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

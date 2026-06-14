import os
from config import USERNAME
from commands.dispatcher import parse_command
from utils.formatter import format_general_info, format_error
from commands.rate import handle_rate
from commands.posts import handle_posts

# モック用クラス
class MockCache:
    def get_user(self, username):
        return {
            "username": username,
            "postsCount": 100,
            "followersCount": 50,
            "createdAt": "2024-01-01T00:00:00.000Z"
        }
    def get_ranking(self, category, username):
        return 1, 100
    def get_top(self, category, limit=10):
        return []

class MockAPI:
    def get_user_detail(self, username):
        return {"username": username, "postsCount": 100, "followersCount": 50}

class MockCollector:
    def enrich_single_user(self, username):
        pass

cache = MockCache()
api = MockAPI()
collector = MockCollector()

def test_command(content, author):
    print(f"--- Test: '{content}' by @{author} ---")
    
    # メンション検知ロジック (main.py と同じ)
    notification_type = "MENTION"
    if f"@{USERNAME.lower()}" not in content.lower():
        print("  => Ignored (not mentioned)")
        return
    
    command, _ = parse_command(content)
    print(f"  Parsed command: {command}")
    
    if command is None:
        print("  Response: " + format_general_info(author, cache.get_user(author), {"posts": (1,10), "followers": (2,10)}).replace('\n', ' '))
    elif command == "rate":
        print("  Response: " + handle_rate(author, api, cache, collector).replace('\n', ' '))
    elif command == "posts":
        print("  Response: " + handle_posts(author, api, cache, collector).replace('\n', ' '))
    else:
        print(f"  Response: (Other command {command})")

test_command("@kbot", "testuser")
test_command("@kbot rt", "testuser")
test_command("@kbot ps", "testuser")
test_command("こんにちは @kbot rt", "testuser")
test_command("@kbotなんか", "testuser")

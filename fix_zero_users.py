import sys
import os
import time

sys.path.append('c:\\KaroBot\\kbot')
from api.auth import AuthManager
from api.karotter import KarotterAPI
from services.ranking_cache import RankingCache

auth = AuthManager()
if not auth.login():
    print("Login failed")
    sys.exit(1)

api = KarotterAPI(auth)
cache = RankingCache()

zero_users = [uname for uname, data in cache.users.items() if data.get("postsCount", 0) == 0]
print(f"0件のユーザー数: {len(zero_users)}")

updated_count = 0
for i, uname in enumerate(zero_users):
    detail = api.get_user_detail(uname)
    if detail:
        old_posts = cache.users.get(uname, {}).get("postsCount", 0)
        cache.update_user(uname, detail)
        new_posts = cache.users.get(uname, {}).get("postsCount", 0)
        if new_posts > 0:
            print(f"[{i+1}/{len(zero_users)}] @{uname} が復旧しました: {old_posts} -> {new_posts}件")
            updated_count += 1
            
    if (i + 1) % 50 == 0:
        cache.save()
        print(f"--- 途中保存 ({i+1}人処理済み) ---")

cache.save()
print(f"\n完了！ {updated_count}人のデータを復旧しました。")

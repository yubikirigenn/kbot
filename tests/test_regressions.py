# -*- coding: utf-8 -*-
"""外部通信を一切行わない回帰テスト。"""
import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["KBOT_DISABLE_WRITES"] = "true"

import services.ranking_cache as ranking_cache_module
import services.history_manager as history_module
from api.auth import AuthManager
import api.auth as auth_module
from api.karotter import KarotterAPI
import api.karotter as karotter_module
import main as main_module
from commands.posts import handle_posts


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_cache_file = ranking_cache_module.USER_CACHE_FILE
        self.old_excluded_file = ranking_cache_module.EXCLUDED_USERS_FILE
        ranking_cache_module.USER_CACHE_FILE = str(root / "users_cache.json")
        ranking_cache_module.EXCLUDED_USERS_FILE = str(root / "excluded_users.json")
        (root / "excluded_users.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        ranking_cache_module.USER_CACHE_FILE = self.old_cache_file
        ranking_cache_module.EXCLUDED_USERS_FILE = self.old_excluded_file
        self.temp.cleanup()

    def make_cache(self):
        return ranking_cache_module.RankingCache()

    @staticmethod
    def detail(user_id, username, posts, created="2026-01-01T00:00:00Z"):
        return {
            "id": user_id,
            "username": username,
            "postsCount": posts,
            "followersCount": 5,
            "followingCount": 2,
            "createdAt": created,
            "displayName": username,
        }

    def test_rename_follows_stable_id_and_removes_old_key(self):
        cache = self.make_cache()
        cache.update_user("old_name", self.detail(10, "old_name", 100))
        canonical = cache.update_user("old_name", self.detail(10, "new_name", 108))
        self.assertEqual(canonical, "new_name")
        self.assertIsNone(cache.get_user("old_name"))
        self.assertEqual(cache.get_user("NEW_NAME")["postsCount"], 108)
        self.assertEqual(cache.get_user("new_name")["userId"], "10")

    def test_reused_username_does_not_inherit_previous_person_count(self):
        cache = self.make_cache()
        cache.update_user("shared", self.detail(10, "shared", 18000, "2026-01-01T00:00:00Z"))
        cache.update_user("shared", self.detail(99, "shared", 3, "2026-08-01T00:00:00Z"))
        record = cache.get_user("shared")
        self.assertEqual(record["userId"], "99")
        self.assertEqual(record["postsCount"], 3)

    def test_legitimate_decrease_and_zero_are_accepted(self):
        cache = self.make_cache()
        cache.update_user("person", self.detail(10, "person", 100))
        cache.update_user("person", self.detail(10, "person", 95))
        self.assertEqual(cache.get_user("person")["postsCount"], 95)
        cache.update_user("person", self.detail(10, "person", 0))
        self.assertEqual(cache.get_user("person")["postsCount"], 0)

    def test_case_only_duplicates_are_collapsed(self):
        path = Path(ranking_cache_module.USER_CACHE_FILE)
        path.write_text(json.dumps({
            "Person": {"postsCount": 10, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"},
            "person": {"postsCount": 0, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-03T00:00:00Z"},
        }), encoding="utf-8")
        cache = self.make_cache()
        self.assertEqual(cache.user_count(), 1)
        self.assertEqual(cache.get_user("PERSON")["postsCount"], 10)


class HistoryTestCase(CacheTestCase):
    def setUp(self):
        super().setUp()
        root = Path(self.temp.name)
        self.old_data_dir = history_module.DATA_DIR
        self.old_daily = history_module.DAILY_HISTORY_FILE
        self.old_weekly = history_module.WEEKLY_HISTORY_FILE
        history_module.DATA_DIR = str(root)
        history_module.DAILY_HISTORY_FILE = str(root / "history_daily.json")
        history_module.WEEKLY_HISTORY_FILE = str(root / "history_weekly.json")

    def tearDown(self):
        history_module.DATA_DIR = self.old_data_dir
        history_module.DAILY_HISTORY_FILE = self.old_daily
        history_module.WEEKLY_HISTORY_FILE = self.old_weekly
        super().tearDown()

    def test_daily_delta_survives_username_change_by_id(self):
        cache = self.make_cache()
        cache.update_user("old_name", self.detail(10, "old_name", 100))
        history = history_module.HistoryManager()
        self.assertTrue(history.save_snapshot(cache, "day"))
        cache.update_user("old_name", self.detail(10, "new_name", 112))
        delta = history.get_deltas(cache, "day")["new_name"]
        self.assertTrue(delta["valid"])
        self.assertEqual(delta["postsCount"], 12)

    def test_stale_baseline_is_excluded_instead_of_prorated(self):
        cache = self.make_cache()
        cache.update_user("person", self.detail(10, "person", 100))
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        with cache._lock:
            cache.users["person"]["sampledAt"] = stale
            cache.users["person"]["updatedAt"] = stale
        history = history_module.HistoryManager()
        history.save_snapshot(cache, "day")
        cache.update_user("person", self.detail(10, "person", 2000))
        delta = history.get_deltas(cache, "day")["person"]
        self.assertFalse(delta["valid"])
        self.assertEqual(delta["postsCount"], 0)
        self.assertEqual(delta["reason"], "stale_or_legacy_baseline")

    def test_username_reuse_does_not_use_other_id_baseline(self):
        cache = self.make_cache()
        cache.update_user("shared", self.detail(10, "shared", 100, "2026-01-01T00:00:00Z"))
        history = history_module.HistoryManager()
        history.save_snapshot(cache, "day")
        cache.update_user("shared", self.detail(99, "shared", 4, "2026-02-01T00:00:00Z"))
        delta = history.get_deltas(cache, "day")["shared"]
        self.assertFalse(delta["valid"])
        self.assertEqual(delta["postsCount"], 0)

    def test_decrease_is_visible_and_not_clamped(self):
        cache = self.make_cache()
        cache.update_user("person", self.detail(10, "person", 100))
        history = history_module.HistoryManager()
        history.save_snapshot(cache, "day")
        cache.update_user("person", self.detail(10, "person", 95))
        delta = history.get_deltas(cache, "day")["person"]
        self.assertTrue(delta["valid"])
        self.assertEqual(delta["postsCount"], -5)


class NoExternalWriteTests(unittest.TestCase):
    def test_zero_post_account_is_valid_data(self):
        cache = mock.Mock()
        cache.get_user.return_value = {"postsCount": 0}
        cache.get_ranking.return_value = (1, 1)
        rendered = handle_posts("zero_user", mock.Mock(), cache, mock.Mock())
        self.assertIn("0", rendered)

    def test_command_uses_canonical_username_returned_by_collector(self):
        collector = mock.Mock()
        collector.enrich_single_user.return_value = "new_name"
        with mock.patch.object(main_module, "handle_posts", return_value="ok") as handler:
            result = main_module.execute_command(
                {"cmd": "posts", "target": "old_name"},
                "author",
                mock.Mock(),
                mock.Mock(),
                collector,
                mock.Mock(),
            )
        self.assertEqual(result, ("ok", None))
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0], "new_name")

    def test_karotter_post_methods_are_blocked_before_auth_call(self):
        fake_auth = mock.Mock()
        karotter_module.DISABLE_KAROTTER_WRITES = True
        api = KarotterAPI(fake_auth)
        self.assertFalse(api.post_reply("never sent", "123"))
        self.assertIsNone(api.post_karoto("never sent"))
        fake_auth.request.assert_not_called()

    def test_auth_layer_blocks_posts(self):
        auth_module.DISABLE_KAROTTER_WRITES = True
        manager = AuthManager(username="test", password="test")
        manager.session = mock.Mock()
        self.assertIsNone(manager.request("POST", "/posts", json={"content": "blocked"}))
        manager.session.request.assert_not_called()

    def test_post_is_never_retried_after_401(self):
        auth_module.DISABLE_KAROTTER_WRITES = False
        manager = AuthManager(username="test", password="test")
        response = mock.Mock(status_code=401)
        manager.session = mock.Mock()
        manager.session.headers = {"Content-Type": "application/json"}
        manager.session.request.return_value = response
        manager._login_locked = mock.Mock(return_value=True)
        result = manager.request("POST", "/posts", json={"content": "one attempt"})
        self.assertIs(result, response)
        self.assertEqual(manager.session.request.call_count, 1)
        manager._login_locked.assert_not_called()
        auth_module.DISABLE_KAROTTER_WRITES = True

    def test_reply_claim_is_skipped_without_any_remote_call_in_safe_mode(self):
        main_module.DISABLE_KAROTTER_WRITES = True
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(main_module.claim_notification_for_reply("post-1"))
            urlopen.assert_not_called()

    def test_reply_claim_fails_closed_when_shared_lock_is_unconfigured(self):
        main_module.DISABLE_KAROTTER_WRITES = False
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPO": ""}, clear=False):
            with mock.patch("urllib.request.urlopen") as urlopen:
                self.assertIsNone(main_module.claim_notification_for_reply("post-2"))
                urlopen.assert_not_called()
        main_module.DISABLE_KAROTTER_WRITES = True

    def test_existing_shared_claim_is_the_only_false_result(self):
        main_module.DISABLE_KAROTTER_WRITES = False
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "sha": "abc",
            "content": base64.b64encode(
                json.dumps({"post_ids": ["post-3"]}).encode("utf-8")
            ).decode("ascii"),
        }).encode("utf-8")
        with mock.patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "test-token", "GITHUB_REPO": "owner/repo"},
            clear=False,
        ):
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                self.assertIs(main_module.claim_notification_for_reply("post-3"), False)
                urlopen.assert_called_once()
        main_module.DISABLE_KAROTTER_WRITES = True

    def test_notification_created_after_startup_is_processed(self):
        started_at = datetime(2026, 9, 19, 1, 20, tzinfo=timezone.utc)
        notification = {"createdAt": "2026-09-19T01:27:16.449Z"}
        self.assertFalse(
            main_module.notification_is_from_before_startup(notification, started_at)
        )

    def test_notification_created_before_startup_is_not_replayed(self):
        started_at = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)
        notification = {"createdAt": "2026-09-19T01:27:16.449Z"}
        self.assertTrue(
            main_module.notification_is_from_before_startup(notification, started_at)
        )

    def test_notification_without_valid_timestamp_is_not_replayed(self):
        started_at = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)
        self.assertTrue(
            main_module.notification_is_from_before_startup(
                {"createdAt": "invalid"}, started_at
            )
        )


if __name__ == "__main__":
    unittest.main()

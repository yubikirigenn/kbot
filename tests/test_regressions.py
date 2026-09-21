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
from commands.dispatcher import parse_command
import commands.ranking as ranking_module


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

    def test_current_sample_does_not_expire_only_because_time_passed(self):
        cache = self.make_cache()
        cache.update_user("person", self.detail(10, "person", 110))
        now = datetime.now(timezone.utc)
        baseline_time = (now - timedelta(days=10)).isoformat()
        current_time = (now - timedelta(days=9)).isoformat()
        with cache._lock:
            cache.users["person"]["sampledAt"] = current_time
            cache.users["person"]["updatedAt"] = current_time

        history = history_module.HistoryManager()
        with history._lock:
            history.daily_snapshot = {
                "person": {
                    "userId": "10",
                    "username": "person",
                    "postsCount": 100,
                    "followersCount": 5,
                    "sampledAt": baseline_time,
                    "baselineFresh": True,
                }
            }
            history.daily_timestamp = baseline_time
            history.daily_schema_version = history_module.SNAPSHOT_SCHEMA_VERSION

        delta = history.get_deltas(cache, "day")["person"]
        self.assertTrue(delta["valid"])
        self.assertEqual(delta["postsCount"], 10)
        self.assertGreater(delta["sampleAgeHours"], 3)

    def test_low_coverage_snapshot_is_rejected_without_replacing_baseline(self):
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        users = {
            f"user{i}": {
                "userId": str(i),
                "username": f"user{i}",
                "postsCount": i,
                "followersCount": i,
                "sampledAt": stale,
                "updatedAt": stale,
            }
            for i in range(history_module.HISTORY_COVERAGE_MIN_USERS)
        }
        cache = mock.Mock()
        cache.get_users_snapshot.return_value = users
        history = history_module.HistoryManager()
        original_snapshot = {"kept": {"postsCount": 1}}
        history.daily_snapshot = original_snapshot
        history.daily_timestamp = "2026-09-20T00:00:00+00:00"
        history.daily_schema_version = history_module.SNAPSHOT_SCHEMA_VERSION

        self.assertFalse(history.save_snapshot(cache, "day"))
        self.assertEqual(history.daily_snapshot, original_snapshot)

    def test_outdated_snapshot_is_not_presented_as_current_period(self):
        history = history_module.HistoryManager()
        history.daily_schema_version = history_module.SNAPSHOT_SCHEMA_VERSION
        history.daily_timestamp = "2026-09-20T15:00:00+00:00"
        now = datetime(2026, 9, 21, 15, 1, tzinfo=timezone.utc)
        self.assertFalse(history.snapshot_is_current("day", now=now))
        history.weekly_schema_version = history_module.SNAPSHOT_SCHEMA_VERSION
        history.weekly_timestamp = "2026-09-20T15:00:00+00:00"
        self.assertTrue(history.snapshot_is_current("week", now=now))


class NoExternalWriteTests(unittest.TestCase):
    def test_bot_worker_does_not_shadow_datetime_import(self):
        self.assertNotIn("datetime", main_module.bot_worker.__code__.co_varnames)

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

    def test_rps_period_and_target_are_parsed(self):
        self.assertEqual(
            parse_command("@kbot rps week moko"),
            {
                "cmd": "ranking_posts",
                "period": "week",
                "start": 1,
                "end": 10,
                "target": "moko",
                "rekarot": False,
            },
        )

    def test_rps_target_is_refreshed_and_canonicalized_before_ranking(self):
        collector = mock.Mock()
        collector.enrich_single_user.return_value = "New_Name"
        with mock.patch.object(
            main_module, "handle_ranking_posts", return_value=("ranking", [b"image"])
        ) as handler:
            result = main_module.execute_command(
                parse_command("@kbot rps day old_name"),
                "author",
                mock.Mock(),
                mock.Mock(),
                collector,
                mock.Mock(),
            )
        self.assertEqual(result, ("ranking", [b"image"]))
        collector.enrich_single_user.assert_called_once_with("old_name")
        self.assertEqual(handler.call_args.args[2]["target"], "New_Name")

    def test_rps_target_outside_top_ten_is_appended_with_real_rank(self):
        users = {
            f"user{i}": {
                "postsCount": 1000 - i,
                "displayName": f"User {i}",
                "avatarUrl": "",
            }
            for i in range(1, 13)
        }
        deltas = {
            username: {"postsCount": 100 - i, "valid": True}
            for i, username in enumerate(users, 1)
        }
        cache = mock.Mock()
        cache.get_active_users.return_value = users
        history = mock.Mock()
        history.get_deltas.return_value = deltas
        parsed = {
            "period": "week",
            "start": 1,
            "end": 10,
            "target": "user11",
        }
        with mock.patch.object(ranking_module, "_generate_image_bytes", return_value=b"image") as generate:
            text, images = ranking_module.handle_ranking_posts(
                mock.Mock(), cache, parsed, history
            )
        ranking_rows = generate.call_args.args[2]
        target = next(row for row in ranking_rows if row["username"] == "user11")
        self.assertEqual(target["rank"], 11)
        self.assertTrue(target["is_target"])
        self.assertEqual(images, [b"image"])
        self.assertIn("集計対象 12人", text)

    def test_stale_period_baseline_is_corrected_with_read_only_post_count(self):
        api = mock.Mock()
        api.count_user_posts_since.return_value = 12
        history = mock.Mock()
        boundary = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
        history.get_period_boundary.return_value = boundary
        rows = [
            ("stale_user", 40, {}),
            ("fresh_user", 30, {}),
        ]
        deltas = {
            "stale_user": {"baselineAgeMinutes": 80},
            "fresh_user": {"baselineAgeMinutes": 2},
        }
        calibrated = ranking_module._calibrate_period_post_counts(
            api, history, "day", deltas, rows
        )
        self.assertEqual(calibrated[0][:2], ("fresh_user", 30))
        self.assertEqual(calibrated[1][:2], ("stale_user", 12))
        api.count_user_posts_since.assert_called_once_with("stale_user", boundary)

    def test_outdated_period_returns_preparing_message_instead_of_wrong_ranking(self):
        cache = mock.Mock()
        history = mock.Mock()
        history.snapshot_is_current.return_value = False
        text, images = ranking_module.handle_ranking_posts(
            mock.Mock(),
            cache,
            {"period": "day", "start": 1, "end": 10, "target": None},
            history,
        )
        self.assertIn("期間基準を更新中", text)
        self.assertIsNone(images)
        history.get_deltas.assert_not_called()

    def test_user_posts_since_counter_stops_after_two_old_pages(self):
        boundary = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)

        def response(posts, has_next=True):
            result = mock.MagicMock(status_code=200)
            result.__bool__.return_value = True
            result.json.return_value = {
                "posts": [{"createdAt": created_at} for created_at in posts],
                "pagination": {"hasNext": has_next},
            }
            return result

        auth = mock.Mock()
        auth.request.side_effect = [
            response(["2026-09-20T15:01:00Z", "2026-09-20T15:02:00Z"]),
            response(["2026-09-20T14:59:00Z"]),
            response(["2026-09-20T14:58:00Z"]),
        ]
        api = KarotterAPI(auth)
        api._throttle = mock.Mock()
        self.assertEqual(api.count_user_posts_since("person", boundary), 2)
        self.assertEqual(auth.request.call_count, 3)
        for call in auth.request.call_args_list:
            self.assertEqual(call.args[0], "GET")

    def test_newer_remote_history_is_never_overwritten(self):
        local = json.dumps({"timestamp": "2026-09-21T00:01:00+00:00"})
        remote = json.dumps({"timestamp": "2026-09-21T00:02:00+00:00"})
        self.assertTrue(main_module._remote_history_is_newer_or_equal(local, remote))
        self.assertFalse(main_module._remote_history_is_newer_or_equal(remote, local))

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

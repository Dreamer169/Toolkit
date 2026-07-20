import importlib.util
import io
import os
from email.message import Message
from pathlib import Path
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError


DEFAULT_SOURCE = Path(__file__).with_name("unitool-proxy.oc-live.py")
if not DEFAULT_SOURCE.exists():
    DEFAULT_SOURCE = Path(__file__).with_name("unitool_proxy.py")
SOURCE = Path(os.environ.get("UNITOOL_PROXY_PATH", DEFAULT_SOURCE))
SPEC = importlib.util.spec_from_file_location("unitool_proxy_under_test", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


EXPECTED_PUBLIC_MODELS = {
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0613",
    "gpt-3.5-turbo-16k",
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4o-mini-search",
    "text-davinci-003",
}


def http_error(body: str) -> HTTPError:
    return HTTPError("https://unitool.ai/test", 403, "Forbidden", Message(), io.BytesIO(body.encode()))


class FakePoolEvent:
    def __init__(self) -> None:
        self.waits: list[float | None] = []
        self.clears = 0

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return False

    def clear(self) -> None:
        self.clears += 1


class UnitoolProxyTests(unittest.TestCase):
    def test_public_models_are_exact_verified_allowlist(self) -> None:
        ids = {item["id"] for item in MODULE.MODELS_LIST}
        self.assertEqual(EXPECTED_PUBLIC_MODELS, ids)
        self.assertEqual(7, len(MODULE.MODELS_LIST))
        self.assertEqual("gpt-5.5", MODULE._resolve_model("chatgpt")[0])
        self.assertEqual("claude-sonnet", MODULE._resolve_model("claude")[0])

    def test_aliases_share_service_balance_block_without_global_death(self) -> None:
        entry = MODULE._make_entry("ssid-1", "account-1")
        service_id = MODULE._resolve_model("chatgpt-5.5")[0]
        alias_service_id = MODULE._resolve_model("chatgpt")[0]
        self.assertEqual(service_id, alias_service_id)

        MODULE._mark_balance_blocked(entry, service_id, secs=7200)

        self.assertTrue(MODULE._is_balance_blocked(entry, alias_service_id))
        self.assertFalse(MODULE._is_balance_blocked(entry, "gpt-4o-mini"))
        self.assertEqual(0, entry["dead_until"])
        self.assertEqual("", entry["dead_reason"])

    def test_balance_failures_try_three_distinct_accounts_without_global_mark(self) -> None:
        entries = [MODULE._make_entry(f"ssid-{index}", f"account-{index}") for index in range(10)]
        attempted: list[str] = []

        def fail_balance(entry, *args, **kwargs):
            attempted.append(entry["ssid"])
            raise http_error("Balance need: 10.1")

        with (
            mock.patch.object(MODULE, "_send_and_collect", side_effect=fail_balance),
            mock.patch.object(MODULE, "_mark_dead") as mark_dead,
            mock.patch.object(MODULE, "_invalidate_in_db") as invalidate,
        ):
            with self.assertRaisesRegex(Exception, "all ssids failed"):
                MODULE._try_service("gpt-5.5", "test", entries)

        self.assertEqual(3, len(attempted))
        self.assertEqual(3, len(set(attempted)))
        mark_dead.assert_not_called()
        invalidate.assert_not_called()
        used = [entry for entry in entries if entry["ssid"] in attempted]
        self.assertTrue(all(MODULE._is_balance_blocked(entry, "gpt-5.5") for entry in used))
        self.assertTrue(all(entry["dead_until"] == 0 for entry in entries))

    def test_free_tokens_error_is_service_scoped_and_does_not_invalidate_db(self) -> None:
        entry = MODULE._make_entry("ssid-free", "account-free")

        def fail_free_tokens(*args, **kwargs):
            raise http_error("Free tokens are over")

        with (
            mock.patch.object(MODULE, "_send_and_collect", side_effect=fail_free_tokens),
            mock.patch.object(MODULE, "_mark_dead") as mark_dead,
            mock.patch.object(MODULE, "_invalidate_in_db") as invalidate,
        ):
            with self.assertRaisesRegex(Exception, "all ssids failed"):
                MODULE._try_service("gpt-4o-mini", "test", [entry])

        self.assertTrue(MODULE._is_balance_blocked(entry, "gpt-4o-mini"))
        self.assertEqual(0, entry["dead_until"])
        mark_dead.assert_not_called()
        invalidate.assert_not_called()

    def test_expired_balance_block_is_removed(self) -> None:
        entry = MODULE._make_entry("ssid-expired", "account-expired")
        entry["_balance_blocked_until"]["gpt-5.5"] = MODULE.time.time() - 1
        self.assertFalse(MODULE._is_balance_blocked(entry, "gpt-5.5"))
        self.assertNotIn("gpt-5.5", entry["_balance_blocked_until"])

    def test_busy_account_waits_once_and_is_never_used_as_fallback(self) -> None:
        entry = MODULE._make_entry("ssid-busy", "account-busy")
        entry["_active"] = MODULE.MAX_CONCURRENCY_PER_SSID
        event = FakePoolEvent()
        with (
            mock.patch.object(MODULE, "_pool_release_event", event),
            mock.patch.object(MODULE, "_send_and_collect") as send,
        ):
            with self.assertRaisesRegex(Exception, "no_eligible_accounts"):
                MODULE._try_service("gpt-4o-mini", "test", [entry])

        self.assertEqual([MODULE.ACCOUNT_WAIT_SECONDS], event.waits)
        self.assertEqual(1, event.clears)
        send.assert_not_called()

    def test_all_globally_dead_accounts_never_reach_try_service(self) -> None:
        entry = MODULE._make_entry("ssid-dead", "account-dead")
        entry["dead_until"] = MODULE.time.time() + 600
        with (
            mock.patch.object(MODULE, "_pool", [entry]),
            mock.patch.object(MODULE, "_reload_pool_if_needed"),
            mock.patch.object(MODULE, "_try_service") as try_service,
        ):
            with self.assertRaisesRegex(Exception, "ssid pool empty"):
                MODULE._do_chat("gpt-4o-mini", [{"role": "user", "content": "test"}], None)
        try_service.assert_not_called()

    def test_non_balance_auth_error_keeps_global_account_cooldown(self) -> None:
        entry = MODULE._make_entry("ssid-auth", "account-auth")
        with (
            mock.patch.object(MODULE, "_send_and_collect", side_effect=http_error("invalid session")),
            mock.patch.object(MODULE, "_mark_dead") as mark_dead,
        ):
            with self.assertRaisesRegex(Exception, "all ssids failed"):
                MODULE._try_service("gpt-4o-mini", "test", [entry])
        mark_dead.assert_called_once_with("ssid-auth", secs=600, reason="auth_error")

    def test_accumulated_transport_errors_keep_global_account_cooldown(self) -> None:
        entry = MODULE._make_entry("ssid-transport", "account-transport")
        entry["_conn_errors"] = MODULE.MAX_CONN_ERRORS - 1
        with (
            mock.patch.object(MODULE, "_send_and_collect", side_effect=MODULE._rq.exceptions.ConnectionError("reset")),
            mock.patch.object(MODULE, "_mark_dead") as mark_dead,
            mock.patch.object(MODULE.time, "sleep"),
        ):
            with self.assertRaisesRegex(Exception, "all ssids failed"):
                MODULE._try_service("gpt-4o-mini", "test", [entry])
        mark_dead.assert_called_once_with("ssid-transport", secs=90, reason="conn_reset")

    def test_last_concurrency_slot_is_reserved_atomically(self) -> None:
        entry = MODULE._make_entry("ssid-concurrency", "account-concurrency")
        entry["_active"] = MODULE.MAX_CONCURRENCY_PER_SSID - 1
        started = threading.Event()
        release = threading.Event()
        observed_active: list[int] = []
        first_result: list[str] = []

        def fake_core(*args, **kwargs):
            observed_active.append(entry["_active"])
            started.set()
            release.wait(timeout=2)
            return "ok"

        def first_call() -> None:
            first_result.append(MODULE._try_service("gpt-4o-mini", "test", [entry]))

        with (
            mock.patch.object(MODULE, "_send_and_collect_core", side_effect=fake_core),
            mock.patch.object(MODULE, "ACCOUNT_WAIT_SECONDS", 0.01),
        ):
            thread = threading.Thread(target=first_call)
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            with self.assertRaisesRegex(Exception, "no_eligible_accounts"):
                MODULE._try_service("gpt-4o-mini", "test", [entry])
            release.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(["ok"], first_result)
        self.assertEqual([MODULE.MAX_CONCURRENCY_PER_SSID], observed_active)
        self.assertEqual(MODULE.MAX_CONCURRENCY_PER_SSID - 1, entry["_active"])


if __name__ == "__main__":
    unittest.main()

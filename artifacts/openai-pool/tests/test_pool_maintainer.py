"""
test_pool_maintainer.py  —  pool_maintainer.py unit tests
Covers:
  _get_item_type, _safe_json, _extract_account_id, _parse_time_to_epoch
  Sub2ApiMaintainer: _normalize_account_id, _is_abnormal_status,
                     _account_identity, _account_sort_key, _build_dedupe_plan
"""
import pytest
from datetime import datetime, timezone
from openai_pool_orchestrator.pool_maintainer import (
    _get_item_type,
    _safe_json,
    _extract_account_id,
    _parse_time_to_epoch,
    Sub2ApiMaintainer,
)


def _make_sm():
    return Sub2ApiMaintainer(
        base_url="http://localhost:9999",
        bearer_token="test-token",
        min_candidates=10,
    )


def _acc(id_, email="", refresh_token="", updated_at="2024-01-01T00:00:00Z"):
    return {
        "id": id_,
        "name": email,
        "extra": {"email": email},
        "credentials": {"refresh_token": refresh_token},
        "updated_at": updated_at,
    }


# ── _get_item_type ─────────────────────────────────────────────────────────────
class TestGetItemType:
    def test_returns_type_field(self):
        assert _get_item_type({"type": "oauth"}) == "oauth"

    def test_falls_back_to_typo(self):
        assert _get_item_type({"typo": "access_token"}) == "access_token"

    def test_type_takes_priority_over_typo(self):
        assert _get_item_type({"type": "oauth", "typo": "other"}) == "oauth"

    def test_empty_dict_returns_empty(self):
        assert _get_item_type({}) == ""

    def test_none_value_returns_empty(self):
        assert _get_item_type({"type": None}) == ""

    def test_always_returns_string(self):
        assert isinstance(_get_item_type({"type": 42}), str)


# ── _safe_json ─────────────────────────────────────────────────────────────────
class TestSafeJson:
    def test_valid_object(self):
        assert _safe_json('{"k": "v"}') == {"k": "v"}

    def test_numbers(self):
        assert _safe_json('{"n": 42}') == {"n": 42}

    def test_invalid_returns_empty(self):
        assert _safe_json("not json") == {}

    def test_empty_string_returns_empty(self):
        assert _safe_json("") == {}

    def test_array_returns_parsed_result(self):
        # _safe_json does not coerce non-dict results; arrays parse successfully
        result = _safe_json("[1,2,3]")
        assert result == [1, 2, 3]

    def test_partial_json_returns_empty(self):
        assert _safe_json('{"k":') == {}

    def test_null_returns_none(self):
        # json.loads("null") is None — _safe_json returns it as-is
        assert _safe_json("null") is None


# ── _extract_account_id ────────────────────────────────────────────────────────
class TestExtractAccountId:
    def test_chatgpt_account_id(self):
        assert _extract_account_id({"chatgpt_account_id": "acc-1"}) == "acc-1"

    def test_chatgptAccountId_camel(self):
        assert _extract_account_id({"chatgptAccountId": "acc-2"}) == "acc-2"

    def test_account_id(self):
        assert _extract_account_id({"account_id": "acc-3"}) == "acc-3"

    def test_accountId_camel(self):
        assert _extract_account_id({"accountId": "acc-4"}) == "acc-4"

    def test_priority_first_key_wins(self):
        assert _extract_account_id({"chatgpt_account_id": "first", "account_id": "second"}) == "first"

    def test_no_known_key_returns_none(self):
        assert _extract_account_id({"other": "val"}) is None

    def test_empty_dict_returns_none(self):
        assert _extract_account_id({}) is None

    def test_falsy_value_skipped(self):
        assert _extract_account_id({"chatgpt_account_id": "", "account_id": "fb"}) == "fb"


# ── _parse_time_to_epoch ───────────────────────────────────────────────────────
class TestParseTimeToEpoch:
    def test_iso_z_suffix(self):
        result = _parse_time_to_epoch("2024-01-15T12:00:00Z")
        assert result > 0
        expected = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        assert abs(result - expected) < 1

    def test_iso_offset(self):
        assert _parse_time_to_epoch("2024-06-01T08:30:00+08:00") > 0

    def test_plain_date_format(self):
        assert _parse_time_to_epoch("2024-03-20 10:00:00") > 0

    def test_empty_string_is_zero(self):
        assert _parse_time_to_epoch("") == 0.0

    def test_none_is_zero(self):
        assert _parse_time_to_epoch(None) == 0.0

    def test_garbage_is_zero(self):
        assert _parse_time_to_epoch("not-a-date") == 0.0

    def test_later_date_larger_epoch(self):
        t1 = _parse_time_to_epoch("2024-01-01T00:00:00Z")
        t2 = _parse_time_to_epoch("2024-12-31T00:00:00Z")
        assert t2 > t1


# ── Sub2ApiMaintainer static/pure helpers ─────────────────────────────────────
class TestSub2ApiHelpers:
    def setup_method(self):
        self.sm = _make_sm()

    def test_normalize_int(self):          assert self.sm._normalize_account_id(5) == 5
    def test_normalize_str(self):          assert self.sm._normalize_account_id("42") == 42
    def test_normalize_zero_none(self):    assert self.sm._normalize_account_id(0) is None
    def test_normalize_neg_none(self):     assert self.sm._normalize_account_id(-1) is None
    def test_normalize_none_none(self):    assert self.sm._normalize_account_id(None) is None
    def test_normalize_garbage_none(self): assert self.sm._normalize_account_id("abc") is None

    def test_error_abnormal(self):         assert self.sm._is_abnormal_status("error") is True
    def test_disabled_abnormal(self):      assert self.sm._is_abnormal_status("disabled") is True
    def test_active_normal(self):          assert self.sm._is_abnormal_status("active") is False
    def test_empty_normal(self):           assert self.sm._is_abnormal_status("") is False
    def test_none_normal(self):            assert self.sm._is_abnormal_status(None) is False
    def test_case_insensitive(self):
        assert self.sm._is_abnormal_status("ERROR") is True
        assert self.sm._is_abnormal_status("Disabled") is True

    def test_identity_email_from_extra(self):
        item = {"extra": {"email": "user@example.com"}, "credentials": {}}
        assert self.sm._account_identity(item)["email"] == "user@example.com"

    def test_identity_email_lowercased(self):
        item = {"extra": {"email": "USER@EXAMPLE.COM"}}
        assert self.sm._account_identity(item)["email"] == "user@example.com"

    def test_identity_email_from_name_fallback(self):
        item = {"name": "someone@test.org"}
        assert self.sm._account_identity(item)["email"] == "someone@test.org"

    def test_identity_refresh_token(self):
        item = {"extra": {"email": "u@x.com"}, "credentials": {"refresh_token": "rt_abc"}}
        assert self.sm._account_identity(item)["refresh_token"] == "rt_abc"

    def test_identity_empty_item(self):
        ident = self.sm._account_identity({})
        assert ident["email"] == "" and ident["refresh_token"] == ""

    def test_sort_key_returns_tuple(self):
        key = self.sm._account_sort_key({"id": 10, "updated_at": "2024-06-01T00:00:00Z"})
        assert isinstance(key, tuple) and len(key) == 2

    def test_sort_key_later_date_larger(self):
        old = {"id": 1, "updated_at": "2023-01-01T00:00:00Z"}
        new = {"id": 2, "updated_at": "2024-01-01T00:00:00Z"}
        assert self.sm._account_sort_key(new) > self.sm._account_sort_key(old)

    def test_sort_key_higher_id_wins_same_time(self):
        lo = {"id": 1, "updated_at": "2024-01-01T00:00:00Z"}
        hi = {"id": 9, "updated_at": "2024-01-01T00:00:00Z"}
        assert self.sm._account_sort_key(hi) > self.sm._account_sort_key(lo)


# ── _build_dedupe_plan ─────────────────────────────────────────────────────────
class TestBuildDedupePlan:
    def setup_method(self):
        self.sm = _make_sm()

    def _plan(self, accounts):
        return self.sm._build_dedupe_plan(accounts)

    def test_empty_list(self):
        p = self._plan([])
        assert p["duplicate_groups"] == 0 and p["delete_ids"] == []

    def test_single_account_no_dup(self):
        assert self._plan([_acc(1, "a@x.com", "rt1")])["duplicate_groups"] == 0

    def test_unique_accounts_no_dup(self):
        accs = [_acc(i, f"u{i}@x.com", f"rt{i}") for i in range(1, 4)]
        assert self._plan(accs)["duplicate_groups"] == 0

    def test_duplicate_by_email(self):
        accs = [_acc(1, "dup@x.com", "rt1"), _acc(2, "dup@x.com", "rt2")]
        p = self._plan(accs)
        assert p["duplicate_groups"] == 1
        assert p["duplicate_accounts"] == 2
        assert len(p["delete_ids"]) == 1

    def test_duplicate_by_refresh_token(self):
        accs = [_acc(1, "a@x.com", "shared"), _acc(2, "b@x.com", "shared")]
        p = self._plan(accs)
        assert p["duplicate_groups"] == 1
        assert len(p["delete_ids"]) == 1

    def test_keeps_highest_id_same_timestamp(self):
        accs = [
            _acc(1, "dup@x.com", "rt1", "2024-01-01T00:00:00Z"),
            _acc(9, "dup@x.com", "rt2", "2024-01-01T00:00:00Z"),
        ]
        p = self._plan(accs)
        assert 1 in p["delete_ids"]
        assert 9 not in p["delete_ids"]

    def test_keeps_latest_updated_account(self):
        accs = [
            _acc(5, "dup@x.com", "rt1", "2023-01-01T00:00:00Z"),
            _acc(3, "dup@x.com", "rt2", "2024-06-01T00:00:00Z"),
        ]
        p = self._plan(accs)
        assert 5 in p["delete_ids"]
        assert 3 not in p["delete_ids"]

    def test_three_duplicates_keeps_one(self):
        accs = [_acc(i, "tri@x.com", f"rt{i}") for i in range(1, 4)]
        p = self._plan(accs)
        assert p["duplicate_groups"] == 1
        assert p["duplicate_accounts"] == 3
        assert len(p["delete_ids"]) == 2

    def test_multiple_independent_groups(self):
        accs = [
            _acc(1, "a@x.com", "rt1"), _acc(2, "a@x.com", "rt2"),
            _acc(3, "b@x.com", "rt3"), _acc(4, "b@x.com", "rt4"),
        ]
        p = self._plan(accs)
        assert p["duplicate_groups"] == 2
        assert len(p["delete_ids"]) == 2

    def test_email_and_rt_shared_single_group(self):
        accs = [_acc(1, "x@x.com", "shared"), _acc(2, "x@x.com", "shared")]
        p = self._plan(accs)
        assert p["duplicate_groups"] == 1
        assert len(p["delete_ids"]) == 1

    def test_invalid_id_accounts_ignored(self):
        accs = [
            {"id": None, "extra": {"email": "ghost@x.com"}, "credentials": {}},
            {"id": 0,    "extra": {"email": "ghost@x.com"}, "credentials": {}},
            _acc(5, "real@x.com", "rt5"),
        ]
        assert self._plan(accs)["duplicate_groups"] == 0

    def test_required_keys_present(self):
        p = self._plan([])
        for key in ("duplicate_groups", "duplicate_accounts", "delete_ids", "groups_preview"):
            assert key in p

    def test_groups_preview_has_email_and_ids(self):
        accs = [_acc(1, "preview@x.com", "rt1"), _acc(2, "preview@x.com", "rt2")]
        p = self._plan(accs)
        assert len(p["groups_preview"]) == 1
        g = p["groups_preview"][0]
        assert "preview@x.com" in g.get("emails", [])
        assert "keep_id" in g and "delete_ids" in g

"""
test_server_config.py  —  server.py pure-function unit tests
Tests: _as_bool, _normalize_sub2api_maintain_actions,
       _describe_sub2api_maintain_actions, _format_sub2api_maintain_result_message
"""
import pytest
from openai_pool_orchestrator.server import (
    _as_bool,
    _normalize_sub2api_maintain_actions,
    _describe_sub2api_maintain_actions,
    _format_sub2api_maintain_result_message,
)

DEFAULTS = {
    "refresh_abnormal_accounts": True,
    "delete_abnormal_accounts": True,
    "dedupe_duplicate_accounts": True,
}


class TestAsBool:
    def test_true_bool(self):           assert _as_bool(True) is True
    def test_one_int(self):             assert _as_bool(1) is True
    def test_string_true(self):         assert _as_bool("true") is True
    def test_string_yes(self):          assert _as_bool("yes") is True
    def test_string_on(self):           assert _as_bool("on") is True
    def test_string_1(self):            assert _as_bool("1") is True
    def test_string_TRUE_upper(self):   assert _as_bool("TRUE") is True
    def test_false_bool(self):          assert _as_bool(False) is False
    def test_zero_int(self):            assert _as_bool(0) is False
    def test_string_false(self):        assert _as_bool("false") is False
    def test_string_no(self):           assert _as_bool("no") is False
    def test_string_off(self):          assert _as_bool("off") is False
    def test_string_0(self):            assert _as_bool("0") is False
    def test_empty_string(self):        assert _as_bool("") is False
    def test_none_default_false(self):  assert _as_bool(None) is False
    def test_none_default_true(self):   assert _as_bool(None, default=True) is True
    def test_unknown_default_false(self):  assert _as_bool("maybe") is False
    def test_unknown_default_true(self):   assert _as_bool("maybe", default=True) is True
    def test_float_truthy(self):        assert _as_bool(1.5) is True
    def test_float_zero(self):          assert _as_bool(0.0) is False


class TestNormalizeActions:
    def test_none_returns_all_defaults(self):
        assert _normalize_sub2api_maintain_actions(None) == DEFAULTS

    def test_empty_dict_returns_all_defaults(self):
        assert _normalize_sub2api_maintain_actions({}) == DEFAULTS

    def test_all_false_overrides(self):
        raw = {k: False for k in DEFAULTS}
        assert _normalize_sub2api_maintain_actions(raw) == {k: False for k in DEFAULTS}

    def test_partial_override_keeps_others(self):
        raw = {"dedupe_duplicate_accounts": False}
        result = _normalize_sub2api_maintain_actions(raw)
        assert result["refresh_abnormal_accounts"] is True
        assert result["delete_abnormal_accounts"] is True
        assert result["dedupe_duplicate_accounts"] is False

    def test_string_false_coerced(self):
        raw = {"refresh_abnormal_accounts": "false"}
        assert _normalize_sub2api_maintain_actions(raw)["refresh_abnormal_accounts"] is False

    def test_string_true_coerced(self):
        raw = {k: "true" for k in DEFAULTS}
        result = _normalize_sub2api_maintain_actions(raw)
        assert all(v is True for v in result.values())

    def test_integer_zero_coerced(self):
        raw = {"refresh_abnormal_accounts": 0}
        assert _normalize_sub2api_maintain_actions(raw)["refresh_abnormal_accounts"] is False

    def test_non_dict_returns_defaults(self):
        assert _normalize_sub2api_maintain_actions("bad") == DEFAULTS

    def test_result_has_all_three_keys(self):
        for raw in [None, {}, {"refresh_abnormal_accounts": True}]:
            assert set(_normalize_sub2api_maintain_actions(raw).keys()) == set(DEFAULTS.keys())


class TestDescribeActions:
    def test_all_true_includes_all_labels(self):
        desc = _describe_sub2api_maintain_actions(DEFAULTS)
        assert "异常测活" in desc
        assert "异常清理" in desc
        assert "重复清理" in desc

    def test_all_false_returns_no_action(self):
        assert _describe_sub2api_maintain_actions({k: False for k in DEFAULTS}) == "无动作"

    def test_only_refresh_enabled(self):
        raw = {"refresh_abnormal_accounts": True, "delete_abnormal_accounts": False, "dedupe_duplicate_accounts": False}
        desc = _describe_sub2api_maintain_actions(raw)
        assert "异常测活" in desc
        assert "异常清理" not in desc

    def test_only_delete_enabled(self):
        raw = {"refresh_abnormal_accounts": False, "delete_abnormal_accounts": True, "dedupe_duplicate_accounts": False}
        desc = _describe_sub2api_maintain_actions(raw)
        assert "异常清理" in desc
        assert "异常测活" not in desc

    def test_only_dedupe_enabled(self):
        raw = {"refresh_abnormal_accounts": False, "delete_abnormal_accounts": False, "dedupe_duplicate_accounts": True}
        assert "重复清理" in _describe_sub2api_maintain_actions(raw)

    def test_none_input_uses_defaults(self):
        desc = _describe_sub2api_maintain_actions(None)
        assert "异常测活" in desc and "异常清理" in desc and "重复清理" in desc

    def test_labels_joined_with_chinese_pause(self):
        assert "\u3001" in _describe_sub2api_maintain_actions(DEFAULTS)


class TestFormatMaintainResult:
    def _r(self, **kw):
        base = {"actions": DEFAULTS, "error_count": 5, "refreshed": 3,
                "duplicate_groups": 2, "deleted_ok": 4, "deleted_fail": 1, "duration_ms": 2500}
        base.update(kw)
        return base

    def test_contains_error_count(self):
        assert "7" in _format_sub2api_maintain_result_message(self._r(error_count=7))

    def test_contains_refreshed(self):
        assert "3" in _format_sub2api_maintain_result_message(self._r(refreshed=3))

    def test_contains_deleted_ok(self):
        assert "4" in _format_sub2api_maintain_result_message(self._r(deleted_ok=4))

    def test_manual_mode_prefix(self):
        assert "维护完成" in _format_sub2api_maintain_result_message(self._r(), auto=False)

    def test_auto_mode_prefix(self):
        assert "自动维护" in _format_sub2api_maintain_result_message(self._r(), auto=True)

    def test_duration_displayed_in_seconds(self):
        msg = _format_sub2api_maintain_result_message(self._r(duration_ms=3000))
        assert "3.0" in msg

    def test_zero_values_no_crash(self):
        result = {"actions": {k: False for k in DEFAULTS}, "error_count": 0,
                  "refreshed": 0, "duplicate_groups": 0, "deleted_ok": 0,
                  "deleted_fail": 0, "duration_ms": 0}
        msg = _format_sub2api_maintain_result_message(result)
        assert isinstance(msg, str) and len(msg) > 0

    def test_missing_keys_no_crash(self):
        msg = _format_sub2api_maintain_result_message({})
        assert isinstance(msg, str)

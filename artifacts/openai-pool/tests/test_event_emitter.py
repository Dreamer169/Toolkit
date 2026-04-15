"""
test_event_emitter.py  —  EventEmitter unit tests
"""
import queue
import threading
import pytest
from openai_pool_orchestrator.register import EventEmitter


class TestQueueEmit:
    def setup_method(self):
        self.q = queue.Queue()
        self.em = EventEmitter(q=self.q)

    def _get(self):
        return self.q.get_nowait()

    def test_info_level_in_queue(self):
        self.em.emit("info", "hello")
        evt = self._get()
        assert evt["level"] == "info"
        assert evt["message"] == "hello"

    def test_success_level(self):
        self.em.emit("success", "done")
        assert self._get()["level"] == "success"

    def test_error_level(self):
        self.em.emit("error", "boom")
        assert self._get()["level"] == "error"

    def test_warn_level(self):
        self.em.emit("warn", "careful")
        assert self._get()["level"] == "warn"

    def test_event_has_ts_field(self):
        self.em.emit("info", "ping")
        evt = self._get()
        assert "ts" in evt and evt["ts"]

    def test_step_field_propagated(self):
        self.em.emit("info", "s", step="check_proxy")
        assert self._get()["step"] == "check_proxy"

    def test_empty_step_default(self):
        self.em.emit("info", "no step")
        assert self._get()["step"] == ""

    def test_extra_kwargs_in_event(self):
        self.em.emit("info", "extra", step="s", worker_id=3)
        assert self._get()["worker_id"] == 3

    def test_shorthand_info(self):
        self.em.info("hi")
        assert self._get()["level"] == "info"

    def test_shorthand_success(self):
        self.em.success("ok")
        assert self._get()["level"] == "success"

    def test_shorthand_error(self):
        self.em.error("err")
        assert self._get()["level"] == "error"

    def test_shorthand_warn(self):
        self.em.warn("w")
        assert self._get()["level"] == "warn"

    def test_multiple_events_ordered(self):
        for i in range(5):
            self.em.emit("info", f"msg-{i}")
        msgs = [self.q.get_nowait()["message"] for _ in range(5)]
        assert msgs == [f"msg-{i}" for i in range(5)]


class TestBind:
    def setup_method(self):
        self.q = queue.Queue()
        self.em = EventEmitter(q=self.q)

    def test_bind_injects_default_field(self):
        bound = self.em.bind(worker_id=7)
        bound.emit("info", "bound")
        assert self.q.get_nowait()["worker_id"] == 7

    def test_bind_multiple_defaults(self):
        bound = self.em.bind(worker_id=2, run_id="abc")
        bound.emit("info", "multi")
        evt = self.q.get_nowait()
        assert evt["worker_id"] == 2
        assert evt["run_id"] == "abc"

    def test_bind_chained(self):
        b = self.em.bind(worker_id=1).bind(run_id="xyz")
        b.emit("info", "chained")
        evt = self.q.get_nowait()
        assert evt["worker_id"] == 1
        assert evt["run_id"] == "xyz"

    def test_bind_does_not_mutate_parent(self):
        _ = self.em.bind(worker_id=99)
        self.em.emit("info", "parent")
        assert "worker_id" not in self.q.get_nowait()

    def test_emit_kwarg_overrides_bound_default(self):
        bound = self.em.bind(worker_id=1)
        bound.emit("info", "override", worker_id=42)
        assert self.q.get_nowait()["worker_id"] == 42

    def test_bind_none_value_excluded(self):
        bound = self.em.bind(worker_id=None)
        bound.emit("info", "none val")
        assert "worker_id" not in self.q.get_nowait()


class TestCliMode:
    def test_cli_prints_to_stdout(self, capsys):
        em = EventEmitter(cli_mode=True)
        em.emit("info", "cli output")
        assert "cli output" in capsys.readouterr().out

    def test_cli_success_prefix(self, capsys):
        em = EventEmitter(cli_mode=True)
        em.emit("success", "all good")
        assert "[+]" in capsys.readouterr().out

    def test_cli_error_prefix(self, capsys):
        em = EventEmitter(cli_mode=True)
        em.emit("error", "fail")
        assert "[Error]" in capsys.readouterr().out

    def test_cli_warn_prefix(self, capsys):
        em = EventEmitter(cli_mode=True)
        em.emit("warn", "warning")
        assert "[!]" in capsys.readouterr().out

    def test_no_queue_no_crash(self):
        em = EventEmitter()
        em.emit("info", "no queue")   # must not raise


class TestQueueFull:
    def test_full_queue_silently_drops(self):
        q = queue.Queue(maxsize=1)
        em = EventEmitter(q=q)
        em.emit("info", "first")
        em.emit("info", "second")    # dropped silently
        assert q.qsize() == 1


class TestThreadSafety:
    def test_concurrent_emits_do_not_crash(self):
        q = queue.Queue(maxsize=1000)
        em = EventEmitter(q=q)
        errors = []

        def worker(idx):
            try:
                for _ in range(20):
                    em.emit("info", f"worker-{idx}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors

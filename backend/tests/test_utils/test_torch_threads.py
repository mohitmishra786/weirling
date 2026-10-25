"""PyTorch threads follow the container's CPU quota, not the host's cores."""

from __future__ import annotations

import builtins
import io

from app.utils import embeddings


def _cgroup(monkeypatch, content: str | None):
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if path == "/sys/fs/cgroup/cpu.max":
            if content is None:
                raise FileNotFoundError(path)
            return io.StringIO(content)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_quota_rounds_up_to_whole_cpus(monkeypatch):
    import os

    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(64)), raising=False)
    _cgroup(monkeypatch, "150000 100000\n")
    assert embeddings.container_cpus() == 2
    _cgroup(monkeypatch, "200000 100000\n")
    assert embeddings.container_cpus() == 2


def test_no_quota_falls_back_to_the_affinity_mask(monkeypatch):
    _cgroup(monkeypatch, "max 100000\n")
    monkeypatch.setattr(
        embeddings.os if hasattr(embeddings, "os") else __import__("os"),
        "sched_getaffinity",
        lambda _pid: {0, 1, 2},
        raising=False,
    )
    assert embeddings.container_cpus() == 3


def test_explicit_setting_wins(monkeypatch):
    import sys
    import types

    calls = []
    fake_torch = types.SimpleNamespace(set_num_threads=calls.append)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.delenv("TORCH_NUM_THREADS", raising=False)
    assert embeddings.limit_torch_threads() == 3
    assert calls == [3]

    monkeypatch.delenv("OMP_NUM_THREADS")
    _cgroup(monkeypatch, "100000 100000\n")
    assert embeddings.limit_torch_threads() == 1


def test_affinity_caps_a_larger_quota(monkeypatch):
    import os

    _cgroup(monkeypatch, "800000 100000\n")  # quota allows 8
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1}, raising=False)
    assert embeddings.container_cpus() == 2


def test_zero_or_junk_thread_settings_are_ignored(monkeypatch):
    import sys
    import types

    calls = []
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=calls.append))
    monkeypatch.setenv("TORCH_NUM_THREADS", "0")
    monkeypatch.setenv("OMP_NUM_THREADS", "lots")
    monkeypatch.setattr(embeddings, "container_cpus", lambda: 2)
    assert embeddings.limit_torch_threads() == 2
    assert calls == [2]

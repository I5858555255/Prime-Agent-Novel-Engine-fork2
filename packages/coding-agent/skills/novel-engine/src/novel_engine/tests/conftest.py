"""pytest conftest: load .env before any test collection + block real network by default."""
import socket
from novel_engine.core.env_bootstrap import load_project_env


def pytest_configure(config):
    load_project_env()
    config.addinivalue_line("markers", "allow_network: allow real network access in this test")


class _BlockedHTTPXClient:
    """Stand-in for httpx.Client that raises on any network activity."""
    def __init__(self, *args, **kwargs):
        pass
    def get(self, *a, **k):
        raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")
    def post(self, *a, **k):
        raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")
    def request(self, *a, **k):
        raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")
    def send(self, *a, **k):
        raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")
    def stream(self, *a, **k):
        raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass


_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


def _block_connect(self, *args, **kwargs):
    raise RuntimeError("real network blocked in tests (use @pytest.mark.allow_network to bypass)")


def _block_connect_ex(self, *args, **kwargs):
    return -2  # ECONNREFUSED-like


def _patch_socket(block: bool):
    if block:
        socket.socket.connect = _block_connect
        socket.socket.connect_ex = _block_connect_ex
    else:
        socket.socket.connect = _REAL_CONNECT
        socket.socket.connect_ex = _REAL_CONNECT_EX


# Block network at module import time (pytest collection)
_patch_socket(True)


def pytest_runtest_setup(item):
    """Pre-test hook: ensure network is blocked unless test is marked allow_network."""
    if item.get_closest_marker("allow_network"):
        _patch_socket(False)
    else:
        _patch_socket(True)


def pytest_runtest_teardown(item, nextitem):
    """Post-test hook: restore blocking."""
    _patch_socket(True)

"""Recoverable publication fence for chapter files and their state side effects."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("wb") as stream:
            stream.write(content if isinstance(content, bytes) else content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def publication_lock(root, lock_name="publication"):
    root = Path(root).resolve()
    key = (str(root), lock_name)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_LOCAL, "held", set())
        if key in held:
            yield
            return
        path = root / "runtime" / f".{lock_name}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            if fcntl is not None:
                fcntl.flock(stream, fcntl.LOCK_EX)
            else:
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            _LOCAL.held = held | {key}
            try:
                yield
            finally:
                _LOCAL.held = held
                if fcntl is not None:
                    fcntl.flock(stream, fcntl.LOCK_UN)
                else:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _restore(root, folder, manifest, connections=None):
    connections = connections or {}
    for item in manifest["files"]:
        target = (root / item["path"]).resolve()
        if not target.is_relative_to(root):
            raise ValueError("publication recovery path escapes project")
        snapshot = folder / item["snapshot"]
        if item.get("sqlite"):
            connection = connections.get(str(target))
            own_connection = connection is None
            if own_connection:
                connection = sqlite3.connect(str(target))
            try:
                connection.rollback()
                with sqlite3.connect(str(snapshot)) as source:
                    source.backup(connection)
            finally:
                if own_connection:
                    connection.close()
        elif item["existed"]:
            atomic_write(target, snapshot.read_bytes())
        else:
            target.unlink(missing_ok=True)


def recover_publications(root):
    root = Path(root).resolve()
    if str(root) in getattr(_LOCAL, "transactions", set()):
        return
    with publication_lock(root):
        directory = root / "runtime" / "publication_transactions"
        for path in sorted(directory.glob("*/manifest.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["status"] == "prepared":
                _restore(root, path.parent, manifest)
            shutil.rmtree(path.parent)


@contextmanager
def publication_transaction(root, chapter, paths, databases=None):
    root = Path(root).resolve()
    databases = databases or {}
    connections = {str(Path(path).resolve()): conn for path, conn in databases.items()}
    with publication_lock(root):
        held = getattr(_LOCAL, "transactions", set())
        if str(root) in held:
            yield
            return
        recover_publications(root)
        folder = root / "runtime" / "publication_transactions" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        manifest = {"chapter": chapter, "status": "prepared", "files": []}
        files = set(Path(p).resolve() for p in paths) | set(Path(p) for p in connections)
        try:
            for index, target in enumerate(sorted(files)):
                if not target.is_relative_to(root):
                    raise ValueError("publication path escapes project")
                item = {"path": str(target.relative_to(root)), "existed": target.exists(),
                        "snapshot": f"{index}.bak", "sqlite": str(target) in connections}
                backup = folder / item["snapshot"]
                if item["sqlite"]:
                    if connections[str(target)].in_transaction:
                        raise RuntimeError("cannot publish with an uncommitted SQLite transaction")
                    with sqlite3.connect(str(backup)) as dest:
                        connections[str(target)].backup(dest)
                elif item["existed"]:
                    atomic_write(backup, target.read_bytes())
                manifest["files"].append(item)
            atomic_write(folder / "manifest.json", json.dumps(manifest))
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        _LOCAL.transactions = held | {str(root)}
        try:
            yield
            manifest["status"] = "committed"
            atomic_write(folder / "manifest.json", json.dumps(manifest))
        except BaseException:
            _restore(root, folder, manifest, connections)
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            _LOCAL.transactions = held
        shutil.rmtree(folder, ignore_errors=True)

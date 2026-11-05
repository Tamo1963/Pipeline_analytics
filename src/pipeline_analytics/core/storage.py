"""Safe file writes shared by the rate library and the project store.

* :func:`atomic_write_text` writes to a temporary file in the same directory,
  flushes it to disk and renames it over the target with ``os.replace``.
  Readers see the old file or the new file, never a partial one.
* :func:`file_lock` is an exclusive advisory lock on ``<path>.lock`` that
  serialises read-modify-write cycles between processes (for example two
  Streamlit sessions saving the same project).
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return path


class LockTimeoutError(TimeoutError):
    pass


@contextlib.contextmanager
def file_lock(path: str | Path, timeout_s: float = 10.0) -> Iterator[None]:
    """Exclusive lock on ``<path>.lock`` (fcntl on POSIX, msvcrt on Windows)."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")  # noqa: SIM115 - held for the lock's lifetime
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                _lock(f)
                break
            except OSError:
                if time.monotonic() > deadline:
                    msg = f"could not lock {lock_path} within {timeout_s} s"
                    raise LockTimeoutError(msg) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(f)
    finally:
        f.close()


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock(f: object) -> None:
        f.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]

    def _unlock(f: object) -> None:
        f.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _lock(f: object) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]

    def _unlock(f: object) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]

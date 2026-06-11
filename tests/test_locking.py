import stat
import subprocess
import sys
import textwrap

from engram.core.locking import store_lock


def _try_lock_script(lock_path) -> str:
    return textwrap.dedent(
        f"""
        import fcntl, os
        fd = os.open({str(lock_path)!r}, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("acquired")
        except OSError:
            print("blocked")
        """
    )


def test_lock_file_created_with_0600(tmp_path):
    with store_lock(tmp_path):
        lock = tmp_path / ".lock"
        assert lock.exists()
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_store_lock_is_reentrant(tmp_path):
    with store_lock(tmp_path):
        with store_lock(tmp_path):
            assert (tmp_path / ".lock").exists()
    # Released cleanly at depth 0 — a fresh acquire must still succeed.
    with store_lock(tmp_path):
        pass


def test_lock_excludes_other_process(tmp_path):
    script = _try_lock_script(tmp_path / ".lock")
    with store_lock(tmp_path):
        held = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert held.stdout.strip() == "blocked"
    freed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert freed.stdout.strip() == "acquired"

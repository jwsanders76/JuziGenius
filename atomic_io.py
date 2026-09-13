"""
Atomic JSON writes.

Every save in this project used to be `open(path, "w")` followed by
`json.dump(...)`. That truncates the file to zero bytes *before* writing a
single one, which opens a window where the file on disk is empty or half
written. Anything that kills the process inside that window -- an OOM kill on
a 458 MB droplet, a full disk, a power loss, a deploy restarting the service
mid-request -- leaves the file destroyed rather than merely stale. For a
brain.json that is one learner's entire SRS history; for accounts.json it is
every account at once.

`write_json()` closes that window. It writes to a temporary file in the same
directory, flushes it all the way down to the platter, then renames it over
the target. `os.replace()` is atomic on POSIX: a reader sees either the whole
old file or the whole new one, never a torn mixture. Same directory is not
incidental -- rename is only atomic within a single filesystem, so a temp file
in /tmp would silently become a copy-then-delete and reintroduce the window.

The directory fsync at the end is what makes the *rename* durable. Without it
the new contents survive a crash but the rename may not, and some filesystems
will hand back the old file as though nothing happened.

The cost is one extra fsync per save. These files are a few hundred KB at
most and saves are per-interaction, not per-frame, so this is not a hot path.
"""
import json
import os
import stat
import tempfile


def write_json(path, data, indent=4):
    """
    Serialize `data` to `path` as UTF-8 JSON, atomically.

    Either the file ends up complete and correct, or it is left exactly as it
    was. There is no state in between, and no partially-written file is ever
    visible under the real name.
    """
    path = os.path.abspath(path)
    directory = os.path.dirname(path)

    # Preserve the existing file's permissions. mkstemp creates 0600, so
    # without this a world-readable file would silently become owner-only on
    # its next save -- the kind of change that breaks something weeks later
    # and looks unrelated.
    mode = None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        pass

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        # The target is untouched at this point, whatever went wrong. Take the
        # temp file with us so a failing disk doesn't litter the directory.
        # BaseException, not Exception: a KeyboardInterrupt mid-write should
        # clean up too.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    _fsync_directory(directory)


def _fsync_directory(directory):
    """
    Force the rename itself to disk. Best-effort: some filesystems refuse to
    open a directory for this, and a failure here means the write is still
    correct, just not yet guaranteed durable across a power cut. Not worth
    failing an otherwise successful save over.
    """
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)

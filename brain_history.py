"""
Per-account save history for brain.json.

atomic_io.write_json makes it impossible for a crash to corrupt a save. It
does nothing about a save that is complete and wrong: a logic bug quietly
writing bad data (the runaway review intervals found in September 2026 were
exactly that), a Start Over someone regrets, a one-off repair script with a
mistake in it. The nightly off-site backup covers those at one-day
granularity. This covers the last two days at ten-minute granularity, on the
machine itself, so it can be undone in seconds.

Every write of a brain.json goes through save_brain(). Just before the file
is replaced, the version on disk is kept as

    <account directory>/brain-history/brain-<UTC time that version was saved>.json

provided the newest snapshot is at least SNAPSHOT_INTERVAL old. Snapshots
older than RETENTION are pruned, except the newest MIN_KEEP, which are kept
whatever their age -- someone returning after a week still has the state they
left, even though it is older than two days.

Each snapshot is a real copy, written to a temporary file and renamed into
place so a crash can never leave a half-written one. Hard links were tried
first -- write_json always replaces a file rather than editing it, so the
outgoing version could have been kept for free under a second name -- and
rejected: anything that DOES edit a brain.json in place, such as an operator
fixing one by hand in vim (which deliberately writes in place to keep hard
links intact), would silently rewrite the snapshot too, destroying exactly the
restore point needed afterwards. A copy is at most a few hundred KB, so the
safety costs nothing worth saving.

History is best-effort by design. A failure to snapshot or prune is logged
and the save goes ahead regardless: losing a snapshot is a small loss, but
refusing a save would lose the learner's actual practice.

Snapshots are restored with restore_brain.py.
"""
import filecmp
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from atomic_io import write_json

HISTORY_DIRNAME = "brain-history"
SNAPSHOT_INTERVAL = timedelta(minutes=10)
RETENTION = timedelta(hours=48)
MIN_KEEP = 3
# Two days at one snapshot per ten minutes is 288. Only a clock jumping
# around could produce more; this stops anything like that filling the disk.
MAX_SNAPSHOTS = 300

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_NAME_RE = re.compile(r"^brain-(\d{8}T\d{6}Z)\.json$")


def save_brain(path, data, force_snapshot=False):
    """
    Writes a brain.json atomically, first keeping the outgoing version if one
    is due. Pass force_snapshot=True for anything destructive -- Start Over,
    an operator reset, a bulk repair -- so the state it replaces is kept even
    when the last snapshot is only minutes old. Otherwise practice done in
    those minutes would be exactly what a regretted reset can't bring back.
    """
    snapshot(path, force=force_snapshot)
    write_json(path, data)


def history_dir(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), HISTORY_DIRNAME)


def stamp(saved_at):
    """The timestamp form used in snapshot names and on restore_brain.py's command line."""
    return saved_at.strftime(_STAMP_FORMAT)


def list_snapshots(path):
    """[(saved_at, snapshot_path)] for one brain.json, oldest first."""
    directory = history_dir(path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in names:
        match = _NAME_RE.match(name)
        if match:
            saved_at = datetime.strptime(match.group(1), _STAMP_FORMAT).replace(tzinfo=timezone.utc)
            found.append((saved_at, os.path.join(directory, name)))
    return sorted(found)


def snapshot(path, now=None, force=False):
    """
    Keeps the current contents of `path` as a snapshot if one is due -- or
    unconditionally with `force`, which restore_brain.py uses so the state a
    restore replaces is always recoverable -- then prunes. Returns the
    snapshot's path if one now exists for the current contents, else None.
    Never raises.
    """
    try:
        if not os.path.isfile(path):
            return None
        now = now or datetime.now(timezone.utc)
        existing = list_snapshots(path)
        result = None
        if force or not existing or now - existing[-1][0] >= SNAPSHOT_INTERVAL:
            # Named for when this version was saved, not when it was set
            # aside, which is what anyone choosing a restore point needs.
            saved_at = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
            directory = history_dir(path)
            os.makedirs(directory, exist_ok=True)
            result = os.path.join(directory, f"brain-{stamp(saved_at)}.json")
            # Names are only precise to the second, so two saves within one
            # second would collide. A name already holding identical contents
            # means this version is kept; one holding different contents must
            # not swallow it, least of all on a forced snapshot.
            while os.path.exists(result) and not filecmp.cmp(result, path, shallow=False):
                saved_at += timedelta(seconds=1)
                result = os.path.join(directory, f"brain-{stamp(saved_at)}.json")
            if not os.path.exists(result):
                _copy_atomically(path, result)
        _prune(path, now)
        return result
    except Exception as e:
        # The exception type only: its message would include the account's
        # directory name, which for a /u/<slug>/ account is its credential.
        print(f"brain history: snapshot skipped ({type(e).__name__})", flush=True)
        return None


def _copy_atomically(source, destination):
    """Copies to a temporary name first, so a snapshot is either whole or absent."""
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(destination), prefix=".tmp-")
    os.close(fd)
    try:
        shutil.copy2(source, tmp_path)
        os.replace(tmp_path, destination)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _prune(path, now):
    snapshots = list_snapshots(path)
    protected_from = max(0, len(snapshots) - MIN_KEEP)
    doomed = [snapshot_path for index, (saved_at, snapshot_path) in enumerate(snapshots)
              if index < protected_from and now - saved_at > RETENTION]
    survivors = [snapshot_path for _, snapshot_path in snapshots if snapshot_path not in doomed]
    if len(survivors) > MAX_SNAPSHOTS:
        doomed += survivors[:len(survivors) - MAX_SNAPSHOTS]
    for snapshot_path in doomed:
        try:
            os.unlink(snapshot_path)
        except OSError:
            pass

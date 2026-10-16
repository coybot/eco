"""outbox.py - deliver photos and chat replies when the link allows, never in the mission's way.

Before this, every photo was uploaded inline, once, by the thread flying the
mission: out of WiFi range the upload failed, the photo was dropped from the
survey (left in /tmp, never retried), and a slow link held the aircraft over a
cell for as long as the S3 client cared to retry. The reply message went out
on MQTT with nothing behind it if the process restarted before reconnecting.

Now capture and delivery are separate:

  * enqueue_file()/enqueue_message() write the item to disk under the
    aircraft's outbox directory and return at once. Nothing on the flying
    thread waits for the network.
  * One background worker sends items oldest-first, retrying with backoff for
    as long as it takes. Oldest-first is what makes the reply arrive AFTER the
    photos it lists: it was queued after them, so the app never gets an album
    of links that do not exist yet.
  * Items are files, so a reboot loses nothing; the next daemon start drains
    what is left.

An item is `<seq>.json` (metadata, written last and atomically, so its
presence means the item is complete) plus `<seq>.data` for a file's bytes.
A permanent failure (the server keeps refusing it) is moved to `failed/`
after a few attempts rather than blocking everything behind it.

Python 3.8 (the aircraft's companion runs it): no `X | Y`, no builtin generics
at runtime.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:          # not on the aircraft (Linux); tests on other OSes
    fcntl = None

# Retry pacing for transient failures (no route, timeout, 5xx): fast at first so
# a brief dropout costs seconds, capped so a long one is not a busy loop.
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0
# A permanent refusal (403, 404 bucket, ...) is retried this many times before
# the item is set aside in failed/, so one bad item cannot hold up the rest.
MAX_PERMANENT_ATTEMPTS = 5
# A reply published while connected that has not been acknowledged in this long
# is published again. Not sooner: the MQTT client keeps its own copy of an
# unacknowledged message and resends it on reconnect, so publishing again early
# only puts the same reply in the chat twice.
REPUBLISH_AFTER_S = 600.0
# Refuse to queue (and say so) rather than fill the root filesystem.
MIN_FREE_BYTES = 200 * 1024 * 1024


class PermanentError(Exception):
    """The far end refused this item for a reason retrying will not fix."""


# uploader(data_path, key, content_type) -> None; raises PermanentError for a
# refusal, anything else for a transient failure.
Uploader = Callable[[str, str, str], None]
# publisher(topic, payload, qos) -> a future-like with .done()/.exception(), or None.
Publisher = Callable[[str, str, int], Any]


class Outbox:
    def __init__(self, directory: Path, uploader: Optional[Uploader] = None,
                 log: Callable[[str], None] = print):
        self.dir = Path(directory)
        self.items = self.dir / "items"
        self.failed = self.dir / "failed"
        self.items.mkdir(parents=True, exist_ok=True)
        self.failed.mkdir(parents=True, exist_ok=True)
        self._uploader = uploader
        self._publisher: Optional[Publisher] = None
        self._connected: Callable[[], bool] = lambda: True
        self._log = log
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_fd: Optional[int] = None
        self._seq = itertools.count()
        self._state_lock = threading.Lock()
        # Outstanding MQTT publishes: item name -> (future, published_at)
        self._inflight: Dict[str, Tuple[Any, float]] = {}
        self._attempts: Dict[str, int] = {}
        self._next_try = 0.0
        self._backoff = BACKOFF_START_S
        self.sent = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ queueing

    def _name(self) -> str:
        # Wall-clock ns + a per-process counter + a random tail: sorts in queue
        # order, and two writers (daemon + a script) cannot collide.
        return "%020d-%06d-%s" % (time.time_ns(), next(self._seq) % 1000000, uuid.uuid4().hex[:6])

    def _commit(self, name: str, meta: Dict[str, Any]) -> None:
        tmp = self.items / (name + ".json.tmp")
        with open(tmp, "w") as f:
            json.dump(meta, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.items / (name + ".json"))
        self._wake.set()

    def _room(self, need: int) -> bool:
        try:
            free = shutil.disk_usage(str(self.dir)).free
        except OSError:
            return True
        return free - need > MIN_FREE_BYTES

    def enqueue_file(self, src: str, key: str, content_type: str, url: str) -> bool:
        """Queue a copy of `src` for upload under `key`. The caller may delete
        `src` afterwards. False (nothing queued) only if the disk is full or the
        file cannot be read."""
        try:
            size = os.path.getsize(src)
        except OSError as e:
            self._log(f"Outbox: cannot queue {src}: {e}")
            return False
        if not self._room(size):
            self._log(f"Outbox: disk nearly full, not queuing {Path(src).name}")
            return False
        name = self._name()
        data = self.items / (name + ".data")
        try:
            shutil.copyfile(src, data)
        except OSError as e:
            self._log(f"Outbox: cannot queue {src}: {e}")
            try:
                data.unlink()
            except OSError:
                pass
            return False
        self._commit(name, {"kind": "file", "key": key, "content_type": content_type,
                            "url": url, "created": time.time()})
        return True

    def enqueue_bytes(self, blob: bytes, key: str, content_type: str, url: str) -> bool:
        if not self._room(len(blob)):
            self._log(f"Outbox: disk nearly full, not queuing {key.rsplit('/', 1)[-1]}")
            return False
        name = self._name()
        try:
            with open(self.items / (name + ".data"), "wb") as f:
                f.write(blob)
        except OSError as e:
            self._log(f"Outbox: cannot queue {key}: {e}")
            return False
        self._commit(name, {"kind": "file", "key": key, "content_type": content_type,
                            "url": url, "created": time.time()})
        return True

    def enqueue_message(self, topic: str, payload: str, qos: int = 1) -> bool:
        if not self._room(len(payload)):
            self._log(f"Outbox: disk nearly full, not queuing message for {topic}")
            return False
        try:
            self._commit(self._name(), {"kind": "mqtt", "topic": topic, "payload": payload,
                                        "qos": int(qos), "created": time.time()})
            return True
        except OSError as e:
            self._log(f"Outbox: cannot queue message for {topic}: {e}")
            return False

    def pending(self) -> List[str]:
        return sorted(p.name[:-5] for p in self.items.glob("*.json"))

    # ------------------------------------------------------------------ wiring

    def set_uploader(self, uploader: Uploader) -> None:
        self._uploader = uploader
        self._wake.set()

    def attach_mqtt(self, publisher: Publisher, connected: Callable[[], bool]) -> None:
        """Give this process's worker the daemon's MQTT link. Until then reply
        messages wait on disk (a script that only takes photos still drains
        its photos)."""
        self._publisher = publisher
        self._connected = connected
        self._wake.set()

    def link_changed(self) -> None:
        """Call on reconnect: retry now instead of at the end of a backoff."""
        self._next_try = 0.0
        self._backoff = BACKOFF_START_S
        self._wake.set()

    # ------------------------------------------------------------------ worker

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="outbox", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout)
        self._release()

    def _acquire(self) -> bool:
        """One drainer per outbox directory across processes (daemon + a script)."""
        if self._lock_fd is not None:
            return True
        if fcntl is None:
            return True
        fd = os.open(str(self.dir / ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def _release(self) -> None:
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)    # closing drops the flock
            except OSError:
                pass
            self._lock_fd = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.drain_once()
            except Exception as e:      # the worker must outlive any one bad item
                self.last_error = str(e)
                self._log(f"Outbox: worker error: {e}")
                busy = False
            if not busy:
                self._wake.wait(1.0)
                self._wake.clear()

    def drain_once(self) -> bool:
        """Try to send the oldest item. True if something was sent (call again
        straight away), False if there is nothing to do right now."""
        if time.monotonic() < self._next_try:
            return False
        names = self.pending()
        if not names:
            return False
        if not self._acquire():
            return False                # another process is draining this outbox
        name = names[0]
        meta_path = self.items / (name + ".json")
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (OSError, ValueError) as e:
            self._set_aside(name, f"unreadable metadata: {e}")
            return True

        if meta.get("kind") == "mqtt":
            outcome = self._send_message(name, meta)
        else:
            outcome = self._send_file(name, meta)

        if outcome == "sent":
            self._done(name)
            self._backoff = BACKOFF_START_S
            self._next_try = 0.0
            self.sent += 1
            return True
        if outcome == "waiting":
            return False
        # transient
        self._next_try = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2.0, BACKOFF_MAX_S)
        return False

    def _send_file(self, name: str, meta: Dict[str, Any]) -> str:
        data = self.items / (name + ".data")
        if not data.exists():
            self._set_aside(name, "data file missing")
            return "sent"   # nothing to send; move on
        if self._uploader is None:
            return "waiting"
        try:
            self._uploader(str(data), meta["key"], meta.get("content_type") or "application/octet-stream")
        except PermanentError as e:
            n = self._attempts.get(name, 0) + 1
            self._attempts[name] = n
            self.last_error = str(e)
            if n >= MAX_PERMANENT_ATTEMPTS:
                self._set_aside(name, f"refused {n} times: {e}")
                return "sent"
            self._log(f"Outbox: {meta['key'].rsplit('/', 1)[-1]} refused ({e}), attempt {n}/{MAX_PERMANENT_ATTEMPTS}")
            return "transient"
        except Exception as e:
            if self.last_error != str(e):
                self._log(f"Outbox: upload waiting for the link ({e}); {len(self.pending())} queued")
            self.last_error = str(e)
            return "transient"
        self.last_error = None
        return "sent"

    def _send_message(self, name: str, meta: Dict[str, Any]) -> str:
        if self._publisher is None or not self._connected():
            return "waiting" if self._publisher is None else "transient"
        now = time.time()
        inflight = self._inflight.get(name)
        if inflight is not None:
            fut, at = inflight
            if _done(fut):
                exc = _exception(fut)
                if exc is None:
                    del self._inflight[name]
                    return "sent"
                del self._inflight[name]
                self._log(f"Outbox: reply publish failed ({exc}); publishing again")
            elif now - at < REPUBLISH_AFTER_S:
                return "waiting"
            else:
                del self._inflight[name]
                self._log("Outbox: reply never acknowledged; publishing again")
        try:
            fut = self._publisher(meta["topic"], meta["payload"], int(meta.get("qos", 1)))
        except Exception as e:
            self.last_error = str(e)
            return "transient"
        if fut is None or int(meta.get("qos", 1)) == 0:
            return "sent"
        self._inflight[name] = (fut, now)
        # An acknowledgement usually comes back within milliseconds; give it a
        # moment so the common case finishes in this call.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not _done(fut):
            time.sleep(0.02)
        if _done(fut) and _exception(fut) is None:
            del self._inflight[name]
            return "sent"
        return "waiting"

    def _done(self, name: str) -> None:
        for suffix in (".json", ".data"):
            try:
                (self.items / (name + suffix)).unlink()
            except OSError:
                pass
        self._attempts.pop(name, None)

    def _set_aside(self, name: str, why: str) -> None:
        self._log(f"Outbox: giving up on {name}: {why} (kept in {self.failed})")
        for suffix in (".json", ".data"):
            src = self.items / (name + suffix)
            if src.exists():
                try:
                    os.replace(src, self.failed / src.name)
                except OSError:
                    pass
        try:
            with open(self.failed / (name + ".why"), "w") as f:
                f.write(why + "\n")
        except OSError:
            pass
        self._attempts.pop(name, None)

    def wait_empty(self, timeout: float) -> bool:
        """Block until everything queued has been sent (for scripts and tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.pending():
                return True
            self._wake.set()
            time.sleep(0.05)
        return not self.pending()


def _done(fut: Any) -> bool:
    try:
        return bool(fut.done())
    except Exception:
        return True


def _exception(fut: Any) -> Optional[BaseException]:
    try:
        return fut.exception(timeout=0)
    except TypeError:
        return fut.exception()
    except Exception as e:
        return e

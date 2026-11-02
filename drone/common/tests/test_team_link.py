"""Team link: authentication, replay rejection, link statistics, transports.

The link is the only thing standing between a teammate and a forged
TASK_CLAIM once there is no cloud, so the failures that matter are the silent
ones: a tampered or replayed frame that is *accepted*, a wrong-key teammate
that just looks absent, or a loss estimate that lies to the allocator.

Most tests feed crafted frames straight into ``TeamLink.receive_datagram`` so
they are deterministic; the transport tests at the bottom use real sockets
and skip cleanly where the sandbox forbids them.

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_team_link.py -q
"""

import logging
import math
import os
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import team_link as tl  # noqa: E402
from team_link import (  # noqa: E402
    FrameError, MemoryHub, TeamKeyring, TeamLink, TeamLinkConfigError,
    UdpBroadcastTransport, Ipv6MulticastTransport, decode_frame, encode_frame,
    canonical_body,
)
from team_link_rssi import (  # noqa: E402
    IwStationRssi, mac_from_link_local, parse_neigh, parse_station_dump,
)

K1 = bytes(range(32))
K2 = bytes(range(100, 132))
KR = TeamKeyring({1: K1, 2: K2})
POS = {"type": "POSITION", "pos_enu": [1.0, 2.0, 3.0], "yaw_deg": 90.0,
       "vel": [0.0, 0.0, 0.0], "confidence": 1.0, "battery_pct": 80.0, "rtl": False}


def frame(seq, sender="peer-a", epoch=1000, ts=None, body=None, key=K1, key_id=1):
    return encode_frame(body or POS, sender, epoch, seq, time.time() if ts is None else ts,
                        key=key, key_id=key_id)


def link(**kw):
    kw.setdefault("keyring", KR)
    kw.setdefault("transport", MemoryHub().transport("self"))
    return TeamLink("self", **kw)


# --------------------------------------------------------------------------- keys / config
def test_keyring_parses_comments_and_signs_with_the_first_key():
    kr = TeamKeyring.parse(f"# team key\n\n7 {K2.hex()}  # new\n3 {K1.hex()}\n")
    assert kr.key_ids == [7, 3]
    assert kr.tx_key_id == 7 and kr.tx_key == K2
    assert kr.get(3) == K1 and kr.get(9) is None
    assert TeamKeyring.parse(f"7 {K2.hex()}\n3 {K1.hex()}", tx_key_id=3).tx_key == K1


@pytest.mark.parametrize("text", [
    "", "# only a comment\n", f"1 {K1.hex()[:20]}", "1 nothex" + "z" * 40,
    f"one {K1.hex()}", f"1 {K1.hex()}\n1 {K2.hex()}", f"300 {K1.hex()}", f"1 {K1.hex()} extra",
])
def test_bad_key_files_are_refused_without_echoing_the_secret(text):
    with pytest.raises(TeamLinkConfigError) as ei:
        TeamKeyring.parse(text)
    assert K1.hex() not in str(ei.value) and K2.hex() not in str(ei.value)


def test_keyring_repr_and_logs_never_contain_key_material(tmp_path, caplog):
    p = tmp_path / "team.key"
    p.write_text(f"1 {K1.hex()}\n")
    os.chmod(p, 0o644)
    with caplog.at_level(logging.DEBUG):
        kr = TeamKeyring.from_file(str(p))
        lk = TeamLink("self", key_file=str(p), transport=MemoryHub().transport("x"))
        lk.start()
        lk.stop()
    assert "chmod 600" in caplog.text               # group/world-readable key file warned
    for text in (repr(kr), caplog.text):
        assert K1.hex() not in text and K1.hex().upper() not in text


def test_signed_is_the_default_and_unsigned_needs_the_explicit_flag():
    with pytest.raises(TeamLinkConfigError):
        TeamLink("self", transport=MemoryHub().transport("x"))
    assert not TeamLink("self", insecure_allow_unsigned=True,
                        transport=MemoryHub().transport("x")).signed
    assert link().signed


def test_missing_key_file_is_a_config_error(tmp_path):
    with pytest.raises(TeamLinkConfigError):
        TeamLink("self", key_file=str(tmp_path / "nope.key"))


def test_from_config(tmp_path):
    (tmp_path / "team.key").write_text(f"2 {K2.hex()}\n1 {K1.hex()}\n")
    lk = TeamLink.from_config("self", {"key_file": "team.key", "tx_key_id": 1, "port": 6000,
                                       "max_clock_skew_s": None}, base_dir=str(tmp_path))
    assert lk.signed and lk._keyring.tx_key_id == 1 and lk.max_clock_skew_s is None
    assert isinstance(lk.transport, UdpBroadcastTransport) and lk.transport.port == 6000
    lk6 = TeamLink.from_config("self", {"key_file": str(tmp_path / "team.key"),
                                        "transport": "ipv6_multicast", "interface": "mesh0"})
    assert isinstance(lk6.transport, Ipv6MulticastTransport) and lk6.transport.interface == "mesh0"
    with pytest.raises(TeamLinkConfigError):
        TeamLink.from_config("self", {"key_file": str(tmp_path / "team.key"),
                                      "transport": "ipv6_multicast"})
    with pytest.raises(TeamLinkConfigError):
        TeamLink.from_config("self", {"key_file": str(tmp_path / "team.key"), "transport": "lora"})
    with pytest.raises(TeamLinkConfigError):
        TeamLink.from_config("self", {})
    assert not TeamLink.from_config("self", {"insecure_allow_unsigned": True}).signed


# --------------------------------------------------------------------------- codec
def test_signed_roundtrip():
    data = frame(5, ts=1234.5)
    f = decode_frame(data, KR)
    assert (f.sender, f.epoch, f.seq, f.ts, f.key_id, f.signed) == ("peer-a", 1000, 5, 1234.5, 1, True)
    assert f.body == POS


def test_canonical_encoding_is_independent_of_key_order():
    a = {"type": "X", "b": 1, "a": [1, 2]}
    b = {"a": [1, 2], "type": "X", "b": 1}
    assert canonical_body(a) == canonical_body(b) == b'{"a":[1,2],"b":1,"type":"X"}'
    assert frame(1, body=a, ts=1.0) == frame(1, body=b, ts=1.0)


def test_every_single_bit_flip_is_rejected():
    data = frame(1)
    for i in range(len(data)):
        for bit in (0x01, 0x80):
            bad = bytearray(data)
            bad[i] ^= bit
            with pytest.raises(FrameError):
                decode_frame(bytes(bad), KR)
            with pytest.raises(FrameError):          # insecure mode still verifies signed frames
                decode_frame(bytes(bad), KR, allow_unsigned=True)


def test_truncated_and_extended_frames_are_rejected():
    data = frame(1)
    for n in range(len(data)):
        with pytest.raises(FrameError):
            decode_frame(data[:n], KR)
    with pytest.raises(FrameError):
        decode_frame(data + b"\x00", KR)


def test_wrong_key_and_unknown_key_id():
    with pytest.raises(FrameError) as ei:
        decode_frame(frame(1, key=K2, key_id=1), KR)     # right id, wrong secret
    assert ei.value.reason == "bad_mac"
    with pytest.raises(FrameError) as ei:
        decode_frame(frame(1, key_id=9), KR)
    assert ei.value.reason == "unknown_key"
    with pytest.raises(FrameError) as ei:
        decode_frame(frame(1), None, allow_unsigned=True)  # keyless bench node can't verify
    assert ei.value.reason == "unknown_key"


def test_rotation_accepts_every_listed_key():
    assert decode_frame(frame(1, key=K2, key_id=2), KR).key_id == 2
    with pytest.raises(FrameError):
        decode_frame(frame(1, key=K2, key_id=2), TeamKeyring({1: K1}))


def test_unsigned_and_legacy_frames_need_the_opt_in():
    unsigned = frame(1, key=None)
    legacy = b'{"type":"TASK_CLAIM","claim_id":"1,2,3","from":"old","t":5.0}'
    for data in (unsigned, legacy):
        with pytest.raises(FrameError) as ei:
            decode_frame(data, KR)
        assert ei.value.reason == "unsigned"
    assert not decode_frame(unsigned, KR, allow_unsigned=True).signed
    f = decode_frame(legacy, None, allow_unsigned=True)
    assert f.legacy and f.sender == "old" and f.body["claim_id"] == "1,2,3"


def test_body_is_never_parsed_unless_it_is_a_json_object_with_a_type():
    for body in (b"[1,2]", b'{"no_type":1}', b"\xff\xfe"):
        head = tl._HDR.pack(tl.MAGIC, tl.VERSION, 1, 1, 1, 1, time.time(), 1) + b"a" + body
        data = head + tl.hmac.new(K1, head, tl.hashlib.sha256).digest()
        with pytest.raises(FrameError) as ei:
            decode_frame(data, KR)
        assert ei.value.reason == "bad_body"


def test_position_frame_fits_the_payload_budget():
    lk = link(vclass_name="fixedwing")
    lk.update_self([12345.678901234, -9876.54321, 120.123456], 359.987654,
                   [12.3456789, -0.000123, -3.5], 0.987654321, 87.654321, False)
    data = lk._build_frame(lk._position_payload())
    big_id = encode_frame(lk._position_payload(), "x" * tl.MAX_ID_BYTES, 2**32 - 1, 2**32 - 1,
                          time.time(), key=K1, key_id=1)
    assert len(data) < 300 and len(big_id) < tl.MAX_FRAME_BYTES


def test_oversize_frames_are_neither_sent_nor_accepted():
    hub = MemoryHub()
    lk = link(transport=hub.transport("self"))
    lk._try_open()
    assert lk.send_sighting("t" * 600, [0, 0, 0]) is None
    assert hub.sent == [] and lk.counters()["tx_oversize"] == 1
    assert not lk.receive_datagram(b"\x00" * (tl.MAX_FRAME_BYTES + 1))
    assert lk.counters()["drop_oversize"] == 1


# --------------------------------------------------------------------------- replay / timestamps
def test_replay_is_dropped_and_counted():
    lk = link()
    data = frame(1)
    assert lk.receive_datagram(data, "10.0.0.2")
    assert not lk.receive_datagram(data, "10.0.0.2")
    c = lk.counters()
    assert c["rx_ok"] == 1 and c["drop_replay"] == 1
    assert len(lk.drain_inbox()) == 1


def test_sliding_window_tolerates_reordering_but_not_duplicates_or_ancient_frames():
    lk = link()
    for s in (10, 12, 11, 13):
        assert lk.receive_datagram(frame(s))
    assert not lk.receive_datagram(frame(11))
    assert lk.receive_datagram(frame(100))
    assert not lk.receive_datagram(frame(100 - tl.REPLAY_WINDOW))   # fell out of the window
    assert lk.receive_datagram(frame(100 - tl.REPLAY_WINDOW + 1))    # still inside, never seen
    assert lk.counters()["drop_replay"] == 2


def test_restart_new_epoch_is_accepted_old_epoch_is_stale():
    lk = link()
    assert lk.receive_datagram(frame(500, epoch=1000))
    assert lk.receive_datagram(frame(0, epoch=1001))          # sender restarted: seq reset is fine
    assert not lk.receive_datagram(frame(501, epoch=1000))    # recorded from the old session
    assert lk.counters()["drop_stale_epoch"] == 1
    assert lk.link_stats()["peer-a"].epoch == 1001


def test_lower_epoch_is_accepted_once_the_newer_session_is_older_than_the_window():
    lk = link(max_clock_skew_s=30.0)
    assert lk.receive_datagram(frame(0, epoch=2000))
    assert not lk.receive_datagram(frame(0, epoch=1990))
    lk._senders["peer-a"].last_heard -= 31.0                   # newer session silent > window
    assert lk.receive_datagram(frame(0, epoch=1990))
    lk_nowin = link(max_clock_skew_s=None)                     # no timestamp window: never
    assert lk_nowin.receive_datagram(frame(0, epoch=2000))
    lk_nowin._senders["peer-a"].last_heard -= 1e6
    assert not lk_nowin.receive_datagram(frame(0, epoch=1990))


def test_out_of_window_timestamp_is_dropped(caplog):
    lk = link(max_clock_skew_s=30.0)
    with caplog.at_level(logging.WARNING):
        assert not lk.receive_datagram(frame(1, ts=time.time() - 31))
        assert not lk.receive_datagram(frame(2, ts=time.time() + 31))
        assert not lk.receive_datagram(frame(3, ts=float("nan")))
    assert lk.receive_datagram(frame(4, ts=time.time() - 29))
    assert lk.counters()["drop_stale_ts"] == 3
    assert "check time sync" in caplog.text and caplog.text.count("check time sync") == 1  # rate-limited
    assert link(max_clock_skew_s=None).receive_datagram(frame(1, ts=0.0))


def test_drops_are_silent_counted_and_never_reach_peer_state():
    lk = link()
    bad = bytearray(frame(1, body={"type": "TASK_CLAIM", "claim_id": "evil"}))
    bad[-1] ^= 1
    for data in (bytes(bad), frame(2, key=None), frame(3, key_id=7), b"XX" + b"\x00" * 40, b"TL"):
        assert not lk.receive_datagram(data, "10.6.6.6")
    c = lk.counters()
    assert (c["drop_bad_mac"], c["drop_unsigned"], c["drop_unknown_key"], c["drop_bad_magic"],
            c["drop_malformed"]) == (1, 1, 1, 1, 1)
    assert lk.drain_inbox() == [] and lk.peers() == {} and lk.link_stats() == {}


def test_own_frames_are_ignored():
    lk = link()
    assert not lk.receive_datagram(frame(1, sender="self"))
    assert lk.counters()["rx_own"] == 1


# --------------------------------------------------------------------------- peer state / stats
def test_messages_keep_the_old_shape_and_peer_semantics():
    lk = link()
    ts = time.time()
    lk.receive_datagram(frame(1, ts=ts, body=dict(POS, vclass="rover")), "10.0.0.2")
    lk.receive_datagram(frame(2, body={"type": "TASK_CLAIM", "claim_id": "1,2,3"}), "10.0.0.2")
    lk.receive_datagram(frame(3, body=POS), "10.0.0.2")
    p = lk.peers()["peer-a"]
    assert p.pos_enu == [1.0, 2.0, 3.0] and p.task_claim == "1,2,3"  # claim survives a POSITION
    assert p.vclass_name is None                                       # last POSITION had none
    lk.receive_datagram(frame(4, body={"type": "TASK_DONE", "claim_id": "1,2,3"}))
    assert lk.peers()["peer-a"].task_claim is None
    msgs = lk.drain_inbox()
    assert [m["type"] for m in msgs] == ["POSITION", "TASK_CLAIM", "POSITION", "TASK_DONE"]
    m = msgs[0]
    assert m["from"] == "peer-a" and m["t"] == ts and m["_addr"] == "10.0.0.2"
    assert m["_auth"] is True and m["_seq"] == 1 and m["vclass"] == "rover"


def test_loss_estimate_from_sequence_gaps():
    lk = link()
    for s in range(10):
        if s not in (3, 7):
            lk.receive_datagram(frame(s), "10.0.0.2")
    st = lk.link_stats()["peer-a"]
    assert st.rx_packets == 8 and st.last_seq == 9
    assert st.loss_total == pytest.approx(0.2) and st.loss_recent == pytest.approx(0.2)
    assert st.authenticated and st.key_id == 1 and st.addr == "10.0.0.2"
    assert 0.0 <= st.age_s < 1.0 and st.rssi_dbm is None


def test_recent_loss_tracks_the_last_window_while_total_covers_the_session():
    lk = link()
    for s in range(100):                       # clean link
        lk.receive_datagram(frame(s))
    for s in range(100, 228, 2):               # then every other frame lost
        lk.receive_datagram(frame(s))
    st = lk.link_stats()["peer-a"]
    assert st.loss_recent == pytest.approx(0.5, abs=0.02)
    assert st.loss_total == pytest.approx(63 / 227, abs=0.01)


def test_loss_is_unknown_until_two_frames_and_resets_on_restart():
    lk = link()
    lk.receive_datagram(frame(40))
    st = lk.link_stats()["peer-a"]
    assert st.loss_recent is None and st.loss_total is None
    lk.receive_datagram(frame(44))
    assert lk.link_stats()["peer-a"].loss_total == pytest.approx(0.6)
    lk.receive_datagram(frame(0, epoch=1001))
    assert lk.link_stats()["peer-a"].loss_total is None


def test_rssi_hook_and_age_filter():
    calls = []

    def rssi(pid, addr):
        calls.append((pid, addr))
        return -61 if pid == "peer-a" else None

    lk = link(rssi_provider=rssi)
    lk.receive_datagram(frame(1), "fe80::1%mesh0")
    lk.receive_datagram(frame(1, sender="peer-b"), "fe80::2%mesh0")
    st = lk.link_stats()
    assert st["peer-a"].rssi_dbm == -61.0 and st["peer-b"].rssi_dbm is None
    assert ("peer-a", "fe80::1%mesh0") in calls
    lk._senders["peer-b"].last_heard -= 100
    assert set(lk.link_stats(max_age_s=10)) == {"peer-a"}

    def broken(pid, addr):
        raise RuntimeError("iw exploded")
    lk2 = link(rssi_provider=broken)
    lk2.receive_datagram(frame(1))
    assert lk2.link_stats()["peer-a"].rssi_dbm is None


def test_epoch_file_keeps_sessions_increasing_across_restarts(tmp_path, monkeypatch):
    path = str(tmp_path / "team_link.epoch")
    monkeypatch.setattr(tl.time, "time", lambda: 5000.0)
    lk = link(epoch_file=path)
    assert lk._next_epoch() == 5000
    assert lk._next_epoch() == 5001                  # same wall clock, still increasing
    monkeypatch.setattr(tl.time, "time", lambda: 100.0)
    assert lk._next_epoch() == 5002                  # clock stepped back after a reboot
    assert link()._next_epoch() == 100               # no file: wall clock


def test_seq_wrap_bumps_the_epoch():
    lk = link()
    lk._epoch, lk._seq = 7, 2**32 - 1
    f1 = decode_frame(lk._build_frame({"type": "HEARTBEAT"}), KR)
    f2 = decode_frame(lk._build_frame({"type": "HEARTBEAT"}), KR)
    assert (f1.epoch, f1.seq) == (7, 2**32 - 1) and (f2.epoch, f2.seq) == (8, 0)


def test_insecure_mode_interops_with_legacy_and_unsigned_peers():
    lk = TeamLink("self", insecure_allow_unsigned=True, transport=MemoryHub().transport("x"))
    assert lk.receive_datagram(b'{"type":"POSITION","pos_enu":[1,1,1],"from":"old","t":1.0}')
    assert lk.receive_datagram(frame(1, sender="new", key=None))
    assert not lk.receive_datagram(frame(1, sender="new", key=None))   # seq still enforced
    m = {x["from"]: x for x in lk.drain_inbox()}
    assert m["old"]["_auth"] is False and m["new"]["_auth"] is False
    assert set(lk.peers()) == {"old", "new"}


# --------------------------------------------------------------------------- live links
def _wait(pred, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _pair(ta, tb, **kw):
    a = TeamLink("drone-a", bcast_hz=20.0, keyring=KR, transport=ta, **kw)
    b = TeamLink("drone-b", bcast_hz=20.0, keyring=KR, transport=tb, **kw)
    a.update_self([1, 2, 3], 10.0)
    b.update_self([4, 5, 6], 20.0)
    return a, b


def _exchange(a, b):
    a.start()
    b.start()
    try:
        assert _wait(lambda: "drone-b" in a.peers() and "drone-a" in b.peers()), \
            (a.counters(), b.counters())
        b.send_task_claim("9,9,9")
        assert _wait(lambda: a.peers()["drone-b"].task_claim == "9,9,9")
        assert a.peers()["drone-b"].pos_enu == [4.0, 5.0, 6.0]
        assert b.peers()["drone-a"].yaw_deg == 10.0
        assert a.counters()["drop_bad_mac"] == 0 and a.counters()["rx_own"] >= 0
    finally:
        a.stop()
        b.stop()


def test_two_links_over_a_memory_hub_with_threads_and_loss():
    hub = MemoryHub()
    a, b = _pair(hub.transport("a"), hub.transport("b"))
    _exchange(a, b)
    # Now lose every other frame from b and check a's estimate converges.
    hub2 = MemoryHub()
    n = {"i": 0}

    def drop(data):
        f = decode_frame(data, KR)
        if f.sender != "drone-b":
            return False
        n["i"] += 1
        return n["i"] % 2 == 0
    hub2.drop = drop
    a, b = _pair(hub2.transport("a"), hub2.transport("b"))
    a.start()
    b.start()
    try:
        assert _wait(lambda: a.link_stats().get("drone-b") is not None
                     and a.link_stats()["drone-b"].rx_packets >= 20)
    finally:
        a.stop()
        b.stop()
    assert a.link_stats()["drone-b"].loss_total == pytest.approx(0.5, abs=0.1)


def test_a_forger_on_the_hub_is_ignored():
    hub = MemoryHub()
    a, b = _pair(hub.transport("a"), hub.transport("b"))
    evil = encode_frame({"type": "TASK_CLAIM", "claim_id": "mine"}, "drone-b", 2**31, 0,
                        time.time(), key=b"\x00" * 32, key_id=1)
    a.start()
    try:
        hub.deliver(evil, "10.6.6.6")
        assert _wait(lambda: a.counters()["drop_bad_mac"] == 1)
    finally:
        a.stop()
    assert "drone-b" not in a.peers()


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_two_links_over_real_udp_sockets_on_loopback():
    try:
        pa, pb = _free_udp_port(), _free_udp_port()
    except OSError as e:
        pytest.skip(f"UDP sockets unavailable: {e}")
    ta = UdpBroadcastTransport(pa, bcast_addr="127.0.0.1", bind_addr="127.0.0.1", tx_port=pb)
    tb = UdpBroadcastTransport(pb, bcast_addr="127.0.0.1", bind_addr="127.0.0.1", tx_port=pa)
    a, b = _pair(ta, tb)
    try:
        ta.open()
        ta.send(b"probe")
        ta.close()
    except OSError as e:
        pytest.skip(f"UDP loopback send not allowed: {e}")
    _exchange(a, b)


def _loopback_iface():
    for name in ("lo0", "lo"):
        try:
            socket.if_nametoindex(name)
            return name
        except OSError:
            continue
    return None


def test_two_links_over_ipv6_link_local_multicast_on_loopback():
    iface = _loopback_iface()
    if iface is None or not socket.has_ipv6:
        pytest.skip("no IPv6 loopback interface")
    port = _free_udp_port()
    probe = Ipv6MulticastTransport(iface, port)
    try:
        probe.open()
        probe.send(b"probe")
        got = probe.recv(1.0)
    except OSError as e:
        pytest.skip(f"IPv6 multicast unavailable on {iface}: {e}")
    finally:
        probe.close()
    if got is None:
        pytest.skip(f"IPv6 multicast does not loop back on {iface}")
    a, b = _pair(Ipv6MulticastTransport(iface, port), Ipv6MulticastTransport(iface, port))
    _exchange(a, b)
    assert "%" in a.link_stats()["drone-b"].addr or a.link_stats()["drone-b"].addr.startswith("fe80")


def test_link_waits_for_an_interface_that_is_not_up_yet():
    lk = TeamLink("self", keyring=KR, transport=Ipv6MulticastTransport("no-such-if0"))
    lk.start()
    try:
        assert not lk.transport_up
        lk.send_task_claim("1,1,1")                  # must not raise
        assert lk.counters()["tx_err"] == 1
    finally:
        lk.stop()


# --------------------------------------------------------------------------- RSSI helpers
STATION_DUMP = """\
Station 02:11:22:33:44:55 (on mesh0)
\tinactive time:\t120 ms
\tsignal:  \t-48 [-50, -51] dBm
\tsignal avg:\t-49 [-51, -52] dBm
\tmesh plink:\tESTAB
Station aa:bb:cc:dd:ee:ff (on mesh0)
\tsignal:  \t-77 dBm
"""


def test_station_dump_and_neighbour_parsing():
    assert parse_station_dump(STATION_DUMP) == {"02:11:22:33:44:55": -48.0, "aa:bb:cc:dd:ee:ff": -77.0}
    neigh = parse_neigh("fe80::1234 lladdr AA:BB:CC:DD:EE:FF router STALE\n"
                        "192.0.2.7 lladdr 02:11:22:33:44:55 REACHABLE\nfe80::9 FAILED\n")
    assert neigh == {"fe80::1234": "aa:bb:cc:dd:ee:ff", "192.0.2.7": "02:11:22:33:44:55"}
    assert mac_from_link_local("fe80::11:22ff:fe33:4455%mesh0") == "02:11:22:33:44:55"
    assert mac_from_link_local("fe80::1") is None and mac_from_link_local("garbage") is None


def test_iw_station_rssi_provider():
    def runner(cmd):
        if cmd[0] == "iw":
            return STATION_DUMP
        if cmd[1] == "-4":
            return "192.0.2.7 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
        return ""
    r = IwStationRssi("mesh0", runner=runner)
    assert r("peer", "fe80::11:22ff:fe33:4455%mesh0") == -48.0   # EUI-64 fallback
    assert r("peer", "192.0.2.7") == -77.0                        # neighbour table
    assert r("peer", "fe80::dead:beef") is None and r("peer", "") is None
    assert IwStationRssi("mesh0", runner=lambda cmd: "")("p", "192.0.2.7") is None


# --------------------------------------------------------------------------- L5 adapter
def test_l5_peer_provider():
    np = pytest.importorskip("numpy")
    from team_link_l5 import TeamLinkPeerProvider

    lk = link()
    lk.receive_datagram(frame(1, sender="r1", body=dict(POS, vclass="rover")))
    lk.receive_datagram(frame(1, sender="q1", body=dict(POS, vclass="banana")))
    lk.receive_datagram(frame(1, sender="bad", body=dict(POS, pos_enu=[1, 2])))
    peers = {p.id: p for p in TeamLinkPeerProvider(lk).peers()}
    assert set(peers) == {"r1", "q1"}
    assert peers["r1"].vclass_name == "rover" and peers["q1"].vclass_name == "quad"
    assert np.allclose(peers["r1"].pos_enu, [1, 2, 3]) and peers["r1"].pos_enu.dtype == np.float32
    with pytest.raises(KeyError):
        TeamLinkPeerProvider(lk, default_vclass="banana")


def test_generic_send_strips_receive_side_keys():
    hub = MemoryHub()
    lk = link(transport=hub.transport("self"))
    assert lk.send({"type": "BATTERY", "pct": 41.0, "from": "spoof", "t": 1.0, "_auth": True}) is False
    lk._try_open()
    assert lk.send({"type": "BATTERY", "pct": 41.0, "from": "spoof", "t": 1.0, "_auth": True})
    f = decode_frame(hub.sent[-1], KR)
    assert f.sender == "self" and f.body == {"type": "BATTERY", "pct": 41.0}
    with pytest.raises(ValueError):
        lk.send({"pct": 1})

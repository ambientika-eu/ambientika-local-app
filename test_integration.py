#!/usr/bin/env python3
"""Integration tests that drive the REAL _handle_conn framing loop
(not a replicated copy) via a fake StreamReader/Writer."""
import asyncio
import importlib

m = importlib.import_module("ambientika_local_bridge")
from test_bridge import FakeMqtt, FakeWriter, build_status


class FakeReader:
    """Yields the queued chunks then EOF (b'')."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


def make_bridge(**config_overrides):
    cfg = m.Config()
    for key, value in config_overrides.items():
        setattr(cfg, key, value)
    b = m.LocalBridge(cfg)
    b.mqtt = FakeMqtt()
    b.loop = asyncio.get_event_loop()
    return b


def test_handle_conn_does_not_send_setup_by_default():
    async def run():
        b = make_bridge()
        w = FakeWriter()
        r = FakeReader([build_status(mode=0, speed=1)])
        await b._handle_conn(r, w)
        return [f for f in w.frames if len(f) == 16 and f[8] == 0x00]
    assert asyncio.run(run()) == []


def test_handle_conn_sends_setup_when_explicitly_enabled():
    async def run():
        b = make_bridge(send_setup=True)
        w = FakeWriter()
        r = FakeReader([build_status(mode=0, speed=1)])
        await b._handle_conn(r, w)
        dev_serial = "AABBCCDDEEFF"
        # Opt-in setup frame: 16 bytes, starts 02 00, byte8=0x00.
        setups = [f for f in w.frames if len(f) == 16 and f[8] == 0x00]
        status_pub = [p for p in b.mqtt.pubs if "/AABBCCDDEEFF/status" in p[0][0]]
        return len(setups), len(status_pub), dev_serial in b.devices
    setups, pubs, still_registered = asyncio.run(run())
    assert setups == 1          # setup pushed once
    assert pubs >= 1            # status published
    # after EOF the device is removed (offline)
    assert still_registered is False


def test_handle_conn_junk_prefix_resyncs_and_decodes():
    """Fixed: a single junk byte ahead of a valid status frame is dropped and
    the parser resyncs, so the real frame behind it IS decoded (real code)."""
    async def run():
        b = make_bridge()
        w = FakeWriter()
        # junk byte 0x7F, then a valid 21-byte status, all in one chunk
        r = FakeReader([bytes([0x7F]) + build_status()])
        await b._handle_conn(r, w)
        status_pub = [p for p in b.mqtt.pubs if "/status" in p[0][0]]
        # device is popped on EOF, so check that it WAS decoded via the publish
        return len(status_pub)
    pubs = asyncio.run(run())
    assert pubs == 1            # the status behind the junk was recovered


def test_handle_conn_two_status_frames_back_to_back():
    async def run():
        b = make_bridge()
        w = FakeWriter()
        r = FakeReader([build_status(serial="AABBCCDDEE01"),
                        build_status(serial="AABBCCDDEE02")])
        await b._handle_conn(r, w)
        pubs = [p for p in b.mqtt.pubs if "/status" in p[0][0]]
        return len(pubs)
    # both frames decoded on the same connection
    assert asyncio.run(run()) == 2


def test_handle_conn_split_frame_reassembles():
    """A status frame split across two reads must reassemble correctly."""
    async def run():
        b = make_bridge()
        w = FakeWriter()
        frame = build_status(mode=3)
        r = FakeReader([frame[:10], frame[10:]])   # split mid-frame
        await b._handle_conn(r, w)
        status_pub = [p for p in b.mqtt.pubs if "/status" in p[0][0]]
        return len(status_pub)
    assert asyncio.run(run()) == 1


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))

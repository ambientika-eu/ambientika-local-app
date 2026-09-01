#!/usr/bin/env python3
"""
verify_capture.py — read-only frame capture for validating the wire layout.

Run this **instead of** the bridge when you want to confirm what an Ambientika
unit actually sends. It accepts the unit's connection on port 11000, decodes
every frame and prints it next to the raw bytes — and it never writes a single
byte back. No commands, no setup frames, no mode changes. The unit keeps running
exactly as it did before; it simply has a listener instead of a controller.

That property is the whole point: it makes it safe to validate a
reverse-engineered layout on a real installation. If a field is mapped wrong you
see a wrong number on screen and nothing has happened to the ventilation.

Usage
-----
    python3 verify_capture.py                      # listen on 0.0.0.0:11000
    python3 verify_capture.py --port 11000 --out capture.jsonl
    python3 verify_capture.py --expect-len 19      # force a layout for testing

Point the unit at this host the same way you would point it at the bridge
(local DNS override for app.ambientika.eu, or a DNAT rule). Stop the bridge
first — both cannot hold port 11000 at the same time.

What to do with it
------------------
Watch the decoded line and compare it against what the unit's own display and
the app show. Note anything that disagrees. Every frame also lands in the JSONL
file with its raw hex, so a disagreement can be traced back to the exact bytes.

Press Ctrl+C to stop; a summary is printed on exit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import socketserver
import sys
import threading

from ambientika_protocol import (
    FrameReader, decode_firmware, decode_status, implausible_fields,
)

STATE = {
    "frames": 0,
    "status": 0,
    "firmware": 0,
    "suspect": 0,
    "devices": {},
    "out": None,
    "expect_len": None,
    "lock": threading.Lock(),
}


def _stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _record(payload: dict) -> None:
    if STATE["out"]:
        STATE["out"].write(json.dumps(payload, sort_keys=True) + "\n")
        STATE["out"].flush()


def _print_status(serial: str, frame: bytes, decoded: dict, problems: list) -> None:
    flag = "  ⚠" if problems else "   "
    print(f"{flag} {_stamp()}  {serial}  {len(frame):>2}B  "
          f"{decoded['modeProtocol']:<22} {decoded['fanLevel']:<7} "
          f"{decoded['temperature']:>5}°C {decoded['humidity']:>3}%  "
          f"dp {decoded['dewPoint']}  role {decoded['deviceRole']}")
    if problems:
        for p in problems:
            print(f"      implausible: {p}")
        print(f"      raw: {frame.hex()}")


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        print(f"\n>> {_stamp()}  unit connected from {peer}")
        # Explicitly shut down our side of the write direction. Even a coding
        # mistake below cannot now put bytes on the wire.
        try:
            self.request.shutdown(socket.SHUT_WR)
        except OSError:
            pass

        reader = FrameReader(forced_len=STATE["expect_len"])
        try:
            while True:
                chunk = self.request.recv(256)
                if not chunk:
                    break
                for kind, frame in reader.feed(chunk):
                    with STATE["lock"]:
                        STATE["frames"] += 1
                        if kind == "firmware":
                            STATE["firmware"] += 1
                            fw = decode_firmware(frame)
                            reader.set_radio_fw(fw["radioFw"])
                            STATE["devices"].setdefault(fw["serial"], {}).update(fw)
                            print(f"   {_stamp()}  {fw['serial']}  firmware "
                                  f"radio={fw['radioFw']} micro={fw['microFw']} "
                                  f"at={fw['radioAtFw']}  -> expecting "
                                  f"{reader.known_status_len or 'unknown'}-byte status")
                            _record({"ts": dt.datetime.now().isoformat(),
                                     "kind": "firmware", "raw": frame.hex(), **fw})
                            continue

                        STATE["status"] += 1
                        try:
                            decoded = decode_status(frame)
                        except ValueError as exc:
                            print(f"  ⚠ {_stamp()}  undecodable status "
                                  f"({exc}): {frame.hex()}")
                            _record({"ts": dt.datetime.now().isoformat(),
                                     "kind": "undecodable", "raw": frame.hex(),
                                     "error": str(exc)})
                            continue
                        problems = implausible_fields(decoded)
                        if problems:
                            STATE["suspect"] += 1
                        serial = decoded["serial"]
                        STATE["devices"].setdefault(serial, {})["lastStatus"] = decoded
                        _print_status(serial, frame, decoded, problems)
                        _record({"ts": dt.datetime.now().isoformat(),
                                 "kind": "status", "raw": frame.hex(),
                                 "frameLength": len(frame),
                                 "implausible": problems, "decoded": decoded})
        except (ConnectionResetError, OSError) as exc:
            print(f"<< {_stamp()}  connection from {peer} ended: {exc}")
        finally:
            print(f"<< {_stamp()}  unit {peer} disconnected")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11000)
    ap.add_argument("--out", default="capture.jsonl",
                    help="JSONL file with raw hex plus decoded fields")
    ap.add_argument("--expect-len", type=int, choices=(19, 21), default=None,
                    help="force a status layout instead of resolving it")
    args = ap.parse_args()

    # Line-buffer stdout so the frames appear as they arrive even when the tool
    # runs under Docker or with its output piped to a file — otherwise the
    # operator watches a blank screen and assumes nothing is being received.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:                    # Python < 3.7
        pass

    STATE["expect_len"] = args.expect_len
    STATE["out"] = open(args.out, "a", encoding="utf-8") if args.out else None

    print("Ambientika read-only frame capture")
    print("This tool never writes to a unit. Nothing you do here can change the")
    print("ventilation — it only listens and decodes.\n")
    print(f"listening on {args.host}:{args.port}   log: {args.out or '(none)'}")
    if args.expect_len:
        print(f"forcing a {args.expect_len}-byte status layout")
    print("point a unit at this host, then compare the lines below against its")
    print("own display. Ctrl+C to stop.\n")

    try:
        with Server((args.host, args.port), Handler) as srv:
            srv.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"\ncannot listen on {args.host}:{args.port}: {exc}")
        print("is the bridge still running? Both cannot hold this port.")
        return 1
    finally:
        print("\n--- summary ---")
        print(f"frames {STATE['frames']}   status {STATE['status']}   "
              f"firmware {STATE['firmware']}   implausible {STATE['suspect']}")
        for serial, info in STATE["devices"].items():
            fw = info.get("radioFw", "?")
            last = info.get("lastStatus", {})
            print(f"  {serial}  radio fw {fw}  "
                  f"last: {last.get('modeProtocol', '-')} "
                  f"{last.get('temperature', '-')}°C {last.get('humidity', '-')}%")
        if STATE["suspect"]:
            print(f"\n{STATE['suspect']} frame(s) decoded to something physically "
                  "implausible — please send the log, those are the interesting ones.")
        if STATE["out"]:
            STATE["out"].close()
            print(f"\nlog written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

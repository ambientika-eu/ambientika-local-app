#!/usr/bin/env python3
"""
ambientika_policy.py
====================

Decisions the bridge makes *before* it writes anything to a ventilation unit.
Pulled out of ``ambientika_local_bridge.py`` because these are exactly the
rules that need review: they determine when the bridge reconfigures hardware
and when it stays quiet.

Three policies live here.

**Setup gating.** The bridge used to push a setup frame carrying
``role``/``zone``/``house`` to *every* unit on connect, using one global set of
defaults (``0 / 0 / 1``). On a single-unit desk setup that is harmless. On a
real installation with several masters and counter-running slaves across zones
it rewrites the master/slave topology and breaks cross ventilation. Setup is
now opt-in, per serial number, and refuses to overwrite a role the unit itself
reports unless that is stated explicitly.

**No-op suppression.** Every mode command makes the unit sound its confirmation
beep. The scheduler and the protection controller both re-issue targets that
are frequently identical to the unit's current state, so a bedroom unit can beep
through the night without anything actually changing. Commands that would not
change the unit's state are dropped.

**Serial allowlist.** The device link is plain TCP with no authentication, so
anything on the LAN can open a connection and claim any serial number. An
optional allowlist keeps the bridge from acting on units that are not ours.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

#: Returned by :func:`setup_decision` to say why a setup frame is or is not sent.
SETUP_SKIP_DISABLED = "disabled"
SETUP_SKIP_NOT_LISTED = "not-listed"
SETUP_SKIP_ROLE_CONFLICT = "role-conflict"
SETUP_SKIP_ALREADY_SENT = "already-sent"
SETUP_SEND = "send"


@dataclass(frozen=True)
class SetupTarget:
    role: int
    zone: int
    house: int


def parse_setup_devices(raw) -> dict:
    """Build {SERIAL: SetupTarget} from a JSON object.

    Accepts ``{"1C9DC2430444": {"role": 0, "zone": 2, "house": 1}}``. Entries
    that cannot be parsed are skipped rather than raising, so one bad entry
    cannot prevent the bridge from starting — but it also means a typo silently
    leaves that unit unconfigured, which is the safe direction here.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for serial, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        try:
            out[str(serial).upper()] = SetupTarget(
                role=int(spec.get("role", 0)),
                zone=int(spec.get("zone", 0)),
                house=int(spec.get("house", 1)),
            )
        except (TypeError, ValueError):
            continue
    return out


def setup_decision(serial: str,
                   enabled: bool,
                   targets: dict,
                   reported_role: Optional[int],
                   already_sent: bool,
                   allow_role_change: bool = False) -> tuple:
    """Decide whether to push a setup frame to one unit.

    Returns ``(action, target_or_None, reason)`` where action is
    :data:`SETUP_SEND` or one of the ``SETUP_SKIP_*`` constants.

    The role conflict check is the important one: if the unit reports that it
    is a slave and the configured target would make it a master, the bridge
    refuses unless ``allow_role_change`` says that reconfiguration is the
    explicit intent. Silently flipping a slave to master is how a working
    installation loses its cross ventilation.
    """
    serial = (serial or "").upper()
    if not enabled:
        return (SETUP_SKIP_DISABLED, None, "setup frames are disabled")
    if already_sent:
        return (SETUP_SKIP_ALREADY_SENT, None, "setup already sent on this connection")
    target = targets.get(serial)
    if target is None:
        return (SETUP_SKIP_NOT_LISTED, None,
                f"{serial} is not listed in SETUP_DEVICES")
    if (reported_role is not None
            and reported_role != target.role
            and not allow_role_change):
        return (SETUP_SKIP_ROLE_CONFLICT, None,
                f"{serial} reports role {reported_role}, configured target is "
                f"{target.role}; refusing to change it without SETUP_ALLOW_ROLE_CHANGE")
    return (SETUP_SEND, target, "ok")


def parse_serial_list(raw: str) -> set:
    """Comma- or whitespace-separated serial numbers -> uppercase set."""
    if not raw:
        return set()
    parts = str(raw).replace(",", " ").split()
    return {p.strip().upper() for p in parts if p.strip()}


def serial_allowed(serial: str, allowlist: set) -> bool:
    """An empty allowlist means 'accept everything' — the previous behaviour."""
    if not allowlist:
        return True
    return (serial or "").upper() in allowlist


def write_refusal(observe_only: bool, what: str = "command") -> Optional[str]:
    """Return a refusal reason when the bridge must not write to a unit.

    Observation mode is the safe way to validate a reverse-engineered layout on
    somebody else's installation: the bridge reads, decodes and publishes, but
    every path that would write to hardware is closed here — one gate, not a
    flag checked in five places. If the field mapping turns out to be wrong,
    the operator sees wrong numbers and reports them; the ventilation itself
    was never touched.
    """
    if observe_only:
        return (f"OBSERVE_ONLY is set — refusing to send {what}. "
                "The bridge is reading and decoding only.")
    return None


def command_is_noop(current: dict, mode: int, speed: int,
                    humidity: int, light: int) -> bool:
    """True when the command would leave the unit in the state it is already in.

    ``current`` is the unit's own last echo (``status_raw_codes``). Comparing
    against the echo rather than against the last command we sent means a change
    made at the unit or by the remote control is still corrected on the next
    write — only genuinely redundant traffic is dropped.
    """
    if not current:
        return False
    return (current.get("mode") == mode
            and current.get("speed") == speed
            and current.get("humidity") == humidity
            and current.get("light") == light)

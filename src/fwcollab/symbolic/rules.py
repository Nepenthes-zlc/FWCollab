"""Versioned public rules shared by observations, prompts and documentation."""

from __future__ import annotations

from typing import Any


RULEBOOK_VERSION = "fwcollab.symbol_rules.v3.2"


def public_rulebook() -> dict[str, Any]:
    """Return the compact, language-neutral contract exposed every round."""

    return {
        "version": RULEBOOK_VERSION,
        "objective": "team_success iff F is on f and W is on w at the same time",
        "coordinates": {
            "format": "[row, column]",
            "origin": "top_left",
            "deltas": {"UP": [-1, 0], "DOWN": [1, 0], "LEFT": [0, -1], "RIGHT": [0, 1]},
        },
        "legend": {
            "#": "wall",
            ".": "floor",
            "actors F,W": "current fire/water actor position",
            "exits f,w": "role-specific fire/water exit",
            "crate O": "pushable crate",
            "orb o": "rolling orb",
            "~": "water",
            "^": "lava",
            "x": "fatal to both",
            "1-9": "plate",
            "A-E/a-e": "closed/open door",
            "L/l": "off/on persistent lever",
            "T/t": "off/on toggle",
            "P-R": "paired portal",
            "J": "one-way gate",
            "I": "frozen thermal field",
            "H/K": "heater/freezer",
            "+": "light source",
            "S/s": "unlit/lit sensor",
            "/ and backslash and M": "fixed mirrors and rotatable mirror",
            "=/-": "inactive/active platform",
        },
        "actions": {
            "moves": ["UP", "DOWN", "LEFT", "RIGHT", "WAIT"],
            "movement_steps": 1,
            "wait_steps": 0,
            "resolution": "both agents choose from the same frozen pre-round state; actions resolve simultaneously",
        },
        "occupancy": {
            "actors": "actors may share a cell and pass through each other",
            "underlay": "map_rows may hide terrain under actors or objects; terrain_rows and layout remain authoritative",
        },
        "controllers": {
            "plate": "active only while an accepted actor or heavy object occupies it; leaving deactivates it",
            "lever": "entering latches it on permanently",
            "toggle": "each new entry flips it; remaining on it does not flip it again",
            "all": "every listed input must be on",
            "any": "at least one listed input must be on",
        },
        "actuators": {
            "door": "a closed door blocks entry; an open door permits entry in either direction",
            "platform": "an inactive platform blocks entry; an active platform is passable",
            "release_timing": (
                "entry is checked against the frozen pre-move actuator state; after movement, plates and actuators "
                "are recomputed for the next observation"
            ),
            "occupied_close": (
                "an actuator may become inactive while an actor already occupies its cell; that actor is not ejected "
                "or killed and may leave toward any otherwise passable neighbor"
            ),
        },
        "movables": {
            "crate": "pushes exactly one cell, cannot be pulled, counts as heavy load O, blocks light",
            "orb": "one push rolls it until the next obstacle, counts as heavy load O when stopped, blocks light",
        },
        "terrain": {
            "water": "safe for W and fatal to F",
            "lava": "safe for F and fatal to W",
            "hazard": "fatal to both actors",
            "wrong_exit": "an actor cannot enter the other actor's exit",
        },
        "special": {
            "portal": "entry teleports once to the paired endpoint; blocked movable occupancy at the exit prevents entry",
            "one_way": "the declared direction restricts entry only; leaving the cell is unrestricted",
            "thermal": "H persistently makes I liquid; K persistently freezes I; simultaneous H/K leaves it unchanged",
            "light": "walls and movable objects block light; mirrors reflect it; a lit sensor continuously enables its door",
            "mirror_reflection": {
                "/": {"UP": "RIGHT", "RIGHT": "UP", "DOWN": "LEFT", "LEFT": "DOWN"},
                "\\": {"UP": "LEFT", "LEFT": "UP", "DOWN": "RIGHT", "RIGHT": "DOWN"},
            },
        },
        "communication": {
            "delay_rounds": 1,
            "message_costs_action": False,
            "silence": "use message=null when no new coordination fact, request, commitment, or blockage exists",
            "semantics": "a message reports intent or a past observation, never the sender's still-hidden current action",
            "staleness": "validate every delivered message against the current public state before following it",
            "held_gate_handshake": ["READY", "HOLDING", "CROSSED", "RELEASE"],
        },
    }

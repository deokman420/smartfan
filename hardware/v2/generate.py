#!/usr/bin/env python3
"""KiCad schematic and PCB for Smartfan prototype V2.

The circuit is the one locked in hardware/v2/prototype-v2.md and drawn in
hardware/v2/build-sheet-v2.html:

  120 V AC -> IEC C8 -> 2 A time-lag fuse in one lead -> J5 -> IRM-10-12
  +12 V -> IRF9540 high side -> NF-F12 yellow
  +12 V -> R-78E5.0-1.0 -> 1N5819 -> Pico VSYS
  fan black stays on 0 V, blue PWM tied to +5 V, green tach to GP18
  SHT40 on I2C (GP0/GP1). No DHT22, no SGP41 on this board.

The C8 and the fuse holder are panel parts. They are on the schematic and
not on the board. Their wires land in J5, a screw terminal beside the
IRM-10's AC pins.

Run with KiCad's Python so pcbnew can build the board:

  & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" hardware/v2/generate.py

The run also checks its own output with kicad-cli: the schematic netlist
against PARTS, ERC, and DRC with schematic parity. Then it rewrites the
schematic PDF, the two SVGs, and the Gerbers and drill files in fab/.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import re
import subprocess
import tempfile
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KICAD_HOME = Path(os.environ["LOCALAPPDATA"]) / "Programs/KiCad/10.0"
KICAD = KICAD_HOME / "share/kicad"
SYM = KICAD / "symbols"
FP = KICAD / "footprints"
CLI = KICAD_HOME / "bin/kicad-cli.exe"

PROJECT = "smartfan-v2"
SHEET_UUID = "b7e1c4a0-6f2d-4a18-9c33-0a51d8e2f4b6"
DATE = "2026-11-08"

# 96 x 70 mm sits on the floor of a Hammond 1590BB, between the corner
# bosses. The Hammond drawing gives that flat as 103.50 x 78.00 mm and
# the inside height as 29.75 mm. The box in hand has not been calipered.
BOARD_W = 96.0
BOARD_H = 70.0

# M3 clearance, unplated. These are not the 1590BB lid bosses. The board
# sits between the bosses; the holes are for separate screws. The keepout
# is wider than the hole so a screw head cannot touch copper.
# (22, 33.5) replaced (12, 34) when J5 took the mains corner. (80, 36.5)
# and (91, 63) replaced (80, 32) and (76, 50) when the switch stage and J4
# were rearranged.
HOLES = (
    (22.0, 33.5),
    (58.0, 10.0),
    (80.0, 36.5),
    (91.0, 63.0),
)
HOLE_D = 3.2
HOLE_KEEP = 3.2
# A screw head or a hex standoff reaches about this far from the hole center.
SCREW_R = 3.2

# 120 V copper keeps this far from everything that is not 120 V: the
# low-voltage copper, the ground pour, unconnected pads, and any screw.
# It is more than reinforced insulation needs at 120 V, and enough for 230 V.
MAINS_GAP = 6.4
AC_NETS = ("AC_N", "AC_HOT")

# The Pico 2 W antenna is the end of the module away from USB. No copper
# on either layer under it or for 7 mm past the end of the module. The
# top edge leaves a strip of pour under pins 21-24 so the ground pin among
# them, pin 23, still reaches the pour.
ANT_KEEP = (53.0, 46.0, 70.0, 60.9)

VIA_D = 0.8
VIA_DRILL = 0.4

# KiCad's default drawing sheet is a red frame 10 mm in from the A4
# corner, and the title block occupies the lower right. A board whose
# outline starts at (0, 0) has that frame running through the copper.
# This parks the outline inside the frame.
PAGE_OX = 100.0
PAGE_OY = 50.0

# The names KiCad gives the power nets. Every other net carries a label,
# so KiCad names it "/" + the label.
POWER_NAMES = {"GND": "GND", "+12V": "+12V", "+5V": "+5V", "+3V3": "+3.3V"}


def uid() -> str:
    return str(uuid.uuid4())


def fmt(n: float) -> str:
    s = f"{n:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def snap(mm: float) -> float:
    """Nearest KiCad connection-grid point, 1.27 mm."""
    return round(mm / 1.27) * 127 / 100


def g(n: float) -> float:
    """n steps of the 1.27 mm schematic grid."""
    return n * 127 / 100


def P(x: float, y: float) -> tuple[float, float]:
    """A schematic point given in grid steps."""
    return (g(x), g(y))


def net_name(name: str) -> str:
    """The name KiCad gives a schematic net, and so the board net name."""
    if name.startswith("unconnected-"):
        return name
    return POWER_NAMES.get(name) or f"/{name}"


def plain_name(board_name: str) -> str:
    """Board net name back to the name used in PARTS."""
    for plain, kicad in POWER_NAMES.items():
        if board_name == kicad:
            return plain
    return board_name[1:] if board_name.startswith("/") else board_name


def extract(text: str, name: str) -> str:
    needle = f'(symbol "{name}"'
    start = text.find(needle)
    if start < 0:
        raise SystemExit(f"symbol {name} not found")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise SystemExit(f"unbalanced symbol {name}")


def cache_symbol(body: str, lib: str, name: str, orig: str, value: str,
                 footprint: str) -> str:
    body = body.replace(f'(symbol "{orig}"', f'(symbol "{lib}:{name}"', 1)
    body = body.replace(f'(symbol "{orig}_', f'(symbol "{name}_')
    body = re.sub(
        r'\(property "Value" "[^"]*"',
        f'(property "Value" "{value}"',
        body,
        count=1,
    )
    body = re.sub(
        r'\(property "Footprint" "[^"]*"',
        f'(property "Footprint" "{footprint}"',
        body,
        count=1,
    )
    return body


def pin_chunks(body: str):
    """Yield (start, end, text) for every (pin ...) in a symbol body."""
    i = 0
    while True:
        j = body.find("(pin ", i)
        if j < 0:
            return
        depth = 0
        k = j
        while k < len(body):
            if body[k] == "(":
                depth += 1
            elif body[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        yield j, k + 1, body[j : k + 1]
        i = k + 1


def demote_pico(body: str) -> str:
    """The Pico symbol marks GND, AGND and VBUS as power outputs and
    ADC_VREF as a power input. On this board the IRM-10 is the ground
    source, VBUS has no wire, and ADC_VREF stays open because the module
    already filters it. Leaving those types makes ERC call a correct
    ground a short and an open pin an undriven supply.
    """
    out = []
    i = 0
    for j, k, chunk in pin_chunks(body):
        out.append(body[i:j])
        num = re.search(r'\(number "([^"]+)"', chunk)
        n = num.group(1) if num else ""
        # GND and AGND are power outputs on the symbol. VBUS is too, and it
        # is left open. ADC_VREF is a power input with nothing on it.
        if n in ("3", "8", "13", "18", "23", "28", "33", "38", "40", "35"):
            chunk = re.sub(r"\(pin \w+", "(pin passive", chunk, count=1)
        out.append(chunk)
        i = k
    out.append(body[i:])
    return "".join(out)


def pin_table(body: str) -> dict[str, tuple[float, float]]:
    pins = {}
    for _j, _k, chunk in pin_chunks(body):
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+) [-\d.]+\)", chunk)
        num = re.search(r'\(number "([^"]+)"', chunk)
        if at and num:
            pins[num.group(1)] = (float(at.group(1)), float(at.group(2)))
    return pins


def pin_names(body: str) -> dict[str, str]:
    names = {}
    for _j, _k, chunk in pin_chunks(body):
        num = re.search(r'\(number "([^"]+)"', chunk)
        name = re.search(r'\(name "([^"]*)"', chunk)
        if num and name:
            names[num.group(1)] = name.group(1)
    return names


def body_box(body: str) -> tuple[float, float, float, float]:
    """Bounding box of a symbol's graphics, pins left out. Symbol units, Y up."""
    graphics = []
    i = 0
    for j, k, _chunk in pin_chunks(body):
        graphics.append(body[i:j])
        i = k
    graphics.append(body[i:])
    text = "".join(graphics)
    xs, ys = [], []
    for m in re.finditer(r"\((?:start|end|mid|xy) ([-\d.]+) ([-\d.]+)\)", text):
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    for m in re.finditer(r"\(center ([-\d.]+) ([-\d.]+)\)\s*\(radius ([-\d.]+)\)", text):
        cx, cy, r = (float(v) for v in m.groups())
        xs += [cx - r, cx + r]
        ys += [cy - r, cy + r]
    return (min(xs), min(ys), max(xs), max(ys))


def field_positions(body: str) -> dict[str, tuple[float, float]]:
    out = {}
    for name in ("Reference", "Value"):
        m = re.search(rf'\(property "{name}" "[^"]*"\s*\(at ([-\d.]+) ([-\d.]+)', body)
        if m:
            out[name] = (float(m.group(1)), float(m.group(2)))
    return out


def transform(ox, oy, rot, px, py, mirror=None):
    """Symbol point (Y up) onto the schematic (Y down). rot is CCW degrees.

    mirror "y" flips left and right, "x" flips top and bottom. It is only
    used with rot 0, where the order of mirror and rotation does not matter.
    """
    if mirror == "y":
        px = -px
    elif mirror == "x":
        py = -py
    r = math.radians(rot)
    rx = px * math.cos(r) - py * math.sin(r)
    ry = px * math.sin(r) + py * math.cos(r)
    return ox + rx, oy - ry


# lib, orig symbol, cached name, default footprint
LIBS = {
    "irm": ("Converter_ACDC.kicad_sym", "IRM-10-3.3", "Converter_ACDC", "IRM-10-12",
            "Converter_ACDC:Converter_ACDC_MeanWell_IRM-10-xx_THT"),
    "buck": ("Converter_DCDC.kicad_sym", "R-78E5.0-0.5", "Converter_DCDC", "R-78E5.0-1.0",
             "Converter_DCDC:Converter_DCDC_RECOM_R-78E-0.5_THT"),
    "d5819": ("Diode.kicad_sym", "SB120", "Diode", "1N5819",
              "Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal"),
    "d4148": ("Diode.kicad_sym", "1N4001", "Diode", "1N4148",
              "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal"),
    "fet": ("Transistor_FET.kicad_sym", "IRF9540N", "Transistor_FET", "IRF9540N",
            "Package_TO_SOT_THT:TO-220-3_Horizontal_TabDown"),
    "npn": ("Transistor_BJT.kicad_sym", "Q_NPN_EBC", "Transistor_BJT", "S8050",
            "Package_TO_SOT_THT:TO-92_Inline"),
    "R": ("Device.kicad_sym", "R", "Device", "R",
          "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"),
    "LED": ("Device.kicad_sym", "LED", "Device", "LED",
            "LED_THT:LED_D3.0mm"),
    "LDR": ("Device.kicad_sym", "R_Photo", "Device", "R_Photo",
            "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"),
    "SW": ("Switch.kicad_sym", "SW_Push", "Switch", "SW_Push",
           "Button_Switch_THT:SW_PUSH_6mm"),
    "FUSE": ("Device.kicad_sym", "Fuse", "Device", "Fuse", ""),
    "C2": ("Connector_Generic.kicad_sym", "Conn_01x02", "Connector_Generic", "Conn_01x02", ""),
    # A keyed 4-pin fan header. A bare pin header lets the fan plug go on
    # backwards, which lands switched 12 V on the fan's tach output.
    "C4": ("Connector_Generic.kicad_sym", "Conn_01x04", "Connector_Generic", "Conn_01x04",
           "Connector:FanPinHeader_1x04_P2.54mm_Vertical"),
    "C6": ("Connector_Generic.kicad_sym", "Conn_01x06", "Connector_Generic", "Conn_01x06",
           "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical"),
    "TERM": ("Connector.kicad_sym", "Screw_Terminal_01x02", "Connector", "Screw_Terminal_01x02",
             "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"),
    "PICO": ("MCU_Module.kicad_sym", "RaspberryPi_Pico", "MCU_Module", "RaspberryPi_Pico",
             "Module:RaspberryPi_Pico_Common_THT"),
    "GND": ("power.kicad_sym", "GND", "power", "GND", ""),
    "+12V": ("power.kicad_sym", "+12V", "power", "+12V", ""),
    "+5V": ("power.kicad_sym", "+5V", "power", "+5V", ""),
    "+3V3": ("power.kicad_sym", "+3.3V", "power", "+3.3V", ""),
    "FLG": ("power.kicad_sym", "PWR_FLAG", "power", "PWR_FLAG", ""),
}

# name, key, value, at, rot, footprint or None, on_board, pin->net
# Positions are in schematic grid steps (1.27 mm) so a wire lands on the pin.
# Power nets use a power symbol. Signal nets meet the Pico by label.
PARTS = [
    # Mains. J1 and F1 are on the box wall; J5 is where their wires land.
    # J5 pin 1 is the unfused lead and pin 2 the fused one. The C8 is not
    # polarized, so "hot" is whichever lead the fuse is in.
    ("J1", "C2", "IEC C8", P(30, 26), 0, None, False, {"1": "AC_L", "2": "AC_N"}),
    ("F1", "FUSE", "2A time-lag", P(44, 26), 90, None, False, {"1": "AC_L", "2": "AC_HOT"}),
    ("J5", "TERM", "AC in", P(60, 28), 0, None, True, {"1": "AC_N", "2": "AC_HOT"}),
    ("PS1", "irm", "IRM-10-12", P(82, 28), 0, None, True,
     {"1": "AC_N", "2": "AC_HOT", "3": "GND", "4": "+12V"}),
    ("U1", "buck", "R-78E5.0-1.0", P(106, 26), 0, None, True,
     {"1": "+12V", "2": "GND", "3": "+5V"}),
    ("D1", "d5819", "1N5819", P(124, 26), 180, None, True, {"2": "+5V", "1": "VSYS"}),
    # The 12 V switch and the fan.
    ("Q1", "fet", "IRF9540NPBF", P(64, 70), 180, None, True,
     {"3": "+12V", "2": "YELLOW", "1": "GATE"}),
    ("R4", "R", "100k", P(76, 64), 0, None, True, {"1": "+12V", "2": "GATE"}),
    ("R3", "R", "100", P(87, 70), 90, None, True, {"1": "GATE", "2": "COLL"}),
    ("Q2", "npn", "S8050", P(98, 78), 0, None, True,
     {"3": "COLL", "2": "BASE", "1": "GND"}),
    ("R1", "R", "1k", P(87, 78), 90, None, True, {"1": "FAN_ON", "2": "BASE"}),
    ("R2", "R", "100k", P(92, 81), 0, None, True, {"1": "BASE", "2": "GND"}),
    ("D2", "d4148", "1N4148", P(62, 82), 270, None, True, {"1": "YELLOW", "2": "GND"}),
    ("J2", "C4", "NF-F12 fan", P(136, 78), 0, None, True,
     {"1": "GND", "2": "YELLOW", "3": "TACH", "4": "+5V"}),
    ("R5", "R", "10k", P(150, 74), 0, None, True, {"1": "+3V3", "2": "TACH"}),
    # At the grille, off the board. The SHT40 breakout is not drawn.
    ("LDR1", "LDR", "LDR", P(28, 118), 0, None, False, {"1": "+3V3", "2": "LIGHT"}),
    ("SW1", "SW", "button", P(48, 118), 270, None, False, {"1": "J4_BTN", "2": "GND"}),
    ("D3", "LED", "green", P(68, 118), 90, None, False, {"2": "J4_GREEN", "1": "GND"}),
    ("D4", "LED", "red", P(88, 118), 90, None, False, {"2": "J4_RED", "1": "GND"}),
    # Board side of the two grille cables. R9, R7 and R8 sit between J4 and
    # the Pico, so a pinched cable or a static spark at the grille meets a
    # resistor before it meets a GPIO.
    ("J3", "C6", "grille sensors", P(130, 118), 0, None, True,
     {"1": "GND", "2": "+3V3", "3": "SDA", "4": "SCL", "5": "LIGHT", "6": "+3V3"}),
    ("R6", "R", "10k", P(142, 121), 0, None, True, {"1": "LIGHT", "2": "GND"}),
    ("J4", "C6", "button + LEDs", P(172, 118), 0, None, True,
     {"1": "GND", "2": "J4_BTN", "3": "J4_GREEN", "4": "GND", "5": "J4_RED", "6": "GND"}),
    ("R9", "R", "1k", P(200, 113), 270, None, True, {"1": "BTN", "2": "J4_BTN"}),
    ("R7", "R", "330", P(200, 120), 270, None, True, {"1": "LED_OK", "2": "J4_GREEN"}),
    ("R8", "R", "330", P(200, 127), 270, None, True, {"1": "LED_RUN", "2": "J4_RED"}),
    ("U2", "PICO", "Pico 2 W", P(262, 104), 0, None, True, {
        "1": "SDA", "2": "SCL",
        "21": "BTN", "22": "FAN_ON", "24": "TACH",
        "25": "LED_OK", "26": "LED_RUN", "31": "LIGHT",
        "33": "GND", "36": "+3V3", "39": "VSYS",
        # The symbol stacks these on pin 3. The footprint does not.
        "3": "GND", "8": "GND", "13": "GND", "18": "GND",
        "23": "GND", "28": "GND", "38": "GND",
    }),
]

# Mirrors, and field positions where the automatic placement would sit on
# a wire. Field offsets are grid steps from the symbol anchor, with a
# justification or None for centered.
SCH_OPTS = {
    "J1": {"mirror": "y"},
    # Pin 2, the fused lead, on top, so neither mains wire crosses the other.
    "J5": {"mirror": "x", "fields": ((2.0, -1.6, "left"), (2.0, -0.2, "left"))},
    # The neutral wire runs just under the fuse, so both lines go above it.
    "F1": {"fields": ((0, -4.4, None), (0, -3.1, None))},
    "U2": {"fields": ((-14.5, -28.2, "left"), (-14.5, 26.5, "left"))},
    # Above and below, clear of R6 and of each connector's stubs.
    "J3": {"fields": ((0, -6.3, None), (0, 8.3, None))},
    "J4": {"fields": ((0, -6.3, None), (0, 8.3, None))},
}

# PCB placement, millimeters from the top-left, footprint anchor, CCW degrees.
# Mains stay on the left. The Pico lies along the bottom with USB to the left.
# The fan header is on the right, away from the AC pins.
PCB_AT = {
    # KiCad's positive angle maps (x, y) to (y, -x). These anchors were
    # checked against that, then against the courtyard report.
    "PS1": (7.0, 14.0, 0),
    # Pin 1 above pin 2, the wire entries facing the mains end wall.
    "J5": (5.6, 31.6, 270),
    "Q1": (70.0, 21.0, 0),
    # The switch stage packs under and beside Q1: R4 gate to source on the
    # left, R3 straight down from the gate, Q2 at the foot of R3, R2 above
    # the base and R1 below it, D2 on the yellow lead.
    "R4": (65.6, 10.84, 270),
    "R3": (70.0, 24.0, 270),
    "Q2": (61.5, 34.16, 0),
    "R2": (66.66, 26.5, 180),
    "R1": (60.0, 38.9, 0),
    "D2": (75.0, 30.5, 0),
    "U1": (50.0, 36.0, 0),
    "D1": (20.0, 39.0, 0),
    # The tach pull-up sits over the tach pin, under U1.
    "R5": (43.0, 39.95, 0),
    # Pins run down from (92, 40); the keying wall faces the board edge.
    "J2": (92.0, 40.0, 270),
    "U2": (13.0, 62.0, 90),
    "J3": (8.0, 67.5, 90),
    # The LDR divider sits beside the sensor cable.
    "R6": (25.5, 67.5, 0),
    # J4 beside the fan header, its resistors past the antenna keepout.
    # Each resistor's lower pad is the GPIO side.
    "J4": (86.0, 52.0, 0),
    "R9": (72.54, 61.66, 90),
    "R7": (75.7, 61.66, 90),
    "R8": (80.16, 61.66, 90),
}


def load_symbols():
    texts = {}
    cached = {}
    pins = {}
    names = {}
    boxes = {}
    fields = {}
    for key, (fn, orig, lib, name, fp) in LIBS.items():
        if fn not in texts:
            texts[fn] = (SYM / fn).read_text(encoding="utf-8")
        body = extract(texts[fn], orig)
        if key == "PICO":
            body = demote_pico(body)
        pins[key] = pin_table(body)
        names[key] = pin_names(body)
        boxes[key] = body_box(body)
        fields[key] = field_positions(body)
        cached[key] = cache_symbol(body, lib, name, orig, name, fp)
    for key in ("GND", "+12V", "+5V", "+3V3", "FLG"):
        if pins[key].get("1") != (0.0, 0.0):
            raise SystemExit(f"power symbol {key} pin 1 is {pins[key].get('1')}, not the origin")
    return {"cached": cached, "pins": pins, "names": names, "boxes": boxes, "fields": fields}


# Width of one character of 1.27 mm schematic text, a little generous.
CHAR_W = 1.1


def centered(x, y, justify, value):
    """A left or right justified anchor turned into the center of the text.

    KiCad turns a field's justification with its symbol, so a right-
    justified field on a symbol rotated 180 degrees runs the wrong way.
    Centered text reads the same whichever way the symbol turns.
    """
    half = len(str(value)) * CHAR_W / 2
    if justify == "left":
        return x + half, y
    if justify == "right":
        return x - half, y
    return x, y


def field_text(name, value, x, y, angle=0, hide=False) -> str:
    hide_s = "\n\t\t\t(hide yes)" if hide else ""
    return f"""		(property "{name}" "{value}"
			(at {fmt(x)} {fmt(y)} {angle}){hide_s}
			(effects
				(font
					(size 1.27 1.27)
				)
			)
		)"""


def instance(lib_id, ref, value, footprint, at, rot, pin_numbers, sym_uuid,
             on_board=True, in_bom=True, mirror=None, fields=None) -> str:
    """fields is ((x, y, justify), (x, y, justify)) for Reference and Value."""
    x, y = at
    pins = "\n".join(
        f'\t\t(pin "{n}"\n\t\t\t(uuid "{uid()}")\n\t\t)' for n in pin_numbers
    )
    board = "yes" if on_board else "no"
    bom = "yes" if in_bom else "no"
    fp = footprint or ""
    mirror_s = f"\n\t\t(mirror {mirror})" if mirror else ""
    if fields is None:
        fields = ((x, y - 5.08, None), (x, y + 5.08, None))
    (rx, ry, rj), (vx, vy, vj) = fields
    rx, ry = centered(rx, ry, rj, ref)
    vx, vy = centered(vx, vy, vj, value)
    # KiCad draws a field on a symbol turned 90 or 270 degrees at the
    # stored angle plus 90. Storing 90 there draws it level.
    fa = 90 if rot % 180 == 90 else 0
    # #PWR and #FLG are KiCad's own hidden power references.
    hide_ref = str(ref).startswith("#")
    return f"""	(symbol
		(lib_id "{lib_id}")
		(at {fmt(x)} {fmt(y)} {rot}){mirror_s}
		(unit 1)
		(exclude_from_sim no)
		(in_bom {bom})
		(on_board {board})
		(dnp no)
		(uuid "{sym_uuid}")
{field_text("Reference", ref, rx, ry, fa, hide_ref)}
{field_text("Value", value, vx, vy, fa)}
{field_text("Footprint", fp, x, y, fa, True)}
{pins}
		(instances
			(project "{PROJECT}"
				(path "/{SHEET_UUID}"
					(reference "{ref}"
					)
					(unit 1)
				)
			)
		)
	)"""


def wire(x1, y1, x2, y2) -> str:
    return f"""	(wire
		(pts
			(xy {fmt(x1)} {fmt(y1)}) (xy {fmt(x2)} {fmt(y2)})
		)
		(stroke
			(width 0)
			(type solid)
		)
		(uuid "{uid()}")
	)"""


def junction(x, y) -> str:
    return f"""	(junction
		(at {fmt(x)} {fmt(y)})
		(diameter 0)
		(color 0 0 0 0)
		(uuid "{uid()}")
	)"""


def label(name, x, y, angle=0) -> str:
    justify = "right bottom" if angle == 180 else "left bottom"
    return f"""	(label "{name}"
		(at {fmt(x)} {fmt(y)} {int(angle)})
		(effects
			(font
				(size 1.27 1.27)
			)
			(justify {justify})
		)
		(uuid "{uid()}")
	)"""


def no_connect(x, y) -> str:
    return f"""	(no_connect
		(at {fmt(x)} {fmt(y)})
		(uuid "{uid()}")
	)"""


def text(content, x, y, size=1.27) -> str:
    esc = content.replace("\\", "\\\\").replace('"', '\\"')
    return f"""	(text "{esc}"
		(exclude_from_sim no)
		(at {fmt(x)} {fmt(y)} 0)
		(effects
			(font
				(size {fmt(size)} {fmt(size)})
			)
			(justify left top)
		)
		(uuid "{uid()}")
	)"""


def rect(x1, y1, x2, y2) -> str:
    return f"""	(rectangle
		(start {fmt(x1)} {fmt(y1)})
		(end {fmt(x2)} {fmt(y2)})
		(stroke
			(width 0.254)
			(type dash)
		)
		(fill
			(type none)
		)
		(uuid "{uid()}")
	)"""


def auto_fields(pins_xy, box):
    """Reference and value beside the body, on a side that has no pins."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    sides = set()
    for px, py in pins_xy:
        dx, dy = px - cx, py - cy
        if abs(dx) >= abs(dy):
            sides.add("R" if dx > 0 else "L")
        else:
            sides.add("B" if dy > 0 else "T")
    gap = 2.0
    line = 1.65
    if "T" not in sides and "B" not in sides:
        return (cx, y0 - gap, None), (cx, y1 + gap, None)
    if "T" not in sides:
        return (cx, y0 - gap - line, None), (cx, y0 - gap, None)
    if "B" not in sides:
        return (cx, y1 + gap, None), (cx, y1 + gap + line, None)
    if "R" not in sides:
        return (x1 + 1.0, cy - 0.9, "left"), (x1 + 1.0, cy + 0.9, "left")
    if "L" not in sides:
        return (x0 - 1.0, cy - 0.9, "right"), (x0 - 1.0, cy + 0.9, "right")
    return (x0, y0 - gap, "left"), (x0, y1 + gap, "left")


def build_schematic(sym) -> dict[str, str]:
    """Return schematic text and the symbol uuid of every on-board part."""
    cached, pins = sym["cached"], sym["pins"]
    bodies = []
    seen = set()
    for key, (_fn, _orig, lib, name, _fp) in LIBS.items():
        lib_id = f"{lib}:{name}"
        if lib_id in seen:
            continue
        seen.add(lib_id)
        bodies.append(cached[key])

    chunks = []
    uuids = {}
    flag_n = 0
    pwr_n = 0
    loc = {}
    net_of = {}

    def box_of(key, at, rot, mirror):
        bx0, by0, bx1, by1 = sym["boxes"][key]
        corners = [transform(at[0], at[1], rot, px, py, mirror)
                   for px, py in ((bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1))]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return (min(xs), min(ys), max(xs), max(ys))

    for ref, key, value, at, rot, fp_override, on_board, netmap in PARTS:
        _fn, _orig, lib, name, fp_default = LIBS[key]
        fp = fp_default if fp_override is None else fp_override
        opts = SCH_OPTS.get(ref, {})
        mirror = opts.get("mirror")
        if mirror and rot:
            raise SystemExit(f"{ref}: mirror is only drawn at rotation 0")
        sym_uuid = uid()
        if on_board:
            uuids[ref] = sym_uuid
        table = pins[key]
        at = (snap(at[0]), snap(at[1]))
        ox, oy = at
        for num, (px, py) in table.items():
            loc[(ref, num)] = transform(ox, oy, rot, px, py, mirror)
            if num in netmap:
                net_of[(ref, num)] = netmap[num]
        if "fields" in opts:
            fields = tuple((ox + g(dx), oy + g(dy), j) for dx, dy, j in opts["fields"])
        else:
            pts = [loc[(ref, num)] for num in table]
            fields = auto_fields(pts, box_of(key, at, rot, mirror))
        chunks.append(instance(
            f"{lib}:{name}", ref, value, fp, at, rot, list(table), sym_uuid,
            on_board=on_board, in_bom=True, mirror=mirror, fields=fields,
        ))
    for ref, *_rest, netmap in PARTS:
        for num in netmap:
            if (ref, num) not in loc:
                raise SystemExit(f"{ref} has no pin {num}")

    def r2(x, y):
        return (round(x, 2), round(y, 2))

    def xy(item):
        if isinstance(item[0], str):
            return loc[(item[0], item[1])]
        return (float(item[0]), float(item[1]))

    # Each chain is a polyline. Every pin on it must be the same net.
    # Corners are bare points. Segments stay on the grid and axis-aligned
    # so they land on the pin hotspot. Nets that cross the sheet meet by
    # label: a short stub off the pin, and a label on the end of it.
    chains = [
        # Mains: panel wires into J5, then J5 into the IRM-10.
        [("J1", "1"), ("F1", "1")],
        [("F1", "2"), ("J5", "2")],
        [("J5", "2"), P(56, 22), P(74, 22), ("PS1", "2")],
        [("J1", "2"), ("J5", "1")],
        [("J5", "1"), P(56, 30), ("PS1", "1")],
        [("PS1", "4"), ("U1", "1")],
        [("U1", "3"), ("D1", "2")],
        [("D1", "1"), P(134, 26)],
        # +12 V at the source, the gate pull-up, the gate driver.
        [P(62, 58), ("Q1", "3")],
        [P(62, 61), ("R4", "1")],
        [("R4", "2"), P(76, 70)],
        [("Q1", "1"), ("R3", "1")],
        [("R3", "2"), P(100, 70), ("Q2", "3")],
        [("R1", "2"), ("Q2", "2")],
        [("R1", "1"), P(78, 78)],
        [("Q1", "2"), ("D2", "1")],
        # Yellow drops under the transistor stage to the fan header.
        [P(62, 76), P(70, 76), P(70, 92), P(118, 92), P(118, 78), ("J2", "2")],
        [("J2", "1"), P(126, 76)],
        [("J2", "3"), P(126, 80)],
        [("J2", "4"), P(126, 82)],
        [("R5", "2"), P(150, 80)],
        # The grille.
        [("LDR1", "2"), P(28, 124), P(32, 124)],
        [("SW1", "1"), P(48, 112), P(52, 112)],
        [("D3", "2"), P(68, 112), P(72, 112)],
        [("D3", "1"), P(68, 123)],
        [("D4", "2"), P(88, 112), P(92, 112)],
        [("D4", "1"), P(88, 123)],
        # Board side of the cables.
        *[[("J3", str(n)), P(114 if n in (1, 2, 6) else 120, 112 + 2 * n)] for n in range(1, 7)],
        [("R6", "1"), P(142, 114)],
        *[[("J4", str(n)), P(156 if n in (1, 4, 6) else 162, 112 + 2 * n)] for n in range(1, 7)],
        [("R9", "2"), P(194, 113)],
        [("R9", "1"), P(206, 113)],
        [("R7", "2"), P(194, 120)],
        [("R7", "1"), P(206, 120)],
        [("R8", "2"), P(194, 127)],
        [("R8", "1"), P(206, 127)],
        # The Pico.
        [("U2", "1"), P(238, 92)],
        [("U2", "2"), P(238, 94)],
        [("U2", "21"), P(286, 94)],
        [("U2", "22"), P(286, 96)],
        [("U2", "24"), P(286, 98)],
        [("U2", "25"), P(286, 100)],
        [("U2", "26"), P(286, 102)],
        [("U2", "31"), P(286, 114)],
        [("U2", "33"), P(284, 122)],
        [("U2", "39"), P(258, 70)],
    ]

    raw = []
    for chain in chains:
        points = [xy(item) for item in chain]
        nets = [net_of[(a, b)] for a, b in chain if isinstance(a, str)]
        if len(set(nets)) != 1:
            raise SystemExit(f"chain mixes nets {nets}: {chain}")
        net = nets[0]
        for a, b in zip(points, points[1:]):
            if r2(*a) == r2(*b):
                continue
            if r2(*a)[0] != r2(*b)[0] and r2(*a)[1] != r2(*b)[1]:
                raise SystemExit(f"diagonal {net} {a} -> {b}")
            raw.append((net, a, b))

    def on_seg(p, a, b):
        x, y = r2(*p)
        x1, y1 = r2(*a)
        x2, y2 = r2(*b)
        if x1 == x2 == x:
            return min(y1, y2) <= y <= max(y1, y2)
        if y1 == y2 == y:
            return min(x1, x2) <= x <= max(x1, x2)
        return False

    def covered(net, p):
        return any(n == net and on_seg(p, a, b) for n, a, b in segs)

    def joins(a, b, c, d):
        """True when KiCad would connect the two segments.

        A cross in the middle of both wires does not connect. An endpoint
        that lands on the other wire does, including a T-junction.
        """
        for p in (a, b):
            if on_seg(p, c, d):
                return True
        for p in (c, d):
            if on_seg(p, a, b):
                return True
        return False

    # net, point, rotation. A rotated ground or supply points away from the
    # connector it hangs off, so it does not lie across the next pin's stub.
    powers = [
        ("+12V", P(95, 26), 0),
        ("+5V", P(116, 26), 0),
        ("GND", loc[("PS1", "3")], 0),
        ("GND", loc[("U1", "2")], 0),
        ("+12V", P(62, 58), 0),
        ("GND", loc[("Q2", "1")], 0),
        ("GND", loc[("R2", "2")], 0),
        ("GND", loc[("D2", "2")], 0),
        ("GND", P(126, 76), 270),
        ("+5V", P(126, 82), 90),
        ("+3V3", loc[("R5", "1")], 0),
        ("+3V3", loc[("LDR1", "1")], 0),
        ("GND", loc[("SW1", "2")], 0),
        ("GND", P(68, 123), 0),
        ("GND", P(88, 123), 0),
        ("GND", P(114, 114), 270),
        ("+3V3", P(114, 116), 90),
        ("+3V3", P(114, 124), 90),
        ("GND", loc[("R6", "2")], 0),
        ("GND", P(156, 114), 270),
        ("GND", P(156, 120), 270),
        ("GND", P(156, 124), 270),
        ("+3V3", loc[("U2", "36")], 0),
        ("GND", loc[("U2", "3")], 0),
        ("GND", P(284, 122), 90),
    ]
    labels = [
        ("AC_L", P(35, 26), 0),
        ("AC_HOT", P(58, 22), 0),
        ("AC_N", P(62, 30), 0),
        ("VSYS", P(128, 26), 0),
        ("GATE", P(70, 70), 0),
        ("COLL", P(92, 70), 0),
        ("BASE", P(90, 78), 0),
        ("FAN_ON", P(78, 78), 180),
        ("YELLOW", P(74, 92), 0),
        ("TACH", P(126, 80), 180),
        ("TACH", P(150, 80), 0),
        ("LIGHT", P(32, 124), 0),
        ("J4_BTN", P(52, 112), 0),
        ("J4_GREEN", P(72, 112), 0),
        ("J4_RED", P(92, 112), 0),
        ("SDA", P(120, 118), 180),
        ("SCL", P(120, 120), 180),
        ("LIGHT", P(120, 122), 180),
        ("LIGHT", P(142, 114), 90),
        ("J4_BTN", P(162, 116), 180),
        ("J4_GREEN", P(162, 118), 180),
        ("J4_RED", P(162, 122), 180),
        ("J4_BTN", P(194, 113), 180),
        ("BTN", P(206, 113), 0),
        ("J4_GREEN", P(194, 120), 180),
        ("LED_OK", P(206, 120), 0),
        ("J4_RED", P(194, 127), 180),
        ("LED_RUN", P(206, 127), 0),
        ("SDA", P(238, 92), 180),
        ("SCL", P(238, 94), 180),
        ("BTN", P(286, 94), 0),
        ("FAN_ON", P(286, 96), 0),
        ("TACH", P(286, 98), 0),
        ("LED_OK", P(286, 100), 0),
        ("LED_RUN", P(286, 102), 0),
        ("LIGHT", P(286, 114), 0),
        ("VSYS", P(258, 70), 90),
    ]
    # The IRM-10's AC pins and the Pico's VSYS are power inputs. Nothing on
    # those nets is a power output, so ERC needs to be told they are fed.
    flags = [
        ("AC_HOT", P(66, 22), 0),
        ("AC_N", P(68, 30), 180),
        ("VSYS", P(134, 26), 0),
    ]

    # A pin, label, or power symbol connects only where a wire ends. Split
    # every segment at those points so nothing sits in the middle of a wire.
    anchors = {}
    for (ref, num), net in net_of.items():
        anchors.setdefault(net, []).append(loc[(ref, num)])
    for net, p, _rot in powers:
        anchors.setdefault(net, []).append(p)
    for net, p, _angle in labels:
        anchors.setdefault(net, []).append(p)
    for net, p, _rot in flags:
        anchors.setdefault(net, []).append(p)
    # Endpoints that land mid-wire have to split that wire. KiCad joins two
    # wires only where both have an endpoint. A bare T is left open.
    for net, a, b in raw:
        anchors.setdefault(net, []).append(a)
        anchors.setdefault(net, []).append(b)

    segs = []
    seen = set()
    for net, a, b in raw:
        pts = [a, b]
        for p in anchors.get(net, []):
            if on_seg(p, a, b) and r2(*p) not in (r2(*a), r2(*b)):
                pts.append(p)
        if r2(*a)[0] == r2(*b)[0]:
            pts.sort(key=lambda p: p[1])
        else:
            pts.sort(key=lambda p: p[0])
        ordered = []
        for p in pts:
            if not ordered or r2(*ordered[-1]) != r2(*p):
                ordered.append(p)
        for p, q in zip(ordered, ordered[1:]):
            key = (net, r2(*p), r2(*q))
            rev = (net, r2(*q), r2(*p))
            if key in seen or rev in seen:
                continue
            seen.add(key)
            segs.append((net, p, q))

    for i, (n1, a, b) in enumerate(segs):
        for n2, c, d in segs[i + 1:]:
            if n1 != n2 and joins(a, b, c, d):
                raise SystemExit(f"short {n1} {a}->{b} meets {n2} {c}->{d}")

    for _net, a, b in segs:
        chunks.append(wire(a[0], a[1], b[0], b[1]))

    def not_on_other(net, p, what):
        for n, a, b in segs:
            if n != net and on_seg(p, a, b):
                raise SystemExit(f"{what} {net} at {p} sits on a {n} wire")

    def power_instance(key, ref, value, p, rot):
        _fn, _o, lib, name, _fp = LIBS[key]
        x, y = p
        bx0, by0, bx1, by1 = box_of(key, (x, y), rot, None)
        fields = []
        for fname in ("Reference", "Value"):
            fx, fy = sym["fields"][key][fname]
            ax, ay = transform(x, y, rot, fx, fy)
            if ax < x - 0.1:
                fields.append((bx0 - 0.5, y, "right"))
            elif ax > x + 0.1:
                fields.append((bx1 + 0.5, y, "left"))
            else:
                fields.append((ax, ay, None))
        return instance(
            f"{lib}:{name}", ref, value, "", (x, y), rot, ["1"], uid(),
            on_board=False, in_bom=False, fields=tuple(fields),
        )

    pin_at = {r2(*loc[p]): n for p, n in net_of.items()}
    for net, (x, y), rot in powers:
        if not covered(net, (x, y)) and pin_at.get(r2(x, y)) != net:
            raise SystemExit(f"power {net} at {(x, y)} is not on that net")
        not_on_other(net, (x, y), "power")
        pwr_n += 1
        key = {"GND": "GND", "+12V": "+12V", "+5V": "+5V", "+3V3": "+3V3"}[net]
        chunks.append(power_instance(key, f"#PWR{pwr_n:02d}", LIBS[key][3], (x, y), rot))

    for net, (x, y), angle in labels:
        if not covered(net, (x, y)):
            raise SystemExit(f"label {net} at {(x, y)} is not on a wire")
        not_on_other(net, (x, y), "label")
        chunks.append(label(net, x, y, angle))

    for net, (x, y), rot in flags:
        if not covered(net, (x, y)):
            raise SystemExit(f"flag {net} at {(x, y)} is not on a wire")
        not_on_other(net, (x, y), "flag")
        flag_n += 1
        chunks.append(power_instance("FLG", f"#FLG{flag_n:02d}", "PWR_FLAG", (x, y), rot))

    power_at = {r2(*p) for _net, p, _rot in powers}
    nc_at = set()
    for (ref, num), (x, y) in loc.items():
        key = r2(x, y)
        own = net_of.get((ref, num))
        on_own = own is not None and (covered(own, (x, y)) or key in power_at and any(
            n == own and r2(*p) == key for n, p, _rot in powers
        ))
        if own and not on_own:
            raise SystemExit(f"{ref}.{num} {own} at {(x, y)} has no wire")
        for n, a, b in segs:
            if on_seg((x, y), a, b) and n != own:
                raise SystemExit(f"{ref}.{num} {own} sits on {n}")
        if own is None and key not in nc_at and not any(on_seg((x, y), a, b) for _n, a, b in segs):
            chunks.append(no_connect(x, y))
            nc_at.add(key)

    # A dot wherever three or more connections meet: wire ends, pins, and
    # power symbols. A symbol's stacked pins count once.
    ends = Counter()
    for _net, a, b in segs:
        ends[r2(*a)] += 1
        ends[r2(*b)] += 1
    for p in {(ref, r2(*xy_)) for (ref, _num), xy_ in loc.items() if (ref, _num) in net_of}:
        ends[p[1]] += 1
    for _net, p, _rot in powers + flags:
        ends[r2(*p)] += 1
    dots = sorted(p for p, n in ends.items() if n >= 3)
    for x, y in dots:
        chunks.append(junction(x, y))

    print(f"  schematic wires {len(segs)}, junctions {len(dots)}", flush=True)

    chunks.append(rect(g(18), g(14), g(140), g(39)))
    chunks.append(rect(g(24), g(20), g(50), g(32)))
    chunks.append(text(
        "120 V AC. The fuse is in one lead. The C8 is not polarized, so either lead can be the hot one. "
        "No earth, and 0 V is not bonded to the box.",
        g(20), g(15), 1.4,
    ))
    chunks.append(text("box wall", g(25), g(20.6), 1.27))
    chunks.append(text(
        "J1 and F1 are on the box wall. Their wires land in J5, the screw terminal beside the IRM-10. "
        "Follow the marks on the module.",
        g(20), g(36.6), 1.27,
    ))
    chunks.append(text("12 V switch and the fan", g(20), g(46), 1.6))
    chunks.append(rect(g(18), g(106), g(104), g(134)))
    chunks.append(text("at the grille, not on this board", g(20), g(107.2), 1.6))
    chunks.append(text(
        "The SHT40 breakout (Adafruit 4885) also sits here, on J3 pins 1 to 4. Its pull-ups serve the bus.",
        g(20), g(129), 1.27,
    ))
    chunks.append(text("board side of the grille cables", g(110), g(106), 1.6))
    notes = [
        "GP17 high turns the fan on. R2 holds the base low while GP17 floats at boot. "
        "The IRF9540 tab is the drain: insulate it from the box.",
        "J2 is a keyed 4-pin fan header. NF-F12: 1 black = 0 V, 2 yellow = switched +12 V, "
        "3 green = tach, 4 blue = +5 V (before the diode, never 12 V).",
        "J3, grille sensors: 1 GND, 2 3V3, 3 SDA / GP0, 4 SCL / GP1, 5 LDR, 6 3V3 for the LDR. "
        "R6 stays on the board.",
        "J4, button and LEDs: 1 GND, 2 button, 3 green anode, 4 green cathode, 5 red anode, 6 red cathode. "
        "R7, R8 and R9 sit between J4 and the Pico.",
        "Labels with the same name are one net. VBUS, pin 40, gets no wire. GP22 is free. "
        "The DHT22 and the SGP41 are not on this board.",
    ]
    for i, note in enumerate(notes):
        chunks.append(text(note, g(20), g(160 + 4 * i), 1.27))

    lib_symbols = "\n".join(bodies)
    sch = f"""(kicad_sch
	(version 20250114)
	(generator "generate.py")
	(generator_version "10.0")
	(uuid "{SHEET_UUID}")
	(paper "A3")
	(title_block
		(title "Smartfan V2")
		(date "{DATE}")
		(rev "1")
		(company "")
		(comment 1 "120 V AC to 12 V and 5 V. NF-F12 on an IRF9540 high side. SHT40 on I2C.")
		(comment 2 "C8 and the fuse are panel parts wired to J5. VBUS has no wire. Do not bond 0 V to the box.")
	)
	(lib_symbols
{lib_symbols}
	)
{chr(10).join(chunks)}
	(sheet_instances
		(path "/"
			(page "1")
		)
	)
	(embedded_fonts no)
)
"""
    return {"text": sch, "uuids": uuids}


def footprint_path(fp_id: str) -> tuple[str, str, str]:
    nick, name = fp_id.split(":", 1)
    # Nickname directory is the pretty name KiCad ships.
    pretty = {
        "Converter_ACDC": "Converter_ACDC.pretty",
        "Converter_DCDC": "Converter_DCDC.pretty",
        "Diode_THT": "Diode_THT.pretty",
        "Package_TO_SOT_THT": "Package_TO_SOT_THT.pretty",
        "Resistor_THT": "Resistor_THT.pretty",
        "LED_THT": "LED_THT.pretty",
        "Connector": "Connector.pretty",
        "Connector_PinHeader_2.54mm": "Connector_PinHeader_2.54mm.pretty",
        "Connector_PinSocket_2.54mm": "Connector_PinSocket_2.54mm.pretty",
        "TerminalBlock_Phoenix": "TerminalBlock_Phoenix.pretty",
        "Button_Switch_THT": "Button_Switch_THT.pretty",
        "Module": "Module.pretty",
    }[nick]
    return str(FP / pretty), nick, name


def dist_point_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    if length < 1e-12:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def seg_dist(a, b, c, d):
    """Distance between two segments in any direction."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4):
        return 0.0
    return min(
        dist_point_seg(*a, *c, *d), dist_point_seg(*b, *c, *d),
        dist_point_seg(*c, *a, *b), dist_point_seg(*d, *a, *b),
    )


def build_board(uuids, sym) -> None:
    import pcbnew

    pcb_path = str(ROOT / f"{PROJECT}.kicad_pcb")
    board = pcbnew.NewBoard(pcb_path)
    board.SetCopperLayerCount(2)

    def edge(x1, y1, x2, y2):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I_MM(x1, y1))
        seg.SetEnd(pcbnew.VECTOR2I_MM(x2, y2))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.1))
        board.Add(seg)

    edge(0, 0, BOARD_W, 0)
    edge(BOARD_W, 0, BOARD_W, BOARD_H)
    edge(BOARD_W, BOARD_H, 0, BOARD_H)
    edge(0, BOARD_H, 0, 0)

    def circle(cx, cy, radius, layer, width):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_CIRCLE)
        shape.SetCenter(pcbnew.VECTOR2I_MM(cx, cy))
        shape.SetRadius(pcbnew.FromMM(radius))
        shape.SetLayer(layer)
        shape.SetWidth(pcbnew.FromMM(width))
        board.Add(shape)

    # NPTH. A plated hole on the ground pour would put the screw at 0 V,
    # and the screw meets the aluminum box.
    for hx, hy in HOLES:
        circle(hx, hy, HOLE_D / 2, pcbnew.Edge_Cuts, 0.05)
        circle(hx, hy, HOLE_D / 2 + 0.7, pcbnew.F_SilkS, 0.15)
    print(f"  mounting holes {len(HOLES)}", flush=True)

    nets: dict[str, object] = {}

    def get_net(name):
        if name not in nets:
            item = pcbnew.NETINFO_ITEM(board, net_name(name))
            board.Add(item)
            nets[name] = item
        return nets[name]

    fps = {}
    for ref, key, value, _at, _rot, fp_override, on_board, netmap in PARTS:
        if not on_board:
            continue
        _fn, _orig, _lib, _name, fp_default = LIBS[key]
        fp_id = fp_default if fp_override is None else fp_override
        lib_path, nick, fp_name = footprint_path(fp_id)
        fp = pcbnew.FootprintLoad(lib_path, fp_name)
        if fp is None:
            raise SystemExit(f"footprint missing: {fp_id}")
        # Keep the library nickname, or schematic parity reports every part.
        fp.SetFPID(pcbnew.LIB_ID(nick, fp_name))
        x, y, rot = PCB_AT[ref]
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        fp.SetOrientation(pcbnew.EDA_ANGLE(rot, pcbnew.DEGREES_T))
        fp.SetReference(ref)
        fp.SetValue(value)
        try:
            fp.SetPath(pcbnew.KIID_PATH(f"/{SHEET_UUID}/{uuids[ref]}"))
        except Exception as e:
            print(f"  path not set on {ref}: {e}")
        names = sym["names"][key]
        for pad in fp.Pads():
            num = pad.GetNumber()
            if not num:
                continue
            if num in netmap:
                pad.SetNet(get_net(netmap[num]))
            elif num in names:
                # The net KiCad gives an unconnected pin, so parity matches.
                pad.SetNet(get_net(f"unconnected-({ref}-{names[num]}-Pad{num})"))
        board.Add(fp)
        fps[ref] = fp

    boxes = []
    for ref, fp in fps.items():
        poly = fp.GetCourtyard(pcbnew.F_CrtYd)
        bb = poly.BBox()
        box = (
            pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
            pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()),
        )
        boxes.append((ref, box))
        if box[0] < 0 or box[1] < 0 or box[2] > BOARD_W or box[3] > BOARD_H:
            print(f"  off board {ref} {tuple(round(v, 2) for v in box)}")
    overlap = False
    for i, (ra, a) in enumerate(boxes):
        for rb, b in boxes[i + 1:]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                print(f"  courtyard overlap {ra} {rb}")
                overlap = True
    if overlap:
        raise SystemExit("courtyards overlap; placement not saved")
    for hx, hy in HOLES:
        if min(hx, hy, BOARD_W - hx, BOARD_H - hy) < HOLE_D / 2 + 1.5:
            raise SystemExit(f"mounting hole {(hx, hy)} is too close to the edge")
        for ref, box in boxes:
            dx = max(box[0] - hx, 0.0, hx - box[2])
            dy = max(box[1] - hy, 0.0, hy - box[3])
            dist = math.hypot(dx, dy)
            if dist < HOLE_D / 2 + 1.0:
                raise SystemExit(
                    f"mounting hole {(hx, hy)} meets {ref} courtyard ({dist:.2f} mm)"
                )

    # Back-side ground, kept off the IRM-10's AC half and MAINS_GAP clear of
    # J5 and the AC traces. Thermal relief, because the board is hand-soldered.
    gnd = get_net("GND")
    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.B_Cu)
    zone.SetNet(gnd)
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    zone.SetThermalReliefGap(pcbnew.FromMM(0.3))
    zone.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.4))
    zone.SetMinThickness(pcbnew.FromMM(0.25))
    zone.SetLocalClearance(pcbnew.FromMM(0.3))
    outline = zone.Outline()
    outline.NewOutline()
    # The DC ground pin is at x=45.5 and has to be inside the pour. The AC
    # copper ends at x=10.5 and y=38.0; the notch is 6.5 mm past both.
    for x, y in (
        (36, 1), (BOARD_W - 1, 1), (BOARD_W - 1, BOARD_H - 1),
        (1, BOARD_H - 1), (1, 44.5), (17, 44.5), (17, 33), (36, 33),
    ):
        outline.Append(int(pcbnew.FromMM(x)), int(pcbnew.FromMM(y)))
    board.Add(zone)

    def rot_xy(x, y, deg):
        if deg % 360 == 0:
            return x, y
        if deg % 360 == 90:
            return y, -x
        if deg % 360 == 180:
            return -x, -y
        if deg % 360 == 270:
            return -y, x
        raise SystemExit(f"rotation {deg}")

    def board_box(local, anchor, deg):
        pts = [rot_xy(x, y, deg) for x, y in local]
        xs = [anchor[0] + p[0] for p in pts]
        ys = [anchor[1] + p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    # Tab metal of Q1, leads excluded. Local coordinates of the footprint.
    tab_box = board_box(
        ((-2.6, -19.6), (7.7, -19.6), (7.7, -3.6), (-2.6, -3.6)),
        PCB_AT["Q1"][:2], PCB_AT["Q1"][2],
    )
    # AC half of the IRM-10. The router keeps low-voltage tracks out of it.
    # J5 and the AC traces below it are covered by the distance check.
    ac_box = (0.0, 0.0, 36.0, 31.0)
    ant_box = ANT_KEEP

    hole_boxes = tuple(
        (hx - HOLE_KEEP, hy - HOLE_KEEP, hx + HOLE_KEEP, hy + HOLE_KEEP)
        for hx, hy in HOLES
    )

    def in_keepout(x, y, inflate=0.0):
        for box in (ac_box, tab_box, ant_box, *hole_boxes):
            if (box[0] - inflate <= x <= box[2] + inflate
                    and box[1] - inflate <= y <= box[3] + inflate):
                return True
        return False

    # No copper under the MOSFET tab (the tab is the drain), around the
    # mounting holes, or under and past the Pico antenna. Pads stay
    # allowed: the Pico's own pins run along both sides of the antenna.
    for box in (tab_box, ant_box, *hole_boxes):
        keep = pcbnew.ZONE(board)
        keep.SetIsRuleArea(True)
        keep.SetDoNotAllowTracks(True)
        keep.SetDoNotAllowVias(True)
        keep.SetDoNotAllowZoneFills(True)
        keep.SetDoNotAllowPads(False)
        keep.SetDoNotAllowFootprints(False)
        keep.SetLayerSet(pcbnew.LSET.AllCuMask())
        ko = keep.Outline()
        ko.NewOutline()
        x1, y1, x2, y2 = box
        for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
            ko.Append(int(pcbnew.FromMM(x)), int(pcbnew.FromMM(y)))
        board.Add(keep)

    # in_keepout is defined above, next to the rule area.
    # Clearance uses the real pad copper (GetEffectiveShape), not a circle.
    # A circle around the TO-92 rectangle's corners covered the next pad,
    # so no trace could leave the S8050 base.
    obstacles = []
    pad_pts = {}
    ac_pads = []
    for fp in fps.values():
        for pad in fp.Pads():
            pos = pad.GetPosition()
            x, y = pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)
            name = plain_name(pad.GetNetname())
            shape = pad.GetEffectiveShape(pcbnew.F_Cu)
            obstacles.append((x, y, shape, name))
            if name:
                pad_pts.setdefault(name, []).append((x, y))
            if name in AC_NETS:
                bb = shape.BBox()
                ac_pads.append((shape, (
                    pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                    pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()),
                )))
    ac_tracks = []  # x1, y1, x2, y2, width

    tracks = []  # x1, y1, x2, y2, net, width, layer id
    # Half a millimeter clears the TO-92 flat. A finer grid makes the
    # search larger than the expansion cap and still misses open paths.
    GRID = 0.5
    CLEARANCE = 0.2
    BIN = 5.0
    pad_bins = {}
    for obs in obstacles:
        pad_bins.setdefault((int(obs[0] // BIN), int(obs[1] // BIN)), []).append(obs)
    track_bins = {}

    def bin_keys(x1, y1, x2, y2):
        keys = set()
        span = max(abs(x2 - x1), abs(y2 - y1), BIN)
        steps = int(span / BIN) + 1
        for i in range(steps + 1):
            t = i / steps
            ix = int((x1 + (x2 - x1) * t) // BIN)
            iy = int((y1 + (y2 - y1) * t) // BIN)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    keys.add((ix + dx, iy + dy))
        return keys

    def hits_keepout(x1, y1, x2, y2, inflate):
        span = max(abs(x2 - x1), abs(y2 - y1))
        steps = int(span / GRID) + 1
        for i in range(steps + 1):
            t = i / steps
            if in_keepout(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, inflate):
                return True
        return False

    def seg_gap(x1, y1, x2, y2, x3, y3, x4, y4):
        h1 = abs(y1 - y2) < 1e-6
        h2 = abs(y3 - y4) < 1e-6
        if h1 == h2:
            if h1:
                overlap = max(min(x1, x2), min(x3, x4)) <= min(max(x1, x2), max(x3, x4))
                if overlap:
                    return abs(y1 - y3)
            else:
                overlap = max(min(y1, y2), min(y3, y4)) <= min(max(y1, y2), max(y3, y4))
                if overlap:
                    return abs(x1 - x3)
            return min(
                math.hypot(x1 - x3, y1 - y3), math.hypot(x1 - x4, y1 - y4),
                math.hypot(x2 - x3, y2 - y3), math.hypot(x2 - x4, y2 - y4),
            )
        if h1:
            hx1, hx2, hy = min(x1, x2), max(x1, x2), y1
            vx, vy1, vy2 = x3, min(y3, y4), max(y3, y4)
        else:
            hx1, hx2, hy = min(x3, x4), max(x3, x4), y3
            vx, vy1, vy2 = x1, min(y1, y2), max(y1, y2)
        if hx1 - 1e-6 <= vx <= hx2 + 1e-6 and vy1 - 1e-6 <= hy <= vy2 + 1e-6:
            return 0.0
        return min(
            dist_point_seg(vx, vy1, hx1, hy, hx2, hy),
            dist_point_seg(vx, vy2, hx1, hy, hx2, hy),
            dist_point_seg(hx1, hy, vx, vy1, vx, vy2),
            dist_point_seg(hx2, hy, vx, vy1, vx, vy2),
        )

    def mains_ok(x1, y1, x2, y2, net, width, seg):
        """Low-voltage copper stays MAINS_GAP from AC copper, on either layer."""
        if net in AC_NETS:
            return True
        gap = MAINS_GAP + 0.05
        gap_iu = pcbnew.FromMM(gap)
        for shape, _bb in ac_pads:
            if shape.Collide(seg, gap_iu):
                return False
        for tx1, ty1, tx2, ty2, tw in ac_tracks:
            if seg_dist((x1, y1), (x2, y2), (tx1, ty1), (tx2, ty2)) < gap + width / 2 + tw / 2:
                return False
        return True

    def segment_ok(x1, y1, x2, y2, net, width, layer):
        margin = width / 2 + 0.4
        if min(x1, x2) < margin or min(y1, y2) < margin:
            return False
        if max(x1, x2) > BOARD_W - margin or max(y1, y2) > BOARD_H - margin:
            return False
        if hits_keepout(x1, y1, x2, y2, width / 2):
            return False
        need_track = width / 2 + CLEARANCE + 0.01
        seg = pcbnew.SHAPE_SEGMENT(
            pcbnew.VECTOR2I_MM(x1, y1),
            pcbnew.VECTOR2I_MM(x2, y2),
            pcbnew.FromMM(width),
        )
        if not mains_ok(x1, y1, x2, y2, net, width, seg):
            return False
        clear_iu = pcbnew.FromMM(CLEARANCE + 0.01)
        seen_tracks = set()
        for key in bin_keys(x1, y1, x2, y2):
            for _px, _py, shape, name in pad_bins.get(key, ()):
                if name == net:
                    continue
                if shape.Collide(seg, clear_iu):
                    return False
            for index in track_bins.get(key, ()):
                if index in seen_tracks:
                    continue
                seen_tracks.add(index)
                tx1, ty1, tx2, ty2, tnet, twidth, tlayer = tracks[index]
                if tlayer != layer or tnet == net:
                    continue
                if seg_gap(x1, y1, x2, y2, tx1, ty1, tx2, ty2) < need_track + twidth / 2:
                    return False
        return True

    def straighten(path):
        """Drop the points where a path carries straight on."""
        out = [path[0]]
        for i in range(1, len(path) - 1):
            ax, ay = out[-1]
            bx, by = path[i]
            cx, cy = path[i + 1]
            if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) < 1e-9:
                continue
            out.append(path[i])
        out.append(path[-1])
        return out

    board_tracks = []

    def index_track(index):
        x1, y1, x2, y2 = tracks[index][:4]
        span = max(abs(x2 - x1), abs(y2 - y1), BIN)
        steps = int(span / BIN) + 1
        for i in range(steps + 1):
            t = i / steps
            key = (
                int((x1 + (x2 - x1) * t) // BIN),
                int((y1 + (y2 - y1) * t) // BIN),
            )
            track_bins.setdefault(key, []).append(index)

    def commit(path, net, width, layer):
        item = get_net(net)
        path = straighten(path)
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            if abs(x1 - x2) < 0.01 and abs(y1 - y2) < 0.01:
                continue
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I_MM(x1, y1))
            track.SetEnd(pcbnew.VECTOR2I_MM(x2, y2))
            track.SetWidth(pcbnew.FromMM(width))
            track.SetLayer(layer)
            track.SetNet(item)
            board.Add(track)
            board_tracks.append(track)
            tracks.append((x1, y1, x2, y2, net, width, layer))
            if net in AC_NETS:
                ac_tracks.append((x1, y1, x2, y2, width))
            index_track(len(tracks) - 1)

    def rip_up(mark):
        """Take every track laid after mark back off the board."""
        for track in board_tracks[mark:]:
            board.Remove(track)
        del board_tracks[mark:]
        del tracks[mark:]
        track_bins.clear()
        for index in range(len(tracks)):
            index_track(index)

    def route_pair(a, b, net, width):
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            path = bfs(a, b, net, width, layer)
            if path:
                commit(path, net, width, layer)
                return True
        return False

    def bfs(start, goal, net, width, layer):
        """A* on a 0.5 mm grid. Cost is length plus a penalty per bend, so
        the path is the shortest tidy one, not the first one found.
        """
        # Start on the pad center. Snapping a 1.27 mm TO-92 pitch onto a
        # 0.5 mm grid drops the first point into the next pad's clearance.
        start = (round(start[0], 3), round(start[1], 3))
        goal = (round(goal[0], 3), round(goal[1], 3))
        BEND = 1.0
        came = {start: None}
        cost = {start: 0.0}
        counter = 0
        queue = [(abs(start[0] - goal[0]) + abs(start[1] - goal[1]), counter, start)]
        seen = set()
        expansions = 0
        while queue and expansions < 60000:
            _prio, _n, cur = heapq.heappop(queue)
            if cur in seen:
                continue
            seen.add(cur)
            expansions += 1
            if cur == goal:
                path = []
                node = cur
                while node is not None:
                    path.append(node)
                    node = came[node]
                path.reverse()
                return path
            prev = came[cur]
            options = [
                (round(cur[0] + GRID, 3), cur[1]),
                (round(cur[0] - GRID, 3), cur[1]),
                (cur[0], round(cur[1] + GRID, 3)),
                (cur[0], round(cur[1] - GRID, 3)),
            ]
            # Jog onto the goal's row or column once it is close, then in.
            if abs(cur[0] - goal[0]) + abs(cur[1] - goal[1]) < 4:
                options.append((goal[0], cur[1]))
                options.append((cur[0], goal[1]))
            if abs(cur[0] - goal[0]) < 1e-6 or abs(cur[1] - goal[1]) < 1e-6:
                options.append(goal)
            for nxt in options:
                if nxt in seen:
                    continue
                dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
                if abs(dx) < 0.01 and abs(dy) < 0.01:
                    continue
                step = abs(dx) + abs(dy)
                if prev is not None:
                    px, py = cur[0] - prev[0], cur[1] - prev[1]
                    if abs(px * dy - py * dx) > 1e-9:
                        step += BEND
                new = cost[cur] + step
                if new >= cost.get(nxt, math.inf):
                    continue
                if not segment_ok(cur[0], cur[1], nxt[0], nxt[1], net, width, layer):
                    continue
                cost[nxt] = new
                came[nxt] = cur
                counter += 1
                heuristic = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
                heapq.heappush(queue, (new + heuristic, counter, nxt))
        return None

    def route_net(name, width, points):
        if len(points) < 2:
            return True
        # Drop pad centers that landed on top of each other.
        unique = []
        for point in points:
            if all(abs(point[0] - u[0]) > 0.05 or abs(point[1] - u[1]) > 0.05 for u in unique):
                unique.append(point)
        connected = [unique[0]]
        pending = unique[1:]
        ok = True
        while pending:
            pair = min(
                ((abs(p[0] - c[0]) + abs(p[1] - c[1]), p, c) for p in pending for c in connected),
                key=lambda item: item[0],
            )
            pending.remove(pair[1])
            if pair[0] < 0.05:
                connected.append(pair[1])
                continue
            if not route_pair(pair[2], pair[1], name, width):
                print(f"  unrouted {name}")
                ok = False
            connected.append(pair[1])
        return ok

    # The S8050 base has to be able to step off its own pad. If this fails,
    # the clearance model is covering the pad center again.
    base_pad = min(pad_pts["BASE"], key=lambda p: abs(p[0] - 62.77) + abs(p[1] - 34.16))
    if not segment_ok(base_pad[0], base_pad[1], base_pad[0], base_pad[1] + GRID, "BASE", 0.4, pcbnew.F_Cu):
        raise SystemExit("base pad is boxed in by its own clearance model")

    def near(points, xy):
        hit = [p for p in points if abs(p[0] - xy[0]) < 1.2 and abs(p[1] - xy[1]) < 1.2]
        if len(hit) != 1:
            raise SystemExit(f"expected one pad near {xy}, found {hit}")
        return hit[0]

    def lay(chains, net, width, layer):
        # Check every leg before committing, so a blocked net is left for the router.
        for points in chains:
            for a, b in zip(points, points[1:]):
                if abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01:
                    continue
                if not segment_ok(a[0], a[1], b[0], b[1], net, width, layer):
                    print(
                        f"  manual {net} blocked "
                        f"({a[0]:.2f},{a[1]:.2f}) -> ({b[0]:.2f},{b[1]:.2f})"
                    )
                    return False
        for points in chains:
            commit(points, net, width, layer)
        return True

    # 120 V first, on the front, 1 mm wide. Neutral goes up the left edge,
    # outside the IRM-10's AC/L pin. The fused lead goes up the right of
    # J5. Line to neutral stays 3 mm or more. Everything else keeps
    # MAINS_GAP from these, which the router enforces from here on.
    acn_j5 = near(pad_pts["AC_N"], (5.60, 31.60))
    acn_ps = near(pad_pts["AC_N"], (7.00, 14.00))
    ach_j5 = near(pad_pts["AC_HOT"], (5.60, 36.68))
    ach_ps = near(pad_pts["AC_HOT"], (7.00, 24.75))
    commit([acn_j5, (1.8, acn_j5[1]), (1.8, acn_ps[1]), acn_ps], "AC_N", 1.0, pcbnew.F_Cu)
    commit([ach_j5, (10.0, ach_j5[1]), (10.0, ach_ps[1]), ach_ps], "AC_HOT", 1.0, pcbnew.F_Cu)
    # Line to neutral, copper edge to copper edge, pads taken as their box.
    def ac_copper(net):
        out = [("seg", t) for t in tracks if t[4] == net]
        for x, y, shape, name in obstacles:
            if name == net:
                bb = shape.BBox()
                out.append(("box", (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                                    pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))))
        return out

    def gap(a, b):
        if a[0] == "box" and b[0] == "box":
            (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = a[1], b[1]
            return math.hypot(max(bx0 - ax1, ax0 - bx1, 0), max(by0 - ay1, ay0 - by1, 0))
        if a[0] == "box":
            a, b = b, a
        x1, y1, x2, y2, _net, w, _layer = a[1]
        if b[0] == "seg":
            u1, v1, u2, v2, _n, w2, _l = b[1]
            return seg_dist((x1, y1), (x2, y2), (u1, v1), (u2, v2)) - w / 2 - w2 / 2
        bx0, by0, bx1, by1 = b[1]
        corners = ((bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1))
        edges = list(zip(corners, corners[1:] + corners[:1]))
        return min(seg_dist((x1, y1), (x2, y2), p, q) for p, q in edges) - w / 2

    # J5's own pads are 2.48 mm apart, which Phoenix rates for 250 V.
    ln_gap = min(gap(a, b) for a in ac_copper("AC_N") for b in ac_copper("AC_HOT"))
    if ln_gap < 2.4:
        raise SystemExit(f"line to neutral is {ln_gap:.2f} mm")
    print(f"  mains traces laid, line to neutral {ln_gap:.2f} mm", flush=True)

    # Every mounting hole: a screw head or standoff stays MAINS_GAP from 120 V.
    for hx, hy in HOLES:
        near_ac = min(
            [math.hypot(max(bb[0] - hx, 0, hx - bb[2]), max(bb[1] - hy, 0, hy - bb[3]))
             for _s, bb in ac_pads]
            + [dist_point_seg(hx, hy, t[0], t[1], t[2], t[3]) - t[4] / 2 for t in ac_tracks]
        )
        if near_ac < SCREW_R + MAINS_GAP:
            raise SystemExit(f"mounting hole {(hx, hy)} is {near_ac:.1f} mm from 120 V copper")

    manual = {"AC_N": True, "AC_HOT": True}

    def via(x, y, net):
        """A via, and an obstacle for the router on both layers."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        v.SetWidth(pcbnew.FromMM(VIA_D))
        v.SetDrill(pcbnew.FromMM(VIA_DRILL))
        v.SetNet(get_net(net))
        board.Add(v)
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            tracks.append((x, y, x + 0.001, y, net, VIA_D, layer))
            index_track(len(tracks) - 1)

    def route(net, legs):
        """legs: (layer, width, points) or ("via", x, y). All or nothing."""
        for leg in legs:
            if leg[0] == "via":
                continue
            layer, width, points = leg
            for a, b in zip(points, points[1:]):
                if abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01:
                    continue
                if not segment_ok(a[0], a[1], b[0], b[1], net, width, layer):
                    raise SystemExit(
                        f"route {net} blocked ({a[0]:.2f},{a[1]:.2f}) -> ({b[0]:.2f},{b[1]:.2f})"
                    )
        for leg in legs:
            if leg[0] == "via":
                via(leg[1], leg[2], net)
            else:
                commit(leg[2], net, leg[1], leg[0])
        manual[net] = True

    F, B = pcbnew.F_Cu, pcbnew.B_Cu

    def pad(net, xy):
        return near(pad_pts[net], xy)

    # Pico pins on the top row, which faces the fan stage.
    u_btn = pad("BTN", (61.26, 44.22))
    u_fan = pad("FAN_ON", (58.72, 44.22))
    u_tach = pad("TACH", (53.64, 44.22))
    u_ok = pad("LED_OK", (51.10, 44.22))
    u_run = pad("LED_RUN", (48.56, 44.22))
    u_3v3 = pad("+3V3", (23.16, 44.22))

    # Power. +12 V and +5 V run on the back so the front stays open for the
    # signals. Ground is the pour. +12 V passes under Q1's pins, below the
    # tab keepout, and comes up to the source from beneath.
    p12_ps = pad("+12V", (45.50, 16.75))
    p12_u1 = pad("+12V", (50.00, 36.00))
    p12_q1 = pad("+12V", (75.08, 21.00))
    p12_r4 = pad("+12V", (65.60, 10.84))
    route("+12V", [
        (B, 0.8, [p12_ps, (48.0, p12_ps[1]), (48.0, p12_u1[1]), p12_u1]),
        (B, 0.8, [(48.0, 18.6), (p12_q1[0], 18.6), p12_q1]),
        (B, 0.8, [(p12_r4[0], 18.6), p12_r4]),
    ])
    # +5 V runs under the Pico's top row: left to the 1N5819, right to the
    # fan's blue lead.
    p5_u1 = pad("+5V", (55.08, 36.00))
    p5_d1 = pad("+5V", (30.16, 39.00))
    p5_j2 = pad("+5V", (92.00, 47.62))
    route("+5V", [
        (B, 0.8, [p5_u1, (p5_u1[0], 41.6), (p5_d1[0], 41.6), p5_d1]),
        (B, 0.8, [(p5_u1[0], 41.6), (84.0, 41.6), (84.0, p5_j2[1]), p5_j2]),
    ])
    # VSYS drops below the diode before it turns left, which keeps it
    # MAINS_GAP from the fused lead's corner at (10, 36.7).
    vs_d = pad("VSYS", (20.00, 39.00))
    vs_u = pad("VSYS", (15.54, 44.22))
    route("VSYS", [(F, 0.6, [vs_d, (vs_d[0], 42.5), (vs_u[0], 42.5), vs_u])])

    # Two ground pads sit where the pour cannot grow two spokes. Tie each
    # to a pad the pour does reach, and keep the pour off the weak pad.
    def pad_by(ref, number):
        for p in fps[ref].Pads():
            if p.GetNumber() == number:
                return p
        raise SystemExit(f"missing pad {ref}.{number}")

    def stitch(pad_obj, points):
        if not lay([points], "GND", 0.5, F):
            raise SystemExit("ground stitch blocked")
        pad_obj.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)

    gnd_u1 = pad("GND", (52.54, 36.00))
    gnd_ps1 = pad("GND", (45.50, 24.75))
    gnd_28 = pad("GND", (43.48, 44.22))
    gnd_33 = pad("GND", (30.78, 44.22))
    # U1's ground goes up to the IRM-10's 0 V pin, not down across the
    # corridor the fan, tach and button traces use.
    stitch(pad_by("U1", "2"), [gnd_u1, (gnd_u1[0], 30.0), (gnd_ps1[0], 30.0), gnd_ps1])
    stitch(pad_by("U2", "28"), [gnd_28, (gnd_28[0], 42.6), (gnd_33[0], 42.6), gnd_33])

    # The fan switch. R4 holds the gate at the source, R3 drops from the
    # gate to the S8050 collector, R1 and R2 meet the base from below and
    # from above. Nothing here crosses anything else.
    route("GATE", [(F, 0.4, [pad("GATE", (65.60, 21.00)), pad("GATE", (70.00, 21.00)),
                             pad("GATE", (70.00, 24.00))])])
    route("COLL", [(F, 0.4, [pad("COLL", (70.00, 34.16)), pad("COLL", (64.04, 34.16))])])
    q2_b = pad("BASE", (62.77, 34.16))
    r1_b = pad("BASE", (70.16, 38.90))
    r2_b = pad("BASE", (66.66, 26.50))
    route("BASE", [
        (F, 0.4, [r1_b, (r1_b[0], 36.6), (q2_b[0], 36.6), q2_b]),
        (F, 0.4, [r2_b, (r2_b[0], 30.6), (q2_b[0], 30.6), q2_b]),
    ])
    route("FAN_ON", [(F, 0.4, [u_fan, (u_fan[0], 41.6), (60.0, 41.6),
                               pad("FAN_ON", (60.00, 38.90))])])
    # Yellow leaves the drain, drops past R3, and runs to the fan header
    # over everything else. D2 hangs off it close to Q1.
    yel_q = pad("YELLOW", (72.54, 21.00))
    yel_d = pad("YELLOW", (75.00, 30.50))
    yel_j = pad("YELLOW", (92.00, 42.54))
    route("YELLOW", [
        (F, 0.8, [yel_q, (yel_q[0], 28.0), (88.0, 28.0), (88.0, yel_j[1]), yel_j]),
        (F, 0.8, [(yel_d[0], 28.0), yel_d]),
    ])
    # The tach pin sits between the LED pins and the fan pin, and has to
    # cross the fan's path. It goes under on the back, rises through a via
    # past the button trace, and reaches J2 below the yellow lead. R5 sits
    # above the pin, fed from the Pico's 3V3 on the left.
    r5_t = pad("TACH", (53.16, 39.95))
    j2_t = pad("TACH", (92.00, 45.08))
    route("TACH", [
        (F, 0.4, [r5_t, (r5_t[0], 43.0), (u_tach[0], 43.0), u_tach]),
        (B, 0.4, [u_tach, (u_tach[0], 42.7), (78.0, 42.7)]),
        ("via", 78.0, 42.7),
        (F, 0.4, [(78.0, 42.7), (78.0, j2_t[1]), j2_t]),
    ])
    r5_p = pad("+3V3", (43.00, 39.95))
    if not lay([[u_3v3, (u_3v3[0], 41.6), (r5_p[0], 41.6), r5_p]], "+3V3", 0.6, F):
        raise SystemExit("3V3 to R5 blocked")

    # The button, and the LEDs. The button trace runs right above the pins
    # and down past the antenna keepout to R9. The LED lines go the other
    # way, under the socketed Pico on the front, out between its bottom-row
    # pins, and along the bottom edge to R7 and R8. Each resistor's lower
    # pad is the GPIO side.
    route("BTN", [(F, 0.4, [u_btn, (u_btn[0], 42.9), (70.5, 42.9), (70.5, 61.66),
                            pad("BTN", (72.54, 61.66))])])
    route("LED_OK", [(F, 0.4, [u_ok, (u_ok[0], 58.6), (52.37, 58.6), (52.37, 63.7),
                               (75.7, 63.7), pad("LED_OK", (75.70, 61.66))])])
    route("LED_RUN", [(F, 0.4, [u_run, (u_run[0], 59.5), (49.83, 59.5), (49.83, 64.6),
                                (80.16, 64.6), pad("LED_RUN", (80.16, 61.66))])])
    # J4 stands beside the fan header. The three cable lines nest: the left
    # resistor takes the highest line and the top signal pin.
    route("J4_BTN", [(F, 0.4, [pad("J4_BTN", (72.54, 51.50)), (72.54, 49.0), (84.6, 49.0),
                               (84.6, 54.54), pad("J4_BTN", (86.0, 54.54))])])
    route("J4_GREEN", [(F, 0.4, [pad("J4_GREEN", (75.70, 51.50)), (75.7, 50.1), (83.6, 50.1),
                                 (83.6, 57.08), pad("J4_GREEN", (86.0, 57.08))])])
    route("J4_RED", [(F, 0.4, [pad("J4_RED", (80.16, 51.50)), (82.6, 51.5), (82.6, 62.16),
                               pad("J4_RED", (86.0, 62.16))])])
    print(f"  laid by hand: {', '.join(sorted(n for n, ok in manual.items() if ok))}", flush=True)

    # What is left is short and local: the sensor cable and the rest of 3V3.
    # R5's 3V3 pad is already tied to the Pico, so the router leaves it out.
    wide = {"+3V3": 0.6}
    nets_left = {n: pts for n, pts in pad_pts.items()
                 if n != "GND" and not manual.get(n) and len(pts) >= 2}
    nets_left["+3V3"] = [pt for pt in pad_pts["+3V3"] if pt != r5_p]
    todo = sorted(nets_left)
    mark = len(board_tracks)
    for attempt in range(1, 9):
        failed = [n for n in todo if not route_net(n, wide.get(n, 0.4), nets_left[n])]
        if not failed:
            break
        # A short path laid early can close the only way out for a later
        # net. Take the router's tracks up and lay the failed nets first.
        print(f"  attempt {attempt}: {', '.join(failed)} blocked; rerouting them first", flush=True)
        rip_up(mark)
        todo = failed + [n for n in todo if n not in failed]
    else:
        raise SystemExit("nets with a missing segment: " + ", ".join(failed))
    print(f"  routed {', '.join(todo)} in {attempt} attempt(s); GND by the pour", flush=True)

    silk = (
        # J5: the unfused C8 lead on pin 1, the fused lead on pin 2.
        (11.6, 31.6, "C8"),
        (11.6, 33.3, "120 V AC"),
        (11.6, 35.0, "no earth"),
        (11.6, 36.68, "FUSE"),
        # 1.0 mm legend beside the tab, and the fan lead colors beside
        # the J2 pin each one lands on.
        (80, 2.3, "insulate"),
        (80, 4.2, "tab = drain"),
        (84.6, 40.0, "BLK"),
        (84.6, 42.54, "YEL"),
        (84.6, 45.08, "GRN"),
        (84.6, 47.62, "BLU"),
    )
    ref_at = {
        "PS1": (30.0, 30.6),
        "Q1": (80.0, 12.0),
        # Off the mains legend, off the board edge, and off each other.
        "J5": (15.6, 38.6),
        "J2": (92.0, 50.6),
        "R9": (72.54, 49.3),
        "R7": (75.7, 49.3),
        "R8": (80.16, 49.3),
        "R1": (72.6, 38.9),
        "R4": (63.2, 15.9),
        "R5": (40.6, 39.95),
        # Inside the Pico outline: visible until the module goes in.
        "U2": (36.0, 53.0),
    }
    for ref, (x, y) in ref_at.items():
        fps[ref].Reference().SetPosition(pcbnew.VECTOR2I_MM(x, y))
    for x, y, s in silk:
        txt = pcbnew.PCB_TEXT(board)
        txt.SetText(s)
        txt.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        txt.SetLayer(pcbnew.F_SilkS)
        txt.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.0), pcbnew.FromMM(1.0)))
        txt.SetTextThickness(pcbnew.FromMM(0.15))
        txt.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
        txt.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        board.Add(txt)

    tb = board.GetTitleBlock()
    tb.SetTitle("Smartfan V2")
    tb.SetDate(DATE)
    tb.SetComment(0, "96 x 70 mm, Hammond 1590BB. Holes are not the lid bosses.")
    tb.SetComment(1, "Do not bond 0 V or the MOSFET tab to the box. 120 V keeps 6.4 mm.")

    # Shift after routing, which is in board coordinates, and before the
    # pour is filled so the fill lands on the moved pads.
    delta = pcbnew.VECTOR2I(pcbnew.FromMM(PAGE_OX), pcbnew.FromMM(PAGE_OY))
    for fp in list(board.GetFootprints()):
        fp.Move(delta)
    for track in list(board.GetTracks()):
        track.Move(delta)
    for drawing in list(board.GetDrawings()):
        drawing.Move(delta)
    for zone in list(board.Zones()):
        zone.Move(delta)

    # Gerbers and the drill file use this origin. Plot Y grows up and the
    # board file grows down, so the origin is the lower-left corner. The
    # upper-left corner puts the whole board in negative Y.
    # Set it before the fill: replacing the design settings afterwards
    # drops the poured copper.
    settings = board.GetDesignSettings()
    settings.SetAuxOrigin(pcbnew.VECTOR2I_MM(PAGE_OX, PAGE_OY + BOARD_H))
    board.SetDesignSettings(settings)

    # A pour filled on this fresh board comes back empty. Saving and loading
    # builds the board outline, and the filler then keeps the copper.
    pcbnew.SaveBoard(pcb_path, board)
    board = pcbnew.LoadBoard(pcb_path)
    zone = next(z for z in board.Zones() if not z.GetIsRuleArea())
    board.BuildConnectivity()
    filler = pcbnew.ZONE_FILLER(board)
    filled = filler.Fill(board.Zones())
    # GetFilledArea is in square internal units. 1 mm is 1e6 IU.
    area = zone.GetFilledArea() / 1e12
    print(f"  zone fill {'ok' if filled else 'FAILED'} {area:.0f} mm2", flush=True)
    if not filled or area < 1000:
        raise SystemExit("ground pour did not fill")

    pcbnew.SaveBoard(pcb_path, board)
    print(f"  board {pcb_path}")


def write_rules() -> None:
    """DRC rule: 120 V copper keeps MAINS_GAP from everything else.

    KiCad applies it to tracks, pads, the pour, and pads with no net. It
    checks the copper on each layer; generate.py also keeps the distance
    between layers, which DRC does not.
    """
    def is_ac(item):
        return "(" + " || ".join(f"{item}.NetName == '{net_name(n)}'" for n in AC_NETS) + ")"

    rules = f"""(version 1)

# Written by generate.py. Do not edit; change MAINS_GAP there.
(rule "mains clearance"
\t(condition "({is_ac('A')} && !{is_ac('B')}) || ({is_ac('B')} && !{is_ac('A')})")
\t(constraint clearance (min {MAINS_GAP}mm))
\t(constraint creepage (min {MAINS_GAP}mm)))
"""
    (ROOT / f"{PROJECT}.kicad_dru").write_text(rules, encoding="utf-8")


def sexpr(text: str):
    stack = [[]]
    for tok in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        elif tok.startswith('"'):
            stack[-1].append(tok[1:-1])
        else:
            stack[-1].append(tok)
    return stack[0][0]


def cli(*args) -> subprocess.CompletedProcess:
    res = subprocess.run([str(CLI), *map(str, args)], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"kicad-cli {' '.join(map(str, args[:3]))} failed:\n{res.stdout}\n{res.stderr}")
    return res


def verify_schematic() -> None:
    """The netlist KiCad reads from the drawing must be PARTS, pin for pin.

    Off-board parts drop out of a board netlist, so this exports a copy of
    the sheet with every part marked on the board.
    """
    sch = ROOT / f"{PROJECT}.kicad_sch"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / sch.name
        copy.write_text(
            sch.read_text(encoding="utf-8").replace("(on_board no)", "(on_board yes)"),
            encoding="utf-8",
        )
        out = Path(tmp) / "net.net"
        cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, copy)
        tree = sexpr(out.read_text(encoding="utf-8"))
    got = {}
    for item in tree:
        if isinstance(item, list) and item and item[0] == "nets":
            for net in item[1:]:
                name = next(x[1] for x in net if isinstance(x, list) and x[0] == "name")
                nodes = set()
                for x in net:
                    if isinstance(x, list) and x[0] == "node":
                        ref = next(y[1] for y in x if isinstance(y, list) and y[0] == "ref")
                        pin = next(y[1] for y in x if isinstance(y, list) and y[0] == "pin")
                        if not ref.startswith("#"):
                            nodes.add((ref, pin))
                got[name] = nodes
    want = {}
    for ref, _key, _value, _at, _rot, _fp, _on_board, netmap in PARTS:
        for pin, net in netmap.items():
            want.setdefault(net_name(net), set()).add((ref, pin))
    bad = []
    for name, pins in sorted(want.items()):
        if got.get(name) != pins:
            bad.append(f"{name}: drawn {sorted(got.get(name, ()))}, PARTS {sorted(pins)}")
    for name, pins in sorted(got.items()):
        if name not in want and not name.startswith("unconnected-"):
            bad.append(f"{name}: drawn {sorted(pins)}, not in PARTS")
    if bad:
        raise SystemExit("schematic does not match PARTS:\n  " + "\n  ".join(bad))
    print(f"  schematic netlist matches PARTS, {len(want)} nets", flush=True)


def check_and_export() -> None:
    sch = ROOT / f"{PROJECT}.kicad_sch"
    pcb = ROOT / f"{PROJECT}.kicad_pcb"
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "erc.json"
        cli("sch", "erc", "--severity-all", "--format", "json", "-o", report, sch)
        found = [v for sheet in json.loads(report.read_text(encoding="utf-8")).get("sheets", [])
                 for v in sheet.get("violations", [])]
        # The embedded symbols are renamed copies of the library ones.
        serious = [v for v in found if v["type"] != "lib_symbol_mismatch"]
        print(f"  ERC: {len(found) - len(serious)} lib_symbol_mismatch warnings, "
              f"{len(serious)} other", flush=True)
        if serious:
            raise SystemExit("ERC:\n  " + "\n  ".join(
                f'{v["type"]}: {v["description"]}' for v in serious))

        report = Path(tmp) / "drc.json"
        cli("pcb", "drc", "--schematic-parity", "--severity-all", "--format", "json",
            "-o", report, pcb)
        data = json.loads(report.read_text(encoding="utf-8"))
        found = (data.get("violations", []) + data.get("unconnected_items", [])
                 + data.get("schematic_parity", []))
        print(f"  DRC with schematic parity: {len(found)} findings", flush=True)
        if found:
            raise SystemExit("DRC:\n  " + "\n  ".join(
                f'{v["severity"]} {v["type"]}: {v["description"]} '
                f'{[i.get("description") for i in v.get("items", [])]}'
                for v in found[:30]))

    fab = ROOT / "fab"
    fab.mkdir(exist_ok=True)
    for old in fab.glob(f"{PROJECT}*"):
        old.unlink()
    cli("pcb", "export", "gerbers", "--layers",
        "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
        "--use-drill-file-origin", "-o", f"{fab}{os.sep}", pcb)
    cli("pcb", "export", "drill", "--drill-origin", "plot", "--excellon-units", "mm",
        "--excellon-separate-th", "--generate-map", "--map-format", "pdf",
        "-o", f"{fab}{os.sep}", pcb)
    cli("sch", "export", "pdf", "-o", ROOT / f"{PROJECT}-schematic.pdf", sch)
    for side, layers in (("front", "F.Cu,F.Silkscreen,Edge.Cuts"), ("back", "B.Cu,Edge.Cuts")):
        cli("pcb", "export", "svg", "--layers", layers, "--mode-single",
            "--page-size-mode", "2", "--exclude-drawing-sheet",
            "-o", ROOT / f"{PROJECT}-{side}.svg", pcb)
    print(f"  fab/ {len(list(fab.glob(f'{PROJECT}*')))} files, schematic PDF, front and back SVG")


def main() -> None:
    sym = load_symbols()
    built = build_schematic(sym)
    sch_path = ROOT / f"{PROJECT}.kicad_sch"
    sch_path.write_text(built["text"], encoding="utf-8")
    print(f"  schematic {sch_path}")
    write_rules()
    if CLI.exists():
        verify_schematic()
    build_board(built["uuids"], sym)
    if CLI.exists():
        check_and_export()
    else:
        print("  kicad-cli not found: no ERC, DRC or fab outputs")


if __name__ == "__main__":
    main()

"""
build_sld.py

Generates an IOM-style medium-voltage switchgear Single Line Diagram from
tabular unit data, composing each cubicle from a shared symbol library rather
than from a hand-drawn block per unit type.

Three inputs drive the drawing:

  sld_config.json    geometry (elevations, offsets, cubicle pitch) and the
                     archetype definitions -- which device roles each unit type
                     is built from
  units.csv          one row per cubicle: tag, archetype, ratings, relay,
                     destination, per-device overrides such as CT ratio
  symbol_library.dxf the real AutoCAD Electrical blocks, plus red placeholder
                     stubs for symbols not yet available

Layout follows the reference drawings 8508+A01-000-053/054: devices sit at
fixed sheet elevations shared by every unit, so a lineup reads straight across
regardless of what each cubicle contains. Units missing a device simply leave
that elevation empty and the conductor runs through.

Usage:
    python build_sld.py units.csv output.dxf
    python build_sld.py units.csv bus_a.dxf --bus A
"""

import argparse
import csv
import json
import re
import sys

import ezdxf
from ezdxf.addons import Importer

import stacker

CONFIG_PATH = "sld_config.json"
LIBRARY_PATH = "symbol_library.dxf"

LAYERS = [
    ("SLD_BUS", 5, 50),      # main bus -- heavier lineweight
    ("SLD_WIRE", 7, 25),     # power conductors
    ("SLD_SYMS", 7, 25),     # device symbols
    ("SLD_CUBICLE", 8, 13),  # cubicle division outlines
    ("SLD_TEXT", 7, 13),     # annotation
    ("SLD_CONTROL", 7, 13),  # CT secondary / control wiring
]


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------

def load_config(path=CONFIG_PATH, library_path=LIBRARY_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # An archetype may declare a device stack instead of measured elevations;
    # expanding it here means the rest of the engine only ever sees concrete
    # coordinates. Configs without stacks come back unchanged.
    return stacker.expand(cfg, library_path)


def load_units(path, bus=None, cfg=None):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("unit", "").strip()]
    if bus:
        rows = [r for r in rows if r.get("bus", "").strip().upper() == bus.upper()]
    rows = sorted(rows, key=lambda r: int(r["unit"]))
    if cfg:
        apply_decks(rows, cfg)
    assign_cubicles(rows)
    return rows


def apply_decks(rows, cfg):
    """
    Fold the `deck` column into the archetype name.

    A two-high cubicle draws the same feeder at two different elevations, which
    the engine expresses as two archetypes -- 8513 spells them feeder_upper and
    feeder_lower. Writing that distinction into every CSV row means repeating
    the deck in the archetype name, so a lineup may instead name one archetype
    and put the deck in its own column.

    The variant only has to exist where it matters: `deck` of 'single' on a
    lineup whose bus sits at mid height means the unit occupies the lower half
    alone, so it resolves to the same geometry as a lower deck. A deck naming
    no variant falls through to the plain archetype rather than failing, which
    keeps the column optional for single-high standards.
    """
    known = cfg.get("archetypes", {})
    for row in rows:
        deck = (row.get("deck") or "").strip().lower()
        if not deck:
            continue
        base = (row.get("archetype") or "").strip()
        for candidate in (f"{base}_{deck}", base):
            if candidate in known:
                row["archetype"] = candidate
                break
    return rows


def assign_cubicles(rows):
    """
    Give every row the index of the cubicle it is drawn in.

    One row is one *breaker*, which is not the same as one cubicle: a two-high
    lineup stacks two breakers in a single cubicle, so those rows share an x
    position and differ only in elevation. The optional `cubicle` column says
    which cubicle a breaker sits in; without it each breaker gets its own, which
    is the single-high case and how every lineup behaved before the column
    existed.

    Numbering is rebased per sheet, because a bus-B-only build starts at the
    first bus-B cubicle and still has to draw from the left edge of its sheet.
    """
    declared = [r.get("cubicle", "").strip() for r in rows]
    if not all(d for d in declared):
        # Without an explicit column the unit number is the cubicle: a lineup
        # that stacks two breakers gives both rows the same unit and tells them
        # apart by deck. Where units are unique this is the single-high case
        # and comes out one cubicle per row, as it always did.
        declared = [r["unit"].strip() for r in rows]
    order = sorted({int(d) for d in declared})
    rank = {c: i for i, c in enumerate(order)}
    for row, d in zip(rows, declared):
        row["_cubicle"] = rank[int(d)]
    return rows


def import_symbols(doc, library_path=LIBRARY_PATH, needed=None):
    """Pull only the blocks this drawing actually uses out of the library."""
    src = ezdxf.readfile(library_path)
    available = {b.name for b in src.blocks if not b.name.startswith("*")}
    wanted = sorted(available if needed is None else (set(needed) & available))
    missing = sorted(set(needed or []) - available)

    importer = Importer(src, doc)
    for name in wanted:
        importer.import_block(name)
    importer.finalize()
    return wanted, missing


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def centreline_x(cfg, index):
    """X of the power centreline for the index-th cubicle in the lineup."""
    sheet = cfg["sheet"]
    return (sheet["first_cubicle_x"]
            + index * sheet["pitch"]
            + sheet["centreline_from_left"])


def resolve_roles(cfg, archetype):
    """
    Expand an archetype into its ordered list of (name, role-spec) pairs.

    An archetype may carry a "role_overrides" map -- {role_name: {field: value}}
    -- to tweak a shared role (e.g. flip an "outgoing" terminal's rotation)
    without cloning it. The base role in cfg["roles"] is never mutated, so
    every other archetype using that role is unaffected; omitting
    role_overrides entirely reproduces the old behaviour exactly.
    """
    if archetype not in cfg["archetypes"]:
        raise KeyError(f"unknown archetype {archetype!r}")
    overrides = cfg["archetypes"][archetype].get("role_overrides", {})
    out = []
    for name in cfg["archetypes"][archetype]["roles"]:
        if name not in cfg["roles"]:
            raise KeyError(f"archetype {archetype!r} references unknown role {name!r}")
        spec = cfg["roles"][name]
        if name in overrides:
            spec = {**spec, **overrides[name]}
        out.append((name, spec))
    return out


def conductor_gaps(roles):
    """
    Vertical spans the main conductor must break for, taken from series
    devices. Shunt devices and CTs are excluded -- a CT encircles the
    conductor rather than interrupting it, which is why the reference
    drawings run an unbroken wire past both CTs.
    """
    gaps = []
    for _, spec in roles:
        if spec.get("series") and "gap" in spec:
            top, bottom = spec["gap"]
            gaps.append((max(top, bottom), min(top, bottom)))
    return sorted(gaps, key=lambda g: -g[0])


def draw_conductor(msp, x, y_from, y_to, gaps):
    """
    Vertical conductor from y_from down to y_to, broken around each gap.

    Returns the segments drawn as (x, y_bottom, y_top) so the control wiring
    knows where it has to hop.
    """
    segments = []
    y = y_from
    for top, bottom in gaps:
        if top >= y_to and top <= y:
            if y - top > 1e-6:
                msp.add_line((x, y), (x, top), dxfattribs={"layer": "SLD_WIRE"})
                segments.append((x, top, y))
            y = min(y, bottom)
    if y - y_to > 1e-6:
        msp.add_line((x, y), (x, y_to), dxfattribs={"layer": "SLD_WIRE"})
        segments.append((x, y_to, y))
    return segments


# --------------------------------------------------------------------------
# device placement
# --------------------------------------------------------------------------

def resolve_attribs(spec, unit):
    """
    Build the ATTDEF values for a role from its 'attribs' map: each entry is
    an ATTDEF tag pointing at a units.csv column, or at a literal when the
    value starts with '@'. Roles without an 'attribs' map get nothing -- the
    reference drawings give connectors and fuses their own ACADE designations,
    so stamping the breaker tag onto them would be wrong.

    A source may list alternatives separated by '|', taking the first that
    resolves to something: "ct_class|@CT 5P20" prints the job's own accuracy
    class where the sheet states one and the house default where it does not.
    That is what keeps a per-job value out of the drafting standard without
    forcing every job to restate it.
    """
    out = {}
    for tag, source in (spec.get("attribs") or {}).items():
        value = ""
        for part in source.split("|"):
            value = part[1:] if part.startswith("@") else unit.get(part, "")
            if value:
                break
        if value:
            out[tag] = value
    return out


ATTRIB_INVISIBLE = 1


def stamp_attribs(ref, values, positions=None):
    """
    Fill a block reference's attributes, then blank and hide every attribute we
    did not supply a value for.

    ACADE symbols carry ~17 ATTDEFs each, most of them bookkeeping (FAMILY,
    ASSYCODE, TERM01, WDTYPE...). ezdxf's add_auto_attribs seeds any attribute
    it isn't given with the ATTDEF's *default* text, which plots as stray
    labels -- an unset TAG1 on VXF1CT comes out reading 'XF'. The reference
    drawings set the same attributes invisible, so this reproduces how they
    actually plot.

    `positions` moves individual labels relative to the insertion point. Where
    a symbol's text sits is a property of the drawing standard rather than of
    the block: ACADE lets a drafter drag it, and the two reference sets
    disagree -- 8508 leaves the CT ratio below the CT at (-0.201, -0.757),
    which is the ATTDEF default, while 8513 pulls it out to the left at
    (-1.261, -0.017) to clear the conductor. Taking the default in both places
    lands 8513's ratios on top of its own geometry.
    """
    ref.add_auto_attribs(values)
    for attrib in ref.attribs:
        if attrib.dxf.tag not in values:
            attrib.dxf.text = ""
            attrib.dxf.flags |= ATTRIB_INVISIBLE
            continue
        rel = (positions or {}).get(attrib.dxf.tag)
        if rel:
            point = (ref.dxf.insert[0] + rel[0], ref.dxf.insert[1] + rel[1])
            attrib.dxf.insert = point
            # Justified text is placed by its alignment point, and leaving that
            # behind at the block origin drags the label back.
            if attrib.dxf.hasattr("align_point"):
                attrib.dxf.align_point = point


def place_block(msp, spec, x, unit):
    attribs = {"layer": "SLD_SYMS", "rotation": spec.get("rotation", 0)}
    # Optional uniform scale (e.g. to make the bus PT read more clearly at a
    # glance). Omitted for every role that doesn't set it, so this reproduces
    # the old 1:1 insert exactly when unused.
    scale = spec.get("scale")
    if scale:
        attribs["xscale"] = attribs["yscale"] = attribs["zscale"] = scale
    ref = msp.add_blockref(
        spec["block"], (x + spec.get("dx", 0.0), spec["y"]),
        dxfattribs=attribs,
    )
    stamp_attribs(ref, resolve_attribs(spec, unit), spec.get("attrib_pos"))
    # CT polarity mark: a dot on the primary conductor showing winding sense.
    dot = spec.get("polarity_dot")
    if dot:
        msp.add_blockref("WDDOT", (x + dot["dx"], spec["y"] + dot["dy"]),
                         dxfattribs={"layer": "SLD_SYMS"})
    return ref


def place_note(msp, spec, x, unit):
    """
    Free annotation tied to a device rather than to a sheet-wide text style --
    the '1%%C' phase marks beside each CT, the Kirk key interlock note, the
    'NORMALLY CLOSED' caption on the tie breaker.
    """
    source = spec.get("text", "")
    text = source[1:] if source.startswith("@") else unit.get(source, "")
    return add_mtext(msp, text, x, spec)


def place_lead(msp, spec, x):
    """An explicit run of segments -- leader lines, tie-outs, panel drops."""
    pts = [(x + dx, y) for dx, y in spec["points"]]
    for a, b in zip(pts, pts[1:]):
        msp.add_line(a, b, dxfattribs={"layer": spec.get("layer", "SLD_WIRE")})


def place_circle(msp, spec, x):
    msp.add_circle((x + spec.get("dx", 0.0), spec["y"]), radius=spec["radius"],
                   dxfattribs={"layer": spec.get("layer", "SLD_SYMS")})


def place_box(msp, spec, x):
    """Compartment / label outline -- relay box, CT compartment."""
    x0 = x + spec["dx"]
    x1 = x0 + spec["width"]
    attribs = {"layer": spec.get("layer", "SLD_CUBICLE")}
    if spec.get("linetype"):
        attribs["linetype"] = spec["linetype"]
    msp.add_lwpolyline(
        [(x0, spec["y_top"]), (x1, spec["y_top"]),
         (x1, spec["y_bottom"]), (x0, spec["y_bottom"])],
        close=True, dxfattribs=attribs,
    )


def place_breaker(msp, spec, x):
    """
    Drawout breaker body. In the reference drawings this is a plain rectangle
    between the two VCN1PJ primary disconnects, not a block of its own.
    """
    x0 = x + spec["dx"]
    x1 = x0 + spec["width"]
    msp.add_lwpolyline(
        [(x0, spec["y_top"]), (x1, spec["y_top"]),
         (x1, spec["y_bottom"]), (x0, spec["y_bottom"])],
        close=True, dxfattribs={"layer": "SLD_SYMS"},
    )


RELAY_RE = re.compile(r"^\s*([^(]+?)\s*(?:\(([^)]*)\))?\s*$")


def place_relay_functions(msp, spec, x, unit):
    """
    The relay panel: an outlined box carrying the device name and one small
    circle per ANSI function it performs.

    The 8372 sheets draw a relay as 'SEL-751' with 25, 50P, 51P, 50G and 51G
    each circled in a grid, which is exactly how the units.csv relay cell is
    already written -- 'SEL-751 (25, 50P, 51P, 50G, 51G)'. So the cell is
    parsed rather than lettered verbatim: the part before the bracket is the
    device, the comma-separated list inside it is the functions. A cell with no
    bracket simply draws the box and the name, which is what the 8508 and 8513
    standards do with their own relay labels.

    The grid fills left to right, wrapping after `per_row`, so a relay with
    three functions and one with six both come out tidy without the config
    having to know which units carry which.
    """
    source = spec.get("text", "relay")
    raw = source[1:] if source.startswith("@") else (unit.get(source) or "")
    match = RELAY_RE.match(" ".join(raw.split()))
    if not match:
        return
    device, inside = match.group(1), match.group(2) or ""
    functions = [f.strip() for f in inside.split(",") if f.strip()]

    x0 = x + spec["dx"]
    msp.add_lwpolyline(
        [(x0, spec["y_top"]), (x0 + spec["width"], spec["y_top"]),
         (x0 + spec["width"], spec["y_bottom"]), (x0, spec["y_bottom"])],
        close=True, dxfattribs={"layer": spec.get("layer", "SLD_SYMS")})

    name = dict(spec.get("name_style") or {})
    name.setdefault("height", 0.125)
    name["y"] = spec["y_bottom"] + name.pop("dy", 0.108)
    add_mtext(msp, device, x0 + name.pop("dx", 0.089), name)

    radius = spec.get("radius", 0.138)
    per_row = spec.get("per_row", 3)
    step_x, step_y = spec.get("step_x", 0.372), spec.get("step_y", 0.370)
    fx, fy = spec.get("first_dx", 1.356), spec.get("first_dy", -0.166)
    for i, code in enumerate(functions):
        cx = x0 + fx + step_x * (i % per_row)
        cy = spec["y_top"] + fy - step_y * (i // per_row)
        msp.add_circle((cx, cy), radius=radius,
                       dxfattribs={"layer": spec.get("layer", "SLD_SYMS")})
        add_mtext(msp, code, cx, {"y": cy + spec.get("label_dy", 0.051),
                                  "height": spec.get("label_height", 0.093),
                                  "align": "centre", "centre_mode": "paragraph"})


def place_bubble(msp, spec, x, label):
    """
    ANSI device bubble, plus its connecting leads.

    The attribute holding the function number is named by the role's
    'attrib_tag' -- the drafter's DEVICE_BUBBLE block calls it 'DB', not the
    ACADE-standard 'TAG1'.

    Leads are derived from the bubble's own centre and radius rather than being
    fixed offsets, so moving or resizing the bubble keeps them attached. Fixed
    offsets left a visible gap once the bubble block changed radius.
    """
    cx, cy = x + spec["dx"], spec["y"]
    ref = msp.add_blockref(spec["block"], (cx, cy), dxfattribs={"layer": "SLD_SYMS"})
    stamp_attribs(ref, {spec.get("attrib_tag", "TAG1"): label or spec.get("label", "")})

    leads = spec.get("leads") or {}
    radius = spec.get("radius", 0.0)
    if "left_to_dx" in leads:
        msp.add_line((x + leads["left_to_dx"], cy), (cx - radius, cy),
                     dxfattribs={"layer": "SLD_CONTROL"})
    # A measured config knows the absolute elevation to drop to; a stacked one
    # does not, because the thing being reached for was itself computed. So the
    # drop may instead be given as a distance from the bubble's own centre.
    down_to = leads.get("down_to_y")
    if down_to is None and "down_dy" in leads:
        down_to = cy + leads["down_dy"]
    if down_to is not None:
        msp.add_line((cx, cy - radius), (cx, down_to),
                     dxfattribs={"layer": "SLD_CONTROL"})
    # The tie unit carries its relay box above the 86 rather than below it, so
    # the drop to the relay runs the other way.
    if "up_to_y" in leads:
        msp.add_line((cx, cy + radius), (cx, leads["up_to_y"]),
                     dxfattribs={"layer": "SLD_CONTROL"})
    return ref


# --------------------------------------------------------------------------
# control wiring
# --------------------------------------------------------------------------

def connects_to_bus(cfg, archetype, bus_y, bus_name=None, tol=1e-6):
    """
    Whether a unit actually lands on a given control bus -- true only if its
    wiring has a riser terminating at, or a spur running along, that elevation.

    Deriving this from the wiring rather than from role names matters: the XFMR
    feeder and the tie unit both carry metering CTs, but neither joins the
    feeder metering bus. Keying off "has a ct_metering role" would run that bus
    the full width of the lineup instead of stopping at unit 7, as the
    reference does.
    """
    wiring = cfg["archetypes"].get(archetype, {}).get("control_wiring")
    if not wiring:
        return False
    # The PT riser feeds the control-power bus through its *power* conductor,
    # which lands on the bus instead of ending on a connector -- no riser to
    # infer from, so that archetype names the bus outright.
    if bus_name and bus_name in (wiring.get("joins") or []):
        return True
    for riser in wiring.get("risers", []):
        if any(abs(riser[k] - bus_y) < tol for k in ("y_from", "y_to")):
            return True
    return any(abs(spur["y"] - bus_y) < tol for spur in wiring.get("spurs", []))


def bus_dx_override(cfg, archetype, bus_name, default):
    """
    Where a given archetype meets a named bus, when it differs from the bus's
    own default. The PT riser takes the metering bus into the left edge of its
    relay box, not at the riser offset every feeder uses.
    """
    wiring = cfg["archetypes"].get(archetype, {}).get("control_wiring") or {}
    return (wiring.get("bus_dx") or {}).get(bus_name, default)


def bus_extents(cfg, units):
    """
    Horizontal extent of each declared control bus.

    A bus spans only the units that actually feed it, which is why the
    reference metering bus stops at x~20.8 -- the XFMR feeder and PT unit past
    that point have no metering CT to connect. Extents are derived from which
    units carry a participating role rather than being fixed in config, so
    reordering or removing units keeps the buses correct.

    A bus needs two participants to mean anything, so one is dropped: the
    bus-differential run between the PT riser and the tie has no tie to reach
    on a lineup that does not contain one. Testing the participants rather than
    the resulting endpoints matters, because a bus whose start_dx and end_dx
    differ -- which is exactly the bus-differential run -- still spans a
    plausible-looking 1.267 with only one unit on it, and would otherwise be
    drawn as a stub ending in mid-air.
    """
    out = []
    for bus in cfg.get("control", {}).get("buses", []):
        name = bus.get("name", "")
        indices = [i for i, u in enumerate(units)
                   if connects_to_bus(cfg, u["archetype"], bus["y"], name)]
        if len(indices) < 2:
            continue
        first, last = units[indices[0]], units[indices[-1]]
        x0 = centreline_x(cfg, first["_cubicle"]) + bus_dx_override(
            cfg, first["archetype"], name, bus.get("start_dx", 0.0))
        x1 = centreline_x(cfg, last["_cubicle"]) + bus_dx_override(
            cfg, last["archetype"], name, bus.get("end_dx", 0.0))
        if abs(x1 - x0) < 1e-6:
            continue
        out.append({"name": name, "y": bus["y"],
                    "x0": min(x0, x1), "x1": max(x0, x1)})
    return out


def draw_horizontal(msp, y, x0, x1, crossings, hop_radius, layer):
    """
    A horizontal control run, hopped over every vertical it crosses.

    These arcs run 0deg->180deg, bulging in +y, so the *horizontal* detours
    around the *vertical* -- the opposite sense to draw_riser. Both appear in
    the reference: a control bus hops over the power conductor it passes, while
    a riser hops over a bus it merely crosses.
    """
    xs = sorted(cx for cx in crossings if x0 + 1e-6 < cx < x1 - 1e-6)
    cursor = x0
    for cx in xs:
        if cx - hop_radius > cursor + 1e-6:
            msp.add_line((cursor, y), (cx - hop_radius, y), dxfattribs={"layer": layer})
        msp.add_arc(center=(cx, y), radius=hop_radius,
                    start_angle=0, end_angle=180, dxfattribs={"layer": layer})
        cursor = max(cursor, cx + hop_radius)
    if x1 > cursor + 1e-6:
        msp.add_line((cursor, y), (x1, y), dxfattribs={"layer": layer})


def collect_bus_gaps(cfg, units, bus_key):
    """
    Absolute x ranges where a named bus must break for series devices.

    A tie breaker sits *in* the bus rather than hanging off it, so the run stops
    either side of it and the archetype's own roles draw what fills the space --
    connectors, leads and the breaker box. The control bus breaks the same way
    for the tie's pair of horizontal fuses.
    """
    out = []
    for unit in units:
        gaps = (cfg["archetypes"].get(unit["archetype"], {}).get("bus_gaps") or {})
        ux = centreline_x(cfg, unit["_cubicle"])
        for lo, hi in gaps.get(bus_key, []):
            out.append((ux + min(lo, hi), ux + max(lo, hi)))
    return out


def draw_horizontal_run(msp, y, x0, x1, crossings, gaps, hop_radius, layer):
    """A horizontal run broken for series devices, each surviving piece still
    hopping over whatever verticals cross it."""
    pieces = [(x0, x1)]
    for lo, hi in sorted(gaps):
        split = []
        for a, b in pieces:
            if hi <= a or lo >= b:
                split.append((a, b))
                continue
            if a < lo:
                split.append((a, lo))
            if hi < b:
                split.append((hi, b))
        pieces = split
    for a, b in pieces:
        if b - a > 1e-6:
            draw_horizontal(msp, y, a, b, crossings, hop_radius, layer)


def draw_riser(msp, rx, riser, buses, hop_radius):
    """
    One vertical control riser, broken for any device it passes through and
    hopped over every bus that crosses it.

    Hop direction follows the reference: the arcs run 270deg->90deg, bulging in
    +x, so the *vertical* riser detours around the *horizontal* bus. Getting
    this backwards is the thing that reads as wrong to a drafter.
    """
    y_low, y_high = sorted((riser["y_from"], riser["y_to"]))

    breaks = [(min(g), max(g), None) for g in riser.get("gaps", [])]
    for bus in buses:
        crosses = y_low < bus["y"] < y_high and bus["x0"] - 1e-6 <= rx <= bus["x1"] + 1e-6
        if crosses:
            breaks.append((bus["y"] - hop_radius, bus["y"] + hop_radius, bus["y"]))
    breaks.sort()

    cursor = y_low
    for lo, hi, hop_y in breaks:
        if lo > cursor + 1e-6:
            msp.add_line((rx, cursor), (rx, lo), dxfattribs={"layer": "SLD_CONTROL"})
        if hop_y is not None:
            msp.add_arc(center=(rx, hop_y), radius=hop_radius,
                        start_angle=270, end_angle=90,
                        dxfattribs={"layer": "SLD_CONTROL"})
        cursor = max(cursor, hi)
    if y_high > cursor + 1e-6:
        msp.add_line((rx, cursor), (rx, y_high), dxfattribs={"layer": "SLD_CONTROL"})


def draw_control_wiring(msp, cfg, units, buses, conductors):
    """
    Per-unit risers and spurs, then the lineup-wide buses on top.

    'conductors' is every main power conductor segment drawn, as
    (x, y_bottom, y_top). Control runs never break a power conductor -- the
    horizontal hops over it instead, which is what the reference does at every
    cubicle the control-power bus passes through.
    """
    hop_radius = cfg.get("control", {}).get("hop_radius", 0.05)
    dotted = set()

    ends_take_dots = cfg["sheet"].get("bus_dot_at_ends", True)
    drawn_bus_ys = {round(b["y"], 4) for b in buses}
    dropped_bus_ys = {round(b["y"], 4)
                      for b in cfg.get("control", {}).get("buses", [])
                      if round(b["y"], 4) not in drawn_bus_ys}

    def riser_is_orphaned(riser):
        # A riser exists to reach a bus, so one whose end lands on a declared
        # bus that this lineup dropped has nothing to connect to. Risers that
        # end nowhere in particular are left alone -- the feeder CT risers stop
        # below the cubicle at an elevation no bus declares.
        return any(round(riser[end], 4) in dropped_bus_ys
                   for end in ("y_from", "y_to"))

    def conductor_crossings(y, x0, x1):
        # Strict inequality: a conductor that *terminates* on this elevation
        # (the PT riser landing on the control bus) is a connection, not a
        # crossing, and gets a junction dot rather than a hop.
        return [cx for cx, lo, hi in conductors
                if lo + 1e-6 < y < hi - 1e-6 and x0 - 1e-6 <= cx <= x1 + 1e-6]

    for unit in units:
        wiring = (cfg["archetypes"].get(unit["archetype"], {})
                  .get("control_wiring"))
        if not wiring:
            continue
        x = centreline_x(cfg, unit["_cubicle"])
        risers = wiring.get("risers", [])

        for spur in wiring.get("spurs", []):
            sx0, sx1 = sorted((x + spur["dx_from"], x + spur["dx_to"]))
            crossings = conductor_crossings(spur["y"], sx0, sx1)
            # Which way a spur/riser intersection resolves is drawing
            # convention, not geometry, so the spur declares it. Default is
            # that the riser hops (the CT secondary runs under its own risers);
            # the tie unit's mid-CT secondary is the other way round.
            if spur.get("priority") == "horizontal":
                crossings += [x + r["dx"] for r in risers
                              if min(r["y_from"], r["y_to"]) < spur["y"]
                              < max(r["y_from"], r["y_to"])]
            draw_horizontal(msp, spur["y"], sx0, sx1, crossings,
                            hop_radius, "SLD_CONTROL")

        for riser in wiring.get("risers", []):
            if riser_is_orphaned(riser):
                continue
            rx = x + riser["dx"]
            local = [{"y": s["y"],
                      "x0": min(x + s["dx_from"], x + s["dx_to"]),
                      "x1": max(x + s["dx_from"], x + s["dx_to"])}
                     for s in wiring.get("spurs", [])
                     if s.get("priority") != "horizontal"]
            draw_riser(msp, rx, riser, buses + local, hop_radius)
            # A riser that ends exactly on a bus is a real connection, so it
            # gets a junction dot; one that merely passes over got an arc. The
            # dot is skipped at the bus's own ends on sheets that draw them
            # bare -- the 8513 bus PT lands on the far left end of the control
            # bus and the reference shows no dot there.
            for end_y in (riser["y_from"], riser["y_to"]):
                for bus in buses:
                    if (not ends_take_dots
                            and min(abs(rx - bus["x0"]), abs(rx - bus["x1"])) < 1e-6):
                        continue
                    if (abs(end_y - bus["y"]) < 1e-6
                            and bus["x0"] - 1e-6 <= rx <= bus["x1"] + 1e-6):
                        key = (round(rx, 4), round(end_y, 4))
                        if key not in dotted:
                            msp.add_blockref("WDDOT", (rx, end_y),
                                             dxfattribs={"layer": "SLD_SYMS"})
                            dotted.add(key)

    for bus in buses:
        draw_horizontal_run(msp, bus["y"], bus["x0"], bus["x1"],
                            conductor_crossings(bus["y"], bus["x0"], bus["x1"]),
                            collect_bus_gaps(cfg, units, bus["name"]),
                            hop_radius, "SLD_CONTROL")

    # A power conductor that stops dead on a control bus is feeding it -- the
    # PT unit is the source of control power, so that junction takes a dot.
    for cx, lo, hi in conductors:
        for bus in buses:
            if (abs(lo - bus["y"]) < 1e-6
                    and bus["x0"] - 1e-6 <= cx <= bus["x1"] + 1e-6):
                key = (round(cx, 4), round(lo, 4))
                if key not in dotted:
                    msp.add_blockref("WDDOT", (cx, lo),
                                     dxfattribs={"layer": "SLD_SYMS"})
                    dotted.add(key)


# --------------------------------------------------------------------------
# annotation
# --------------------------------------------------------------------------

def wrap_text(text, width):
    """
    Break text into MTEXT hard lines (\\P) at word boundaries. The reference
    drawings use MTEXT with no wrap width, so line breaks have to be explicit
    -- 'SEL-751-GEN1A FEEDER PROTECTION RELAY' is stored there already broken.
    """
    if not width or not text:
        return text
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\\P".join(lines)


def add_mtext(msp, text, x, style):
    """
    Place one MTEXT per the reference convention: TOP_LEFT attachment, no wrap
    width (lines break only on explicit \\P), positioned relative to the unit
    centreline by the style's dx.
    """
    # An archetype can suppress a sheet-wide label it has no use for: the PT
    # and transformer units carry no breaker, so the rating spec block that
    # every feeder gets would otherwise float in empty space.
    if not text or style.get("hide"):
        return None
    align = ezdxf.enums.MTextEntityAlignment
    centred = style.get("align", "centre") == "centre"
    body = wrap_text(text, style.get("wrap"))

    # Two ways to centre an MTEXT, and the reference sets disagree about which.
    # The 8508 drawings anchor centred text TOP_CENTER; 8513 anchors everything
    # TOP_LEFT and centres the lines inside it with the inline code \pxqc;.
    # They plot the same but are different objects, and they diverge as soon as
    # anyone edits the text -- so which one is produced is a property of the
    # standard being drawn, declared per text style.
    paragraph = centred and style.get("centre_mode") == "paragraph"
    if paragraph:
        body = "\\pxqc;" + body

    m = msp.add_mtext(body, dxfattribs={"layer": "SLD_TEXT",
                                        "char_height": style["height"]})
    m.dxf.width = 0.0
    m.set_location((x + style.get("dx", 0.0), style["y"]),
                   attachment_point=(align.TOP_CENTER if centred and not paragraph
                                     else align.TOP_LEFT))
    return m


def text_styles(cfg, archetype):
    """
    Sheet-wide text placements, with any per-archetype overrides merged in.

    Most annotation sits at a fixed sheet elevation so labels line up across
    the whole lineup. A few archetypes move the geometry they annotate -- the
    tie unit drops its breaker stack to clear the third CT -- so the label has
    to follow it rather than float at the feeder elevation.
    """
    # Underscore keys are the config's commentary convention and appear at every
    # level, including inside "text" itself -- they are notes, not placements.
    styles = {k: dict(v) for k, v in cfg["text"].items() if not k.startswith("_")}
    overrides = cfg["archetypes"].get(archetype, {}).get("text_overrides", {})
    for name, patch in overrides.items():
        if name.startswith("_"):
            continue
        styles.setdefault(name, {}).update(patch)
    return styles


def annotate_unit(msp, cfg, unit, x):
    t = text_styles(cfg, unit["archetype"])
    add_mtext(msp, f"UNIT {unit['unit']}", x, t["unit_number"])

    header = "\\P".join(p for p in (unit.get("tag"), unit.get("description")) if p)
    add_mtext(msp, header, x, t["unit_tag"])

    # The tie's spec block carries a fifth line stating that it sits normally
    # closed. That is a property of the archetype rather than of the unit data,
    # so it is appended from the text style instead of adding a column that
    # every other row would leave blank.
    spec_parts = [unit.get("tag"), unit.get("voltage"),
                  unit.get("amp_rating"), unit.get("ka_rating"),
                  t["spec_block"].get("suffix")]
    add_mtext(msp, "\\P".join(p for p in spec_parts if p), x, t["spec_block"])

    add_mtext(msp, unit.get("relay"), x, t["relay_label"])
    add_mtext(msp, unit.get("destination"), x, t["destination"])
    # No arrester label here: the ARRESTER block carries its own L.A./MCOV text.
    if "arrester_label" in t:
        add_mtext(msp, unit.get("arrester"), x, t["arrester_label"])


# --------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------

def build(units, cfg, doc):
    msp = doc.modelspace()
    sheet = cfg["sheet"]

    # --- cubicle outlines, uniform pitch --------------------------------
    # One box per cubicle, not per row: on a two-high lineup two breakers share
    # a box, so drawing per row would stack duplicate outlines on top of each
    # other and run the lineup off the right-hand edge.
    for i in sorted({u["_cubicle"] for u in units}):
        x0 = sheet["first_cubicle_x"] + i * sheet["pitch"]
        x1 = x0 + sheet["pitch"]
        msp.add_lwpolyline(
            [(x0, sheet["cubicle_top"]), (x1, sheet["cubicle_top"]),
             (x1, sheet["cubicle_bottom"]), (x0, sheet["cubicle_bottom"])],
            close=True, dxfattribs={"layer": "SLD_CUBICLE"},
        )

    # --- main bus -------------------------------------------------------
    # The bus normally runs a little past the end cubicles, but a lineup that
    # ends on a tie stops dead at the tie centreline -- past that point the
    # conductor belongs to the other bus.
    bus_y = sheet["bus_y"]

    def bus_edge(unit, sign):
        arch = cfg["archetypes"].get(unit["archetype"], {})
        overhang = 0.0 if arch.get("bus_terminates_here") else sheet["bus_extension"]
        return centreline_x(cfg, unit["_cubicle"]) + sign * overhang

    x_start = bus_edge(units[0], -1)
    x_end = bus_edge(units[-1], +1)

    # The main bus hops over any control riser that crosses it. On a lineup
    # with the bus at the top nothing ever does, so this draws a single
    # unbroken line -- but a two-high lineup runs the bus through the middle of
    # the sheet and every CPT riser passes through it on the way down to the
    # control bus below.
    bus_crossings = []
    for unit in units:
        wiring = (cfg["archetypes"].get(unit["archetype"], {})
                  .get("control_wiring"))
        if not wiring:
            continue
        ux = centreline_x(cfg, unit["_cubicle"])
        for riser in wiring.get("risers", []):
            lo, hi = sorted((riser["y_from"], riser["y_to"]))
            if lo + 1e-6 < bus_y < hi - 1e-6:
                bus_crossings.append(ux + riser["dx"])

    draw_horizontal_run(msp, bus_y, x_start, x_end, bus_crossings,
                        collect_bus_gaps(cfg, units, "main"),
                        cfg["control"]["hop_radius"], "SLD_BUS")

    bus_name = (units[0].get("bus") or "").strip() or "A"
    add_mtext(msp, sheet["bus_label"].replace("{bus}", bus_name),
              centreline_x(cfg, units[0]["_cubicle"]), cfg["text"]["bus_label"])

    # --- cubicles -------------------------------------------------------
    warnings = []
    conductors = []
    bus_dots = set()
    for unit in units:
        x = centreline_x(cfg, unit["_cubicle"])
        try:
            roles = resolve_roles(cfg, unit["archetype"])
            # An archetype is a fixed list, but real lineups carry one-off
            # fittings on otherwise identical units -- a Kirk key on two
            # feeders out of seven, an auxiliary transformer on another. The
            # 'extras' column names roles to append, so those stay data instead
            # of multiplying into an archetype per combination.
            for name in (unit.get("extras") or "").replace(",", " ").split():
                if name not in cfg["roles"]:
                    raise KeyError(f"unknown extra role {name!r}")
                roles.append((name, cfg["roles"][name]))
        except KeyError as exc:
            warnings.append(f"unit {unit['unit']}: {exc}")
            continue

        # The bus is always one end of the main conductor; the archetype says
        # where the other end is. Normally that is below, and defaults to the
        # lowest series terminal, though an archetype may pull it explicitly:
        # the tie unit leaves its cubicle sideways at 11.246 rather than ending
        # on a connector.
        #
        # `conductor_top` is the other case. On a two-high lineup the bus runs
        # through the middle of the sheet and the upper deck sits above it, so
        # its run comes *down* from the cable entry to the bus instead of
        # hanging below. draw_conductor always walks downward, so this is a
        # matter of which end it is handed, not of new drawing logic.
        arch = cfg["archetypes"][unit["archetype"]]
        series_ys = [s["gap"][1] for _, s in roles if s.get("series") and "gap" in s]
        y_top = arch.get("conductor_top")
        if y_top is not None:
            span = (y_top, bus_y)
        else:
            y_bottom = arch.get("conductor_bottom")
            if y_bottom is None and series_ys:
                y_bottom = min(series_ys)
            span = (bus_y, y_bottom) if y_bottom is not None else None
        if span is not None:
            conductors += draw_conductor(msp, x, span[0], span[1],
                                         conductor_gaps(roles))
            # One dot per meeting point, not one per breaker. Both decks of a
            # two-high cubicle terminate on the bus at the same x, so stamping
            # it per unit lays two identical dots on top of each other.
            #
            # Whether the end of a bus takes a dot is a drawing convention, not
            # geometry: the 8513 sheets leave the corner bare where the bus
            # stops dead on a main's centreline, while the 8508 tie -- also
            # sitting on its bus end -- carries one. So it is declared per
            # sheet rather than inferred.
            at_bus_end = (not sheet.get("bus_dot_at_ends", True)
                          and min(abs(x - x_start), abs(x - x_end)) < 1e-6)
            key = (round(x, 4), round(bus_y, 4))
            if not at_bus_end and key not in bus_dots:
                msp.add_blockref("WDDOT", (x, bus_y), dxfattribs={"layer": "SLD_SYMS"})
                bus_dots.add(key)

        for name, spec in roles:
            kind = spec["kind"]
            if kind == "breaker":
                place_breaker(msp, spec, x)
            elif kind == "box":
                place_box(msp, spec, x)
            elif kind == "bubble":
                place_bubble(msp, spec, x, spec.get("label"))
            elif kind == "note":
                place_note(msp, spec, x, unit)
            elif kind == "lead":
                place_lead(msp, spec, x)
            elif kind == "circle":
                place_circle(msp, spec, x)
            elif kind == "arc":
                # Transformer windings are drawn as facing half-arcs rather
                # than as a block on the 8372 sheets, so the primitive is
                # exposed instead of forcing a block into the library.
                msp.add_arc((x + spec["dx"], spec["y"]), spec["radius"],
                            spec["start_angle"], spec["end_angle"],
                            dxfattribs={"layer": spec.get("layer", "SLD_SYMS")})
            elif kind == "relay_functions":
                place_relay_functions(msp, spec, x, unit)
            elif kind == "block":
                if spec["block"] not in doc.blocks:
                    warnings.append(
                        f"unit {unit['unit']}: block {spec['block']!r} for role "
                        f"{name!r} not in library -- skipped")
                    continue
                place_block(msp, spec, x, unit)

        annotate_unit(msp, cfg, unit, x)

    draw_control_wiring(msp, cfg, units, bus_extents(cfg, units), conductors)

    return warnings


def main():
    ap = argparse.ArgumentParser(description="Generate a switchgear SLD from unit data.")
    ap.add_argument("units_csv")
    ap.add_argument("output_dxf")
    ap.add_argument("--bus", help="restrict to one bus (e.g. A)")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--library", default=LIBRARY_PATH)
    args = ap.parse_args()

    # --library has to reach load_config too: expanding a device stack measures
    # the real blocks, so a stacking config read without it falls back to the
    # bare relative default and only resolves when the process happens to be run
    # from the directory holding the library. 8478 is the only stacking config,
    # so it was the only lineup that would not build from the repo root.
    cfg = load_config(args.config, args.library)
    units = load_units(args.units_csv, args.bus, cfg)
    if not units:
        print("No units matched -- nothing to draw.")
        sys.exit(1)

    doc = ezdxf.new("R2013", setup=True)
    # Drawing units here are small (a CT is ~0.3 units), so the default global
    # linetype scale of 1.0 stretches a DASHED compartment outline into a few
    # very long segments.
    doc.header["$LTSCALE"] = 0.15
    for name, color, lw in LAYERS:
        doc.layers.add(name, color=color, lineweight=lw)

    # Only the blocks the archetypes in this lineup actually place. Scanning
    # every defined role instead would import the ground switch and 43LR stubs
    # into every drawing and report them as in use when nothing places them.
    used_roles = {name
                  for u in units
                  for name in cfg["archetypes"].get(u["archetype"], {}).get("roles", [])}
    needed = {cfg["roles"][r]["block"] for r in used_roles if "block" in cfg["roles"].get(r, {})}
    # WDDOT/HGND2 are nested inside ARRESTER; the importer pulls
    # dependencies, but naming them keeps the import explicit.
    needed |= {"WDDOT", "HGND2"}
    imported, missing = import_symbols(doc, args.library, needed)

    warnings = build(units, cfg, doc)
    doc.saveas(args.output_dxf)

    archetypes = {}
    for u in units:
        archetypes[u["archetype"]] = archetypes.get(u["archetype"], 0) + 1

    print(f"Wrote {args.output_dxf}")
    print(f"  units:      {len(units)}"
          + (f" (bus {args.bus})" if args.bus else ""))
    print(f"  archetypes: " + ", ".join(f"{k}={v}" for k, v in sorted(archetypes.items())))
    print(f"  blocks:     {len(imported)} imported")
    stubs = [b for b in imported if b.startswith("SLD_STUB_")]
    if stubs:
        print(f"  STUBS in use (red layer {stubs[0].split('_')[0]}_STUB): {', '.join(stubs)}")
    if missing:
        print(f"  MISSING from library: {', '.join(missing)}")
    for w in warnings:
        print(f"  WARNING: {w}")


if __name__ == "__main__":
    main()

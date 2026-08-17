#!/usr/bin/env python3
"""
Fork sld_config_assignment.json from sld_config.json, the same way
sld_config_8372.json/8513.json/8478.json each already stand apart from the
8508 base rather than sharing its sheet geometry. sld_config.json itself is
never touched, so the 8508 golden fixtures are unaffected.

Two independent problems, both scoped to this fork only:

1. The bus PT on utility_incoming (bus_pt_data + pt_fuse_data, series:True,
   dx:0.0) sits directly on the breaker's own conductor path -- with a
   breaker below it on the same run, that puts the PT and its fuse in series
   with the 1200A main conductor. Checking the two standards that actually
   carry a bus PT alongside a breaker (8513's main_pt, 8372's
   main_with_bus_pt) shows neither does this: both draw the PT as a shunt tap
   -- series:False, connected by its own explicit riser wire -- never as part
   of the automatic series/gap conductor system. 8513 offsets the tap
   sideways; 8372 runs it straight up off the bus on the breaker's own
   centreline, which is what the assignment calls for ("PT above the bus").
   Ported here as three lead risers + three series:False blocks. Order going
   UP from the bus is disconnect -> primary fuse -> PT, measured directly off
   the issued sheet 8372+A01-050-001.dxf (see the table at section 1). The
   fuse must precede the PT: it exists to clear a PT primary fault before it
   becomes a bus fault, which it cannot do from downstream. An earlier pass
   had the PT first and the fuse beyond it, taken from the pri/sec role-name
   suffixes in sld_config_8372.json, which are inverted relative to the
   drawing.

2. 8508's frame puts bus_y only 2.019 below cubicle_top -- enough for the PT
   branch to physically fit (it only needs about 1.74), but not enough room
   for the unit header text to clear it without a horizontal dodge, and not
   enough to read as anything but cramped. cubicle_top is raised to give
   bus_y the same ~5.67 of headroom 8372 unit 6 actually uses for this same
   pattern (a symmetric split would need the full 9.148 the breaker/CT/relay
   stack below needs, roughly doubling the frame for no engineering reason).
   unit_number/unit_tag move up by the same delta so the header stays
   anchored to the new top of the box instead of stranding mid-frame.

Raising the frame also displaces the bus rating label, which sat just above
bus_y in the space the PT branch now occupies; it moves above the branch
rather than riding the header's delta.

This script is the only source of sld_config_assignment.json -- re-running it
must reproduce the committed file byte for byte. Edit the fork by editing this
script, never the JSON, or the next run silently reverts the change.

Also: the "outgoing" terminal on motor_feeder/feeder_breaker gets a
role_overrides rotation flip (it shared drawout_upper's rotation, so at the
bottom of the unit it pointed back up into the cubicle instead of down
toward the destination cable), and the bus PT is scaled up 1.4x for
legibility. Both via mechanisms already in build_sld.py (role_overrides,
scale) that no other archetype uses, so this cannot affect anything else.

    python make_assignment_config.py sld_config.json sld_config_assignment.json
"""
import json, sys
from collections import OrderedDict

src, dst = sys.argv[1], sys.argv[2]
cfg = json.load(open(src, encoding='utf-8'), object_pairs_hook=OrderedDict)
sheet, text, roles, arch = cfg['sheet'], cfg['text'], cfg['roles'], cfg['archetypes']
bus_y = sheet['bus_y']

# ---- 1. bus PT as a proper shunt tap, not a series device -----------------
# Order, measured off the issued reference sheet 8372+A01-050-001.dxf rather
# than off a derived config. Its bus PT column (x=24.33, bus_y 13.592) runs,
# going UP from the bus:
#
#     15.489  WD1005      drawout / disconnect
#     17.566  HC01PJ_1-   fuse, labelled "CLF 0.5E"
#     18.262  arc         PT coil
#     18.682  arc         PT coil
#     19.188  WDDOT       secondary reference tap leaves here
#
# The fuse is BETWEEN the bus and the PT, which is the only arrangement that
# does anything: a PT primary fault has to be cleared by the fuse instead of
# becoming a bus fault. "CLF 0.5E" is a current-limiting E-rated 0.5A fuse --
# a primary fuse, not the few-amp 120V secondary one, so it takes
# pt_primary_fuse.
#
# Note the reference's own role names in sld_config_8372.json invert this:
# pt_coil_pri sits at 18.682 and pt_coil_sec at 18.262, so the coil named
# "pri" is the one FURTHER from the bus. Trusting those suffixes is what put
# an earlier pass of this file in the wrong order; the drawing is the source
# of truth, not the derived config.
#
# Spacing is 8508's own rhythm (bus -> first device 0.879, device -> device
# 0.703, from bus_pt_data/pt_fuse_data below the bus) rather than 8372's
# absolute gaps, which belong to a taller sheet and would push the PT into
# the unit header. Gap half-heights likewise come from the 8508 roles these
# replace.
PT_SCALE = 1.4
disc_y = round(bus_y + 0.879, 3)
fuse_y = round(disc_y + 0.703, 3)
pt_y = round(fuse_y + 0.703, 3)
pt_half = round(0.1565 * PT_SCALE, 4)     # scaled -- was the unscaled 8508 half-height
fuse_half = 0.156
# Asymmetric, taken from the base drawout_lower (also rotation 180): its gap
# [14.346, 14.189] straddles y=14.252, so 0.094 above centre and 0.063 below.
_dl = cfg['roles']['drawout_lower']
disc_half_hi = round(_dl['gap'][0] - _dl['y'], 3)
disc_half_lo = round(_dl['y'] - _dl['gap'][1], 3)

# bus_pt_data/pt_fuse_data are deliberately NOT deleted. Only their use in
# utility_incoming was wrong -- there a breaker shares the run, so series:True
# put them in the 1200A path. In bus_pt_only, a PT-only cubicle with no breaker,
# the riser IS the whole conductor and series:True is exactly right. Dropping
# them here would leave bus_pt_only pointing at roles that no longer exist, and
# resolve_roles() raises KeyError on the first row that uses it.

def _riser(y0, y1, note):
    return OrderedDict([
        ("kind", "lead"), ("points", [[0.0, round(y0, 4)], [0.0, round(y1, 4)]]),
        ("layer", "SLD_WIRE"), ("_note", note),
    ])


# Every block below is series:False, so none of them joins the automatic
# series/gap conductor system that the breaker's main run uses -- that is the
# whole point of the fix. They therefore need their connecting wire drawn
# explicitly, the way 8372's pt_riser_bpt does.
roles['pt_riser_lo'] = _riser(
    bus_y, disc_y - disc_half_lo,
    "Bus up to the PT disconnect -- the tap point off the main bus.")
roles['pt_disconnect'] = OrderedDict([
    ("kind", "block"), ("block", "VCN1PJ"), ("dx", 0.0), ("y", disc_y),
    ("rotation", 180), ("series", False),
    ("_note", "Drawout disconnect at the base of the PT branch, so the PT can be "
              "isolated from the live bus for fuse replacement. The reference "
              "uses WD1005 here (8372 sheet 001 y=15.489), but that block is a "
              "single polyline that renders as a small marker; VCN1PJ is the "
              "drawout symbol this sheet already uses for the breaker contacts, "
              "and disc_half_hi/lo above are measured from it via drawout_lower."),
])
roles['pt_riser_mid'] = _riser(
    disc_y + disc_half_hi, fuse_y - fuse_half,
    "Disconnect up to the primary fuse.")
roles['pt_fuse_pri'] = OrderedDict([
    ("kind", "block"), ("block", "VFU1_1-"), ("dx", 0.0), ("y", fuse_y),
    ("rotation", 0), ("series", False),
    ("attribs", OrderedDict([("RATING1", "pt_primary_fuse")])),
    ("_note", "PT PRIMARY fuse, between the bus and the PT. A primary fault has "
              "to open here rather than propagate to the bus, so it cannot sit "
              "beyond the PT. Reference: 8372 sheet 001 y=17.566, 'CLF 0.5E'."),
])
roles['pt_riser_hi'] = _riser(
    fuse_y + fuse_half, pt_y - pt_half,
    "Primary fuse up to the PT itself -- the fused side of the tap.")
roles['bus_pt_above'] = OrderedDict([
    ("kind", "block"), ("block", "VXF1T1_1-"), ("dx", 0.0), ("y", pt_y),
    ("rotation", 0), ("series", False), ("scale", PT_SCALE),
    ("attribs", OrderedDict([("DESC1", "@PT (3)"), ("DESC2", "pt")])),
    ("_note", f"Bus PT on a shunt tap above the bus, fed through the disconnect "
              f"and primary fuse below it, not in series with the breaker (see "
              f"8513 main_pt / 8372 main_with_bus_pt). Scaled {PT_SCALE}x for "
              "legibility."),
])

PT_BRANCH = ["pt_riser_lo", "pt_disconnect", "pt_riser_mid",
             "pt_fuse_pri", "pt_riser_hi", "bus_pt_above"]

ui = arch['utility_incoming']
ui['roles'] = PT_BRANCH + \
              [r for r in ui['roles'] if r not in ("bus_pt_data", "pt_fuse_data")]
sp = ui.setdefault('_spec', OrderedDict())
sp.pop('bus_pt_data + pt_fuse_data', None)
sp[' + '.join(PT_BRANCH)] = (
    "'Provide three voltage transformers' and 'Show PT secondary protection'. "
    "Drawn as a shunt tap off the bus (series:False + explicit risers), not as "
    "part of the breaker's series conductor -- see 8513 main_pt / 8372 "
    "main_with_bus_pt for the same pattern in reference-verified drawings. "
    "Ordered disconnect -> primary fuse -> PT going up from the bus, measured "
    "off issued sheet 8372+A01-050-001.dxf, so the fuse can actually clear a "
    "PT primary fault instead of sitting downstream of it.")
ui['_open'] = (
    "PT placement follows 8372 sheet 001: tapped off the bus, above it, on the "
    "breaker's own centreline, through a disconnect and primary fuse. The "
    "specification asks for 'PT secondary protection' but names no primary "
    "fuse rating, and the reference's own CLF 0.5E is a 4160V part -- so "
    "pt_primary_fuse is currently blank in the CSV and the fuse renders "
    "unlabelled. A rating needs picking for 15kV before this sheet is issued. "
    "The spec also still does not say whether the incomer's VTs sit in this "
    "cubicle or an adjacent one; on 8372 the line PT was housed next door.")

# ---- 2. headroom for the branch + the header text that shares that space --
NEW_TOP = round(bus_y + 5.67, 3)     # same clearance 8372 unit 6 gives its own PT branch
delta = round(NEW_TOP - sheet['cubicle_top'], 3)
sheet['cubicle_top'] = NEW_TOP
sheet['_cubicle_top_note'] = (
    f"Raised from 20.602 so bus_y has {round(NEW_TOP - bus_y, 3)} of headroom -- "
    "the same the bus-PT-above-breaker branch gets in 8372 unit 6 -- instead of "
    "8508's 2.019, which fits the branch but leaves no room for the unit header. "
    "cubicle_bottom and everything below bus_y (breaker, CTs, relay, outgoing) "
    "are untouched. Forked from sld_config.json, which keeps its own 20.602 for "
    "the 8508 fixtures.")
text['unit_number']['y'] = round(text['unit_number']['y'] + delta, 3)
text['unit_tag']['y'] = round(text['unit_tag']['y'] + delta, 3)
ui.pop('text_overrides', None)   # no dodge needed once the header clears the branch vertically

# The bus rating label cannot ride the header's delta. At 8508's 19.088 it sat
# just above bus_y, which is now the PT branch. It is a 3-line MTEXT anchored
# at the TOP and growing downward -- measured at 1.298 tall in the built DXF --
# so the gap between the branch top and unit_tag's own text block (1.349) has
# no usable margin. It goes below the bus instead, where the run from bus_y
# down to drawout_upper's gap is 2.38 clear, keeping it adjacent to the bus it
# describes and on the same left-aligned centreline offset 8508 used.
LABEL_H = 1.3           # measured from the built sheet; MTEXT grows downward
old_label_y = text['bus_label']['y']
LABEL_Y = round(bus_y - 0.15, 3)
branch_top = round(pt_y + pt_half, 3)   # the PT is now the topmost element
_ceiling = cfg['roles']['drawout_upper']['gap'][0]
assert LABEL_Y < bus_y and LABEL_Y - LABEL_H > _ceiling, (
    f"bus_label block [{round(LABEL_Y - LABEL_H, 3)}, {LABEL_Y}] must clear "
    f"bus_y ({bus_y}) above and drawout_upper ({_ceiling}) below")
assert branch_top < text['unit_tag']['y'], (
    f"PT branch top {branch_top} collides with unit_tag {text['unit_tag']['y']}")
text['bus_label']['y'] = LABEL_Y
sheet['_bus_label_note'] += (
    f" Moved to {LABEL_Y} (was {old_label_y}) in this fork: the bus PT branch now "
    "occupies the space directly above bus_y, and at the original position the bus "
    "rating text ran straight through the fuse and PT. It is anchored at its top "
    "and {0} tall, so the band above the branch cannot hold it; placed just below "
    "the bus instead, which is clear all the way down to the breaker drawout."
    .format(LABEL_H))

# ---- 3. feeder outgoing terminal points down, not back into the unit ------
for name in ('motor_feeder', 'feeder_breaker'):
    a = arch[name]
    a.setdefault('role_overrides', OrderedDict())['outgoing'] = OrderedDict([("rotation", 180)])
    a['_open'] = (a.get('_open', '') + ' ' if a.get('_open') else '') + (
        "outgoing.rotation overridden to 180 (matching drawout_lower) so the exit "
        "terminal points down toward the destination label rather than sharing "
        "drawout_upper's up-pointing rotation; the shared outgoing role used by "
        "8508 archetypes is unaffected.")

# newline='' keeps LF on Windows, so re-running this reproduces the committed
# file byte for byte instead of showing up as a whole-file CRLF diff.
json.dump(cfg, open(dst, 'w', encoding='utf-8', newline=''), indent=2, ensure_ascii=False)
print(f'wrote {dst}')
print(f"  branch up from bus {bus_y}: disc {disc_y} -> fuse {fuse_y} -> PT {pt_y} (scale {PT_SCALE})")

print(f'  cubicle_top   20.602 -> {NEW_TOP}  (bus_y unchanged {bus_y})')
print(f'  unit_number.y {text["unit_number"]["y"]}   unit_tag.y {text["unit_tag"]["y"]}')

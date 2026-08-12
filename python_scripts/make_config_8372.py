"""
Emit sld_config_8372.json -- the 4.76kV standard for job 8372.

Measured off the two reference sheets 8372+A01-050-001 and -002, replacing the
first draft of this file, which was written before those existed and guessed
the frame from 8513. Nothing here is inferred any more except where the two
sheets disagree with each other, and those places say so.

The sheets do disagree. They are the same lineup drawn twice -- units 1-7 on
001, units 8-14 on 002 -- and each was laid out to fit its own contents:

    sheet      cubicle height   bus      breaker below bus
    001        14.927           15.489   3.668
    002        14.352           13.592   2.314

What does *not* vary is the breaker assembly. Both sheets put 1.600 between the
primary disconnects and 0.881 from the lower disconnect to the CT. That is the
same result 8513 gave: device dimensions are real and fixed, the space around
them is a drafting choice. Since a generator emits one sheet, this file takes
sheet 002's frame -- it is the one carrying the two-high cubicles, so it is the
constrained case -- and puts every breaker in the lineup at the elevations that
sheet uses.

Two engineering facts recovered from the reference that the BOM and the CSV
could not have told us:

  CT side     A generator feeder carries its CT on the machine side, below the
              breaker (unit 8: CT at 8.796, breaker at 11.278..9.677). A load
              feeder carries it on the bus side, above (unit 12 lower: CT at
              12.241). The utility main carries one of each, straddling the
              breaker, which is what an SEL-787 bus differential needs.

  relay       'SEL-751 (25, 50P, 51P, 50G, 51G)' is not a caption. The sheets
              draw the device name in a box and circle each ANSI function
              beside it. The relay column was already written that way, so the
              builder parses it -- see place_relay_functions.

Usage:
    python make_config_8372.py > sld_config_8372.json
"""
import copy
import json

# --- frame, measured off sheet 002 -----------------------------------------
PITCH = 4.402               # 13 of the 14 cubicles; see UNIT_14_WIDTH below
CUBICLE_TOP = 21.485
CUBICLE_BOTTOM = 7.133
BUS_Y = 13.592
CENTRELINE = 2.457
FIRST_X = 2.093
UNIT_14_WIDTH = 4.787       # the one wide cubicle -- open question, see README

# --- the rigid breaker assembly, identical on both sheets ------------------
LOWER_UPPER = 11.278        # lower-deck / single-high drawout, upper
LOWER_LOWER = 9.677         # ... and lower. Span 1.601 on both sheets.
UPPER_UPPER = 17.665        # upper-deck drawout, upper
UPPER_LOWER = 16.064
BREAKER_INSET = 0.289       # drawout face to breaker body, from unit 8
BREAKER_W = 0.563
CT_BELOW = 0.881            # lower drawout down to a line-side CT
CT_ABOVE = 0.963            # upper drawout up to a bus-side CT (12.241-11.278)


# VC01PJ_1- spans these about its own insertion point, so a connector placed
# at y interrupts the conductor across that span. A stacked config gets this
# from the library at expand time; a measured one has to state it.
CONN_TOP, CONN_BOTTOM = 0.0729, -0.1285


def connector(y, rotation=0, series=True, terminal=False):
    spec = {"kind": "block", "block": "VC01PJ_1-", "dx": 0.0, "y": y,
            "rotation": rotation, "series": series}
    if series:
        spec["gap"] = [y + CONN_TOP, y + CONN_BOTTOM]
    if terminal:
        spec["terminal"] = True
    return spec


def breaker(top, bottom):
    y_top, y_bottom = top - BREAKER_INSET, bottom + BREAKER_INSET
    return {"kind": "breaker", "dx": -BREAKER_W / 2, "width": BREAKER_W,
            "y_top": y_top, "y_bottom": y_bottom,
            "series": True, "gap": [y_top, y_bottom]}


def ct(y, column):
    return {"kind": "block", "block": "1LCT1A", "dx": 0.0, "y": y,
            "rotation": 0, "series": False,
            "attribs": {"DESC1": column}}


def ct_label(y, column):
    return {"kind": "note", "dx": 0.520, "y": y, "height": 0.125,
            "align": "left", "text": column}


def relay(top):
    """Relay panel, positioned by its own top edge."""
    return {
        "kind": "relay_functions", "text": "relay",
        "dx": -1.815, "width": 2.288, "y_top": top, "y_bottom": top - 1.011,
        "layer": "SLD_SYMS", "radius": 0.138, "per_row": 3,
        "step_x": 0.372, "step_y": 0.370,
        "first_dx": 1.356, "first_dy": -0.166,
        "name_style": {"dx": 0.089, "dy": 0.108, "height": 0.125,
                       "align": "left"},
        "label_dy": -0.051,
    }


def deck(sfx, drawout_top, drawout_bottom, ct_side, relay_top, cpt_y=None,
         entry=None, exit_y=None):
    """
    One breaker and everything that belongs to it.

    `ct_side` is 'line' or 'bus' and decides which end of the breaker the CT
    sits on -- the one distinction the reference drawings make between a
    generator feeder and a load feeder.
    """
    roles = {
        f"drawout_upper{sfx}": connector(drawout_top),
        f"breaker{sfx}": breaker(drawout_top, drawout_bottom),
        f"drawout_lower{sfx}": connector(drawout_bottom, rotation=180),
        f"relay{sfx}": relay(relay_top),
    }
    if ct_side == "line":
        y = drawout_bottom - CT_BELOW
    else:
        y = drawout_top + CT_ABOVE
    roles[f"ct{sfx}"] = ct(y, "ct_protection")
    roles[f"ct_label{sfx}"] = ct_label(y + 0.117, "ct_protection")
    if cpt_y is not None:
        # The control-power tap sits above the bus, so the main conductor --
        # which runs downward from the bus -- never reaches it. The reference
        # carries it on a short riser from the bus up into the relay panel.
        roles[f"cpt_riser{sfx}"] = {
            "kind": "lead", "layer": "SLD_WIRE",
            "points": [[0.0, BUS_Y], [0.0, relay_top - 1.011]],
        }
        roles[f"cpt_tap{sfx}"] = {"kind": "block", "block": "WD1005",
                                  "dx": 0.0, "y": cpt_y, "rotation": 0,
                                  "series": False}
    if entry is not None:
        roles[f"cable_entry{sfx}"] = connector(entry, rotation=180, series=False)
    if exit_y is not None:
        roles[f"cable_exit{sfx}"] = connector(exit_y, series=True, terminal=True)
    return roles


def pt_set(sfx, dx, tie_to_bus):
    """
    A three-phase PT set drawn above the bus, measured off unit 11.

    The two on this lineup differ in one thing only: unit 6's bus PT set is
    tapped off the bus and drawn connected to it, while unit 11's line PT set
    is fed from the machine's own PTs off-sheet ('LINE VOLTAGE REFERENCE FROM
    INNIO PTs') and stands free above the relay. `tie_to_bus` draws the riser
    for the first case and leaves it out for the second.

    The windings are two facing pairs of half-arcs rather than a block, which
    is how the reference draws them.
    """
    roles = {
        f"pt_disc{sfx}": {"kind": "block", "block": "HC01PJ_1-", "dx": dx,
                          "y": 19.533, "rotation": 270, "series": False},
        f"pt_lead_top{sfx}": {"kind": "lead", "layer": "SLD_WIRE",
                              "points": [[dx, 19.685], [dx, 19.596]]},
        f"pt_lead_fuse{sfx}": {"kind": "lead", "layer": "SLD_WIRE",
                               "points": [[dx, 19.439], [dx, 19.322]]},
        f"pt_fuse_body{sfx}": {"kind": "box", "dx": dx - 0.047, "width": 0.094,
                               "y_top": 19.322, "y_bottom": 19.010,
                               "layer": "SLD_SYMS"},
        f"pt_fuse_ticks{sfx}": {"kind": "lead", "layer": "SLD_SYMS",
                                "points": [[dx - 0.047, 19.260], [dx + 0.047, 19.260]]},
        f"pt_fuse_ticks2{sfx}": {"kind": "lead", "layer": "SLD_SYMS",
                                 "points": [[dx - 0.047, 19.072], [dx + 0.047, 19.072]]},
        f"pt_lead_pri{sfx}": {"kind": "lead", "layer": "SLD_WIRE",
                              "points": [[dx, 19.010], [dx, 18.682]]},
        f"pt_lead_sec{sfx}": {"kind": "lead", "layer": "SLD_WIRE",
                              "points": [[dx, 17.247], [dx, 18.265]]},
        f"pt_tap_hi{sfx}": {"kind": "block", "block": "WDDOT", "dx": dx,
                            "y": 17.938, "rotation": 0, "series": False},
        f"pt_tap_lo{sfx}": {"kind": "block", "block": "WDDOT", "dx": dx,
                            "y": 17.558, "rotation": 0, "series": False},
        f"pt_label{sfx}": {"kind": "note", "dx": dx - 0.626, "y": 20.213,
                           "height": 0.11, "align": "left", "wrap": 14,
                           "text": "pt"},
        f"pt_fuse_label{sfx}": {"kind": "note", "dx": dx + 0.15, "y": 19.24,
                                "height": 0.10, "align": "left", "wrap": 14,
                                "text": "pt_fuse"},
    }
    # Primary winding bulges down, secondary up, so the two face each other.
    for i, off in enumerate((-0.143, 0.142)):
        roles[f"pt_coil_pri{i}{sfx}"] = {
            "kind": "arc", "dx": dx + off, "y": 18.682, "radius": 0.143,
            "start_angle": 180.0, "end_angle": 0.0, "layer": "SLD_SYMS"}
        roles[f"pt_coil_sec{i}{sfx}"] = {
            "kind": "arc", "dx": dx + off, "y": 18.262, "radius": 0.143,
            "start_angle": 0.0, "end_angle": 180.0, "layer": "SLD_SYMS"}
    for i, off in enumerate((-0.285, 0.285)):
        roles[f"pt_wind_hi{i}{sfx}"] = {
            "kind": "lead", "layer": "SLD_WIRE",
            "points": [[dx + off, 18.682], [dx + off, 18.860]]}
        roles[f"pt_wind_lo{i}{sfx}"] = {
            "kind": "lead", "layer": "SLD_WIRE",
            "points": [[dx + off, 18.262], [dx + off, 18.084]]}
    if tie_to_bus:
        roles[f"pt_riser{sfx}"] = {
            "kind": "lead", "layer": "SLD_WIRE",
            "points": [[dx, BUS_Y], [dx, 17.247]]}
    else:
        # The reference note the free-standing set feeds, boxed as on the sheet.
        roles[f"pt_ref_spur{sfx}"] = {
            "kind": "lead", "layer": "SLD_WIRE",
            "points": [[dx - 0.563, 17.558], [dx, 17.558]]}
        roles[f"pt_ref_box{sfx}"] = {
            "kind": "box", "dx": dx - 2.554, "width": 1.991,
            "y_top": 17.868, "y_bottom": 17.248, "layer": "SLD_SYMS"}
        roles[f"pt_ref_text{sfx}"] = {
            "kind": "note", "dx": dx - 2.49, "y": 17.80, "height": 0.093,
            "align": "left", "wrap": 24, "text": "pt_reference"}
    return roles


def build():
    roles = {"_note": "Every elevation here is measured off the 8372 reference "
                      "sheets. No stack is used: the standard has a drawing, so "
                      "it is ruled rather than derived."}
    archetypes = {}

    # --- generator feeder: single-high, CT on the machine side ------------
    roles.update(deck("_g", LOWER_UPPER, LOWER_LOWER, "line", 15.904,
                      cpt_y=14.406, exit_y=6.695))
    gen_roles = ["cpt_riser_g", "cpt_tap_g", "drawout_upper_g", "breaker_g", "drawout_lower_g",
                 "ct_g", "ct_label_g", "cable_exit_g", "relay_g"]
    archetypes["gen_feeder"] = {
        "description": "Generator incoming, single-high. Bus at the top of the "
                       "run, machine at the bottom, so the CT sits below the "
                       "breaker on the line side.",
        "roles": gen_roles,
    }
    # Unit 11: the same generator feeder plus a free-standing line PT set.
    roles.update(pt_set("_lpt", 0.272, tie_to_bus=False))
    archetypes["gen_with_line_pt"] = {
        "description": "Generator incoming carrying the line PT set. The PTs "
                       "are fed from the machine's own reference off-sheet, so "
                       "they stand clear of the power conductor and feed the "
                       "mains relay through the boxed note.",
        "roles": gen_roles + sorted(k for k in roles if k.endswith("_lpt")),
    }

    # --- load feeder: CT on the bus side ----------------------------------
    roles.update(deck("_c", LOWER_UPPER, LOWER_LOWER, "bus", 12.547 + 1.011,
                      exit_y=6.695))
    load_roles = ["drawout_upper_c", "breaker_c", "drawout_lower_c", "ct_c",
                  "ct_label_c", "cable_exit_c", "relay_c"]
    lower = {
        "description": "Load feeder hanging below the bus. Bus at the top of "
                       "the run and the load at the bottom, so the CT sits "
                       "above the breaker on the bus side.",
        "roles": load_roles,
    }
    for name in ("customer_feeder", "customer_feeder_lower"):
        archetypes[name] = copy.deepcopy(lower)
    # Unit 6: a load feeder plus the bus PT set, which is tapped off the bus.
    roles.update(pt_set("_bpt", 0.004, tie_to_bus=True))
    archetypes["main_with_bus_pt"] = {
        "description": "Main breaker carrying the three-phase bus PT set, "
                       "tapped off the bus and feeding the sync reference.",
        "roles": load_roles + sorted(k for k in roles if k.endswith("_bpt")),
    }

    # --- upper deck of a two-high cubicle ---------------------------------
    roles.update(deck("_u", UPPER_UPPER, UPPER_LOWER, "bus", 15.528 + 1.011,
                      entry=19.685))
    roles["ct_u"]["y"] = 15.072          # measured: bus-side CT of unit 13 upper
    roles["ct_label_u"]["y"] = 15.189
    archetypes["customer_feeder_upper"] = {
        "description": "Feeder on the upper deck of a two-high cubicle. The "
                       "cable enters at the top of the sheet and the run comes "
                       "down through the breaker to the bus, which is what the "
                       "reference letters 'LINE ... BUS'.",
        "conductor_top": 19.685,
        "roles": ["cable_entry_u", "drawout_upper_u", "breaker_u",
                  "drawout_lower_u", "ct_u", "ct_label_u", "relay_u"],
        "text_overrides": {
            "unit_number": {"hide": True},
            "spec_block": {"hide": True},
            "relay_label": {"hide": True},
            "destination": {"dx": -0.801, "y": 20.90},
        },
    }

    # --- utility main: a CT on each side of the breaker -------------------
    roles.update(deck("_utl", UPPER_UPPER, UPPER_LOWER, "bus", 15.528 + 1.011,
                      entry=19.685))
    roles["ct_utl"]["y"] = 18.659        # line side, measured
    roles["ct_label_utl"]["y"] = 18.776
    roles["ct_bus_utl"] = ct(15.072, "ct_metering")
    roles["ct_bus_label_utl"] = ct_label(15.189, "ct_metering")
    archetypes["utility_main_787"] = {
        "description": "Utility main on the upper deck. SEL-751 line "
                       "protection above the breaker and the SEL-787 bus "
                       "differential CT below it, which is why this is the one "
                       "unit in the lineup carrying two CT sets.",
        "conductor_top": 19.685,
        "roles": ["cable_entry_utl", "ct_utl", "ct_label_utl",
                  "drawout_upper_utl", "breaker_utl", "drawout_lower_utl",
                  "ct_bus_utl", "ct_bus_label_utl", "relay_utl"],
        "text_overrides": {
            "unit_number": {"hide": True},
            "spec_block": {"hide": True},
            "relay_label": {"hide": True},
            "destination": {"dx": -0.801, "y": 20.90},
        },
    }

    return {
        "_standard": "8372 -- 4.76kV metal-clad switchgear, 14 cubicles, "
                     "17 breakers, three of them two-high.",
        "_provenance": __doc__.strip(),
        "sheet": {
            "pitch": PITCH,
            "_pitch_note": "Thirteen of the fourteen cubicles are 4.402 wide; "
                           "unit 14 alone is 4.787. The house rule is to draw "
                           "every unit to the widest, which would make the "
                           "sheet 5.4 wider than the reference, so the "
                           "repeated pitch is used and the odd cubicle is "
                           "flagged as a question rather than propagated.",
            "centreline_from_left": CENTRELINE,
            "cubicle_top": CUBICLE_TOP,
            "cubicle_bottom": CUBICLE_BOTTOM,
            "bus_y": BUS_Y,
            "bus_extension": 0.0,
            "bus_dot_at_ends": False,
            "first_cubicle_x": FIRST_X,
            "bus_label": "",
        },
        "text": {
            "_note": "Placements are the lower deck's, because eleven of the "
                     "fourteen cubicles are single-high and sit there. The two "
                     "upper decks suppress most of these and letter their "
                     "destination at the top of the sheet instead.",
            "unit_number": {"dx": -1.821, "y": 21.626, "height": 0.203,
                            "align": "left"},
            "unit_tag": {"hide": True, "dx": 0.0, "y": 0.0, "height": 0.2},
            "spec_block": {"hide": True, "dx": 0.0, "y": 0.0, "height": 0.1,
                           "_note": "The reference letters the rating inside "
                                    "the breaker body, not in a spec block."},
            "relay_label": {"hide": True, "dx": 0.0, "y": 0.0, "height": 0.09,
                            "_note": "The relay is drawn by the "
                                     "relay_functions role, which parses the "
                                     "same column."},
            "destination": {"dx": -0.801, "y": 6.323, "height": 0.125,
                            "align": "left", "wrap": 26},
            "bus_label": {"hide": True, "dx": 0.0, "y": 0.0, "height": 0.1},
        },
        "control": {"hop_radius": 0.05, "buses": []},
        "roles": roles,
        "archetypes": archetypes,
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=1))

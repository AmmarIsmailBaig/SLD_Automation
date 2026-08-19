"""
Build an SLD straight from an intake workbook.

    python build_intake.py intake_assignment.xlsx out.dxf

The old path needed a hand-measured config per job, with an absolute sheet
elevation for every device. That is what forced a new archetype each time a
cubicle differed by one device, and it is why "two CTs" ended up inside type
names -- gen_feeder carried a metering CT because the drawing it was copied
from happened to have one.

Here the unit row states what the cubicle contains and the devices are stacked
in a fixed order, each one dropping its declared gap below the last. A device
appears if and only if its column has a value, so nothing can be inherited
from a reference drawing. A skipped device closes the stack up instead of
leaving a hole.

The generated config is written beside the DXF so it can be read, diffed and
redlined -- the drafting decisions stay visible rather than disappearing into
the renderer.
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import read_intake

HERE = Path(__file__).resolve().parent
STANDARD = HERE / "standard.json"
LIBRARY = HERE.parent / "cad_files" / "symbol_library.dxf"

# Job fields that fill the bus label. Anything else in the template is title
# block material and does not reach the geometry.
LABEL_FIELDS = ("system_voltage", "bus_rating", "bus_ka_rating", "bil")


def present(when, row, std):
    """Is the device named by this 'when' rule called for by the unit row?"""
    if when == "always":
        return True
    if when == "any_ct":
        # Read off the stack rather than listed here, so adding a CT to the
        # standard puts it inside the compartment box instead of leaving it
        # hanging outside one sized from a list nobody remembered to update.
        return any(row.get(e["when"], "").strip() for e in std["stack"]
                   if e["name"].startswith("ct_"))
    return bool(row.get(when, "").strip())


def stack_unit(std, row, key):
    """Lay out one cubicle. Returns {role_name: spec} and the role order.

    Everything is measured downward from the bus, so the elevations a unit ends
    up with depend only on what it actually carries.
    """
    roles, order, anchors = {}, [], {}

    def emit(name, spec):
        full = f"{key}_{name}"
        roles[full] = spec
        order.append(full)
        return full

    def terminals(spec, y):
        """Move a block's conductor break onto its own terminals."""
        up = spec.pop("gap_up", None)
        down = spec.pop("gap_down", None)
        if spec.get("series") and up is not None:
            spec["gap"] = [round(y + up, 4), round(y - down, 4)]
        return up, down

    # --- the main run, bus downward -------------------------------------
    y = std["sheet"]["bus_y"]
    ct_ys, breaker_top = [], None

    for entry in std["stack"]:
        if not present(entry["when"], row, std):
            continue
        y = round(y - entry["gap"], 4)
        spec = json.loads(json.dumps(entry["role"]))  # deep copy, config is data

        if spec["kind"] == "breaker":
            bottom = round(y - entry["height"], 4)
            spec.update(y_top=y, y_bottom=bottom, gap=[y, bottom])
            breaker_top = y
            y = bottom  # the next gap hangs off the breaker's bottom, not its top
        else:
            spec["y"] = y
            terminals(spec, y)

        emit(entry["name"], spec)

        # A device whose secondary has to be wired somewhere records where it
        # ended up, so control_wiring can run to it without re-deriving the
        # stack. Going through anchors also means reflect() turns the wiring
        # over with the deck for free.
        if entry.get("secondary"):
            # Recorded at the terminal the wire actually leaves, not at the
            # symbol's insertion point -- a CT's two secondary terminals sit
            # 0.249 either side of it, and a lead drawn from the middle lands
            # on the iron between them. Applying the offset here rather than in
            # control_wiring means reflect() picks the mirror-image terminal on
            # an upper deck, which is the one facing that deck's relay.
            anchors[entry["name"]] = round(
                y + entry["secondary"].get("dy", 0.0), 4)
        if entry["name"].startswith("ct_"):
            ct_ys.append(y)

    # --- pinned devices, at a fixed elevation ---------------------------
    for entry in std["pinned"]:
        if not present(entry["when"], row, std):
            continue
        spec = json.loads(json.dumps(entry["role"]))
        # Normally pinned to the cubicle floor so cable exits line up across the
        # lineup. A unit carrying enough CTs to stack past that point pushes the
        # exit down instead, rather than ending up above its own last device.
        pin = entry["y"]
        if "push_gap" in entry:
            pin = min(pin, round(y - entry["push_gap"], 4))
        spec["y"] = pin
        terminals(spec, pin)
        anchors["outgoing"] = pin
        emit(entry["name"], spec)

    # --- devices placed relative to something else ----------------------
    for name, entry in std["beside"].items():
        if not present(entry["when"], row, std):
            continue
        spec = json.loads(json.dumps(entry["role"]))
        spec.pop("_note", None)

        if entry["anchor"] == "breaker":
            if breaker_top is None:
                continue
            spec["y_top"] = round(breaker_top + entry["dy"], 4)
            spec["y_bottom"] = round(spec["y_top"] - spec.pop("height"), 4)
            anchors["relay_bottom"] = spec["y_bottom"]
            anchors["relay_top"] = spec["y_top"]
        elif entry["anchor"] == "cts":
            if not ct_ys:
                continue
            # Sized from the CTs this unit actually has, so a lineup with one
            # CT set gets a box around one CT set.
            spec["y_top"] = round(max(ct_ys) + entry["above"], 4)
            spec["y_bottom"] = round(min(ct_ys) - entry["below"], 4)
        elif entry["anchor"] in anchors:
            # Hung off a device that recorded where it landed -- the test
            # switch off the metering CT's secondary terminal. Anything the
            # stack skipped is simply not in anchors, so the device that hangs
            # off it is skipped too rather than drawn at nothing.
            spec["y"] = round(anchors[entry["anchor"]] + entry.get("dy", 0.0), 4)
        else:
            continue
        emit(name, spec)

    # --- the bus PT branch, above the bus -------------------------------
    branch = std["branch"]
    branch_roles = set()
    if present(branch["when"], row, std):
        y = std["sheet"]["bus_y"]
        prev_top = y  # the tap point on the bus itself
        for entry in branch["devices"]:
            y = round(y + entry["gap"], 4)
            spec = json.loads(json.dumps(entry["role"]))
            up = spec.pop("gap_up")
            down = spec.pop("gap_down")
            spec["y"] = y
            # A riser from whatever is below up to this device's lower terminal.
            branch_roles.add(emit(entry["name"] + "_riser", {
                "kind": "lead",
                "points": [[0.0, prev_top], [0.0, round(y - down, 4)]],
                "layer": std["conductor"]["layer"],
            }))
            branch_roles.add(emit(entry["name"], spec))
            prev_top = round(y + up, 4)
            anchors["pt"] = y

        # Secondary lead out to the reference drop's offset, so the drop meets
        # the transformer instead of starting in clear space beside it.
        for bus in std.get("control", {}).get("buses", []):
            if present(bus["source"], row, std):
                branch_roles.add(emit("pt_secondary", {
                    "kind": "lead",
                    "points": [[0.0, anchors["pt"]],
                               [bus.get("source_dx", bus["riser_dx"]), anchors["pt"]]],
                    "layer": "SLD_CONTROL",
                }))
                break

    if breaker_top is not None:
        anchors["breaker_top"] = breaker_top

    return roles, order, anchors, branch_roles


def tie_unit(std, row, key, bus_y):
    """A bus tie: the breaker lies IN the bus rather than hanging off it.

    Everything else in the lineup is a stack -- devices dropping one below
    another off the bus. The tie is the one cubicle that is not, so it is
    described in its own section rather than bent into stack entries whose
    gaps would all be zero. Elevations are 'dy' about the bus for the same
    reason the stack uses gaps: no absolute number here can fall out of step
    with the sheet.

    Splitting the lineup into two buses needs no separate mechanism. The tie
    declares the span the bus must break for, and what is left is two runs.
    """
    tie = std["tie"]
    roles, order, anchors = {}, [], {}

    for entry in tie["devices"]:
        if not present(entry.get("when", "always"), row, std):
            continue
        spec = json.loads(json.dumps(entry["role"]))
        spec.pop("_note", None)
        if "dy" in spec:
            spec["y"] = round(bus_y + spec.pop("dy"), 4)
        if "dy_top" in spec:
            spec["y_top"] = round(bus_y + spec.pop("dy_top"), 4)
            spec["y_bottom"] = round(bus_y + spec.pop("dy_bottom"), 4)
        if spec["kind"] == "lead":
            spec["points"] = [[px, round(bus_y + py, 4)] for px, py in spec["points"]]

        full = f"{key}_{entry['name']}"
        roles[full] = spec
        order.append(full)
        if entry.get("anchor"):
            anchors[entry["anchor"]] = spec.get("y", spec.get("y_bottom"))

    return roles, order, anchors, set()


def reflect(roles, anchors, bus_y, skip=()):
    """Mirror a laid-out deck about the bus, in place.

    The upper deck of a two-high cubicle is the lower deck upside down: it taps
    the same bus from underneath and runs its cable out through the roof. So it
    is built by the ordinary downward pass and then reflected, which means the
    two decks cannot drift apart -- there is only one set of gaps.

    'skip' names roles that are already on the upper side and must be left
    alone. The bus PT branch is the case: it stacks upward off the bus by
    definition, so reflecting it would drive it back down between the bus and
    the breaker -- precisely the arrangement the branch exists to avoid.
    """
    def flip(y):
        return round(2 * bus_y - y, 4)

    for name, spec in roles.items():
        if name in skip:
            continue
        if "y" in spec:
            spec["y"] = flip(spec["y"])
        # A span's top and bottom trade places, or the box inverts.
        if "y_top" in spec:
            spec["y_top"], spec["y_bottom"] = flip(spec["y_bottom"]), flip(spec["y_top"])
        if "gap" in spec and isinstance(spec["gap"], list):
            spec["gap"] = [flip(spec["gap"][1]), flip(spec["gap"][0])]
        if "points" in spec:
            spec["points"] = [[px, flip(py)] for px, py in spec["points"]]
        # Turning a symbol over turns its attribute text over with it, which
        # renders the CT ratio and PT ratio upside down. Only symbols that
        # actually point somewhere -- the drawout contacts -- are rotated; the
        # ones carrying text read the same either way up.
        if "rotation" in spec and not spec.get("attribs"):
            spec["rotation"] = (spec["rotation"] + 180) % 360
        # The polarity mark sits on the winding it belongs to, so it turns over
        # with the CT rather than staying above it.
        if "polarity_dot" in spec:
            spec["polarity_dot"]["dy"] = -spec["polarity_dot"]["dy"]

    return {k: flip(v) for k, v in anchors.items()}


def bus_name_for(bus, mirrored):
    return bus["name"] + ("_upper" if mirrored else "")


def control_wiring(std, row, anchors, mirrored, bus_y):
    """How this unit meets the lineup-wide control runs.

    Participation is declared per unit rather than per type, so the PT
    reference run reaches whichever cubicles carry a relay -- and originates
    wherever the PT happens to be -- instead of being fixed by an archetype.

    Each deck gets its own run at its own elevation. An upper-deck relay cannot
    be fed from the lower deck's run without crossing the main bus to get
    there, which is a short, not a drawing.

    CT secondaries are the same idea one level down. A CT drawn with a ratio
    and no output says nothing about what it protects, so each one names its
    destination on its stack entry: "relay" for its own cubicle's relay, or the
    name of a lineup-wide run for a core that leaves the cubicle -- the
    differential cores, which all end up at whichever cubicle holds the
    differential relay.
    """
    wiring, extra = {}, {}
    bus_ys = {}
    for bus in std.get("control", {}).get("buses", []):
        y = round(2 * bus_y - bus["y"], 4) if mirrored else bus["y"]
        name = bus_name_for(bus, mirrored)
        bus_ys[bus["name"]] = (name, y)

        if bus.get("source") and present(bus["source"], row, std):
            wiring.setdefault("joins", []).append(name)
            # The run has to come FROM somewhere. Without this the PT is drawn
            # on the bus, the reference run is drawn under the relays, and
            # nothing joins the two -- the source cubicle reaches its own run
            # through its relay riser like any feeder, so the run has no source
            # at all. Which anchor the source riser lands on is the bus's own
            # business: the reference run climbs to the PT, the differential
            # run climbs to the relay that owns it.
            anchor = anchors.get(bus.get("source_anchor", "pt"))
            if anchor is not None:
                riser = {
                    "y_from": y,
                    "y_to": anchor,
                    "dx": bus.get("source_dx", bus["riser_dx"]),
                }
                # Broken where it passes the main bus so the crossing does not
                # read as a tap -- but only when it actually passes it. A riser
                # that stays below the bus gets no phantom break.
                lo, hi = sorted((y, anchor))
                if lo < bus_y < hi:
                    riser["gaps"] = [[round(bus_y - 0.05, 4),
                                      round(bus_y + 0.05, 4)]]
                wiring.setdefault("risers", []).append(riser)

        # After reflection the stored relay_bottom is the edge facing this
        # deck's run, so the same anchor is correct either way up.
        reach = anchors.get(bus.get("reach_anchor", "relay_bottom"))
        if bus.get("reaches") and present(bus["reaches"], row, std)                 and reach is not None:
            riser = {"y_from": y, "y_to": reach, "dx": bus["riser_dx"]}
            # A relay's supply is fused where it leaves the run, so the fuse
            # sits ON this riser rather than beside it: the riser breaks for
            # the body and the block fills the gap. Placed at the midpoint,
            # which is the only elevation that stays sensible when the run or
            # the relay box moves.
            fuse = bus.get("reach_fuse")
            if fuse:
                mid = round((y + reach) / 2, 4)
                half = fuse.get("half_height", 0.1875)
                riser["gaps"] = [[round(mid - half, 4), round(mid + half, 4)]]
                spec = {"kind": "block", "block": fuse["block"],
                        "dx": bus["riser_dx"], "y": mid,
                        "rotation": 0, "series": False,
                        "attribs": dict(fuse.get("attribs") or {})}
                if fuse.get("attrib_pos"):
                    spec["attrib_pos"] = fuse["attrib_pos"]
                extra[name + "_fuse"] = spec
            wiring.setdefault("risers", []).append(riser)

    # CT secondaries. A CT is drawn with its ratio and its class, and then the
    # sheet says nothing about where its output goes -- which is the one thing
    # a protection engineer reads the drawing for. Each CT that has a secondary
    # declares its destination on the stack entry: its own relay, or the name
    # of a lineup-wide run.
    for entry in std["stack"]:
        sec = entry.get("secondary")
        if not sec or entry["name"] not in anchors:
            continue
        y = anchors[entry["name"]]
        spur = {"y": y, "dx_from": sec["dx_from"], "dx_to": sec["dx"],
                "_note": f"{entry['name']} secondary"}
        if sec["to"] == "relay":
            target = anchors.get("relay_bottom")
        elif sec["to"] in std.get("beside", {}):
            # A device sitting at the secondary's own elevation -- the test
            # switch. The lead runs into it and stops; there is nothing to
            # climb to, so no riser.
            if present(std["beside"][sec["to"]]["when"], row, std):
                wiring.setdefault("spurs", []).append(spur)
            continue
        else:
            bus_name, target = bus_ys.get(sec["to"], (None, None))
            # Tagged with the run it serves so that a lineup which drops that
            # run -- differential CTs but no differential relay to end at --
            # drops the spur with it instead of leaving a stub in mid-air.
            spur["bus"] = bus_name
        if target is None:
            continue
        wiring.setdefault("spurs", []).append(spur)
        wiring.setdefault("risers", []).append(
            {"y_from": y, "y_to": target, "dx": sec["dx"]})

    return wiring, extra


def build_config(std, job, rows):
    """Turn the standard plus the intake rows into a build_sld config."""
    label = std["sheet"]["bus_label"]
    for field in LABEL_FIELDS:
        label = label.replace("{" + field + "}", job.get(field, ""))
    label = ", ".join(p for p in label.split(", ") if p.strip())

    sheet = {k: v for k, v in std["sheet"].items() if not k.startswith("_")}
    sheet["bus_label"] = label

    cfg = {
        "_comment": f"Generated from an intake workbook for job {job.get('job', '?')}. "
                    f"Do not hand-edit -- edit the workbook or standard.json and rebuild.",
        "sheet": sheet,
        "text": std["text"],
        "roles": {},
        "archetypes": {},
        "control": {k: v for k, v in std.get("control", {}).items()
                    if not k.startswith("_")},
    }

    bus_y = std["sheet"]["bus_y"]
    decks = std.get("decks", {})

    for row in rows:
        deck = (row.get("deck") or "single").strip().lower()
        if deck not in decks:
            raise SystemExit(f"unit {row['unit']}: unknown deck {deck!r} "
                             f"(expected one of {', '.join(sorted(decks))})")

        # Two rows of a two-high cubicle share a unit number, so the deck has to
        # be part of the key or the upper deck would overwrite the lower.
        key = f"u{row['unit']}" + (f"_{deck}" if deck != "single" else "")
        is_tie = "tie" in std and present(std["tie"]["when"], row, std)
        if is_tie:
            roles, order, anchors, branch_roles = tie_unit(std, row, key, bus_y)
        else:
            roles, order, anchors, branch_roles = stack_unit(std, row, key)

        arch = {
            "description": row.get("description", ""),
            "_from": f"intake row {row['unit']}, deck {deck}"
                     + (f", schema_type {row['schema_type']}" if row.get("schema_type") else ""),
            "roles": order,
            # Columns that shaped this archetype without being printed by any
            # role. They reach the drawing as much as a ratio does -- 'deck'
            # decides which way up it is built, 'tie' decides whether it is a
            # stack at all -- but nothing downstream could tell, so preflight
            # reported them as columns you can type into all day.
            "_columns": ["deck"] + (["tie"] if is_tie else []),
        }

        if is_tie:
            arch["bus_gaps"] = std["tie"]["bus_gaps"]
            arch["text_overrides"] = json.loads(
                json.dumps(std["tie"].get("text_overrides", {})))
            # The tie's normal position is the one rating a tie has that no
            # other cubicle does, and the reference prints it as the spec
            # block's last line rather than as a note of its own.
            arch["text_overrides"].setdefault("spec_block", {})["suffix"] =                 row[std["tie"]["when"]].strip()
            arch["_tie"] = "Bus tie: the breaker sits in the bus, which is why "                            "the bus breaks here and the lineup has two of them."

        if decks[deck].get("mirror"):
            anchors = reflect(roles, anchors, bus_y, skip=branch_roles)
            overrides = {
                name: {"y": round(2 * bus_y - std["text"][name]["y"] + dy, 4)}
                for name, dy in decks[deck].get("text_shift", {}).items()
                if name in std["text"]
            }
            # The cubicle header belongs to the cubicle, not to a deck. Both
            # rows would otherwise print it at the same elevation and the two
            # tags would land on top of each other; the upper deck's own tag
            # and ratings are in its spec block, beside its breaker.
            for name in decks[deck].get("hide_text", []):
                overrides[name] = {"hide": True}
            arch["text_overrides"] = overrides
            arch["_mirrored"] = "Upper deck: the lower deck reflected about the bus."

            # draw_conductor always walks downward, so an upper deck is drawn by
            # handing it the far end: the run comes DOWN from the cable entry to
            # the bus. Without this the deck defaults to the below-bus case and
            # every symbol on it is left floating.
            tops = [s["gap"][0] for n, s in roles.items()
                    if s.get("series") and "gap" in s and n not in branch_roles]
            if tops:
                arch["conductor_top"] = max(tops)

        cfg["roles"].update(roles)
        cfg["archetypes"][key] = arch

        mirrored = bool(decks[deck].get("mirror"))
        wiring, extra = control_wiring(std, row, anchors, mirrored, bus_y)
        if wiring:
            cfg["archetypes"][key]["control_wiring"] = wiring
        # Devices that belong to the control wiring rather than to the stack.
        # They are built after reflect() has run and from already-mirrored
        # elevations, so they must not be reflected again -- which is also why
        # they carry no rotation to flip.
        for rname, spec in extra.items():
            full = f"{key}_{rname}"
            cfg["roles"][full] = spec
            arch["roles"].append(full)
        row["archetype"] = key

    # A mirrored deck needs its run declared at the mirrored elevation. Only
    # added when a unit actually sits up there, so a single-high lineup keeps
    # exactly the buses it had before.
    if any((r.get("deck") or "single").strip().lower() in
           {d for d, v in decks.items() if v.get("mirror")} for r in rows):
        for bus in list(cfg["control"].get("buses", [])):
            upper = dict(bus)
            upper["name"] = bus["name"] + "_upper"
            upper["y"] = round(2 * bus_y - bus["y"], 4)
            cfg["control"]["buses"].append(upper)

    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("intake", help="intake workbook (.xlsx)")
    ap.add_argument("out_dxf")
    ap.add_argument("--standard", default=str(STANDARD))
    ap.add_argument("--library", default=str(LIBRARY))
    ap.add_argument("--bus", help="restrict to one bus")
    ap.add_argument("--keep", action="store_true",
                    help="keep the generated config and CSV (default: they are kept anyway)")
    args = ap.parse_args()

    std = json.load(open(args.standard, encoding="utf-8"))
    job, rows, columns = read_intake.read(args.intake)
    cfg = build_config(std, job, rows)

    out = Path(args.out_dxf)
    cfg_path = out.with_name(out.stem + "_config.json")
    csv_path = out.with_name(out.stem + "_units.csv")

    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    fields = columns + ["archetype"] if "archetype" not in columns else columns
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"job {job.get('job', '?')}: {len(rows)} units")
    for row in rows:
        devices = [n.split("_", 1)[1] for n in cfg["archetypes"][row["archetype"]]["roles"]
                   if not n.endswith(("_phase", "_riser"))]
        print(f"  unit {row['unit']:>3} {row.get('tag', ''):<8} {len(devices)} devices: "
              f"{', '.join(devices)}")
    print(f"\nwrote {cfg_path.name}, {csv_path.name}")

    cmd = [sys.executable, str(HERE / "build_sld.py"), str(csv_path), str(out),
           "--config", str(cfg_path), "--library", args.library]
    if args.bus:
        cmd += ["--bus", args.bus]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

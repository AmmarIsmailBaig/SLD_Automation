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


def present(when, row):
    """Is the device named by this 'when' rule called for by the unit row?"""
    if when == "always":
        return True
    if when == "any_ct":
        return any(row.get(c, "").strip() for c in ("ct_protection", "ct_metering", "ct_ground"))
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
        if not present(entry["when"], row):
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

        if entry.get("phase_note"):
            note = dict(std["phase_note"])
            note["y"] = round(y + note.pop("dy"), 4)
            note.pop("_note", None)
            emit(entry["name"] + "_phase", note)
        if entry["name"].startswith("ct_"):
            ct_ys.append(y)

    # --- pinned devices, at a fixed elevation ---------------------------
    for entry in std["pinned"]:
        if not present(entry["when"], row):
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
        if not present(entry["when"], row):
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
        emit(name, spec)

    # --- the bus PT branch, above the bus -------------------------------
    branch = std["branch"]
    branch_roles = set()
    if present(branch["when"], row):
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
            if present(bus["source"], row):
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
    """
    wiring = {}
    for bus in std.get("control", {}).get("buses", []):
        y = round(2 * bus_y - bus["y"], 4) if mirrored else bus["y"]
        name = bus_name_for(bus, mirrored)
        if present(bus["source"], row):
            wiring.setdefault("joins", []).append(name)
            # The run has to come FROM somewhere. Without this the PT is drawn
            # on the bus, the reference run is drawn under the relays, and
            # nothing joins the two -- the source cubicle reaches its own run
            # through its relay riser like any feeder, so the run has no source
            # at all. The secondary drops from the PT to the run, broken where
            # it passes the main bus so the crossing does not read as a tap.
            if "pt" in anchors:
                wiring.setdefault("risers", []).append({
                    "y_from": y,
                    "y_to": anchors["pt"],
                    "dx": bus.get("source_dx", bus["riser_dx"]),
                    "gaps": [[round(bus_y - 0.05, 4), round(bus_y + 0.05, 4)]],
                })
        # After reflection the stored relay_bottom is the edge facing this
        # deck's run, so the same anchor is correct either way up.
        if present(bus["reaches"], row) and "relay_bottom" in anchors:
            wiring.setdefault("risers", []).append({
                "y_from": y,
                "y_to": anchors["relay_bottom"],
                "dx": bus["riser_dx"],
            })
    return wiring


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
        roles, order, anchors, branch_roles = stack_unit(std, row, key)

        arch = {
            "description": row.get("description", ""),
            "_from": f"intake row {row['unit']}, deck {deck}"
                     + (f", schema_type {row['schema_type']}" if row.get("schema_type") else ""),
            "roles": order,
        }

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
        wiring = control_wiring(std, row, anchors, mirrored, bus_y)
        if wiring:
            cfg["archetypes"][key]["control_wiring"] = wiring
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

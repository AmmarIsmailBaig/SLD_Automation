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
        spec["y"] = entry["y"]
        terminals(spec, entry["y"])
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
            emit(entry["name"] + "_riser", {
                "kind": "lead",
                "points": [[0.0, prev_top], [0.0, round(y - down, 4)]],
                "layer": std["conductor"]["layer"],
            })
            emit(entry["name"], spec)
            prev_top = round(y + up, 4)

    return roles, order, anchors


def control_wiring(std, row, anchors):
    """How this unit meets the lineup-wide control runs.

    Participation is declared per unit rather than per type, so the PT
    reference run reaches whichever cubicles carry a relay -- and originates
    wherever the PT happens to be -- instead of being fixed by an archetype.
    """
    wiring = {}
    for bus in std.get("control", {}).get("buses", []):
        if present(bus["source"], row):
            wiring.setdefault("joins", []).append(bus["name"])
        if present(bus["reaches"], row) and "relay_bottom" in anchors:
            wiring.setdefault("risers", []).append({
                "y_from": bus["y"],
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

    for row in rows:
        deck = row.get("deck", "").strip().lower()
        if deck in ("upper", "lower"):
            raise SystemExit(
                f"unit {row['unit']}: deck {deck!r} is not supported yet.\n"
                "  Stacking makes two-high possible, but the drop from the bus to the\n"
                "  lower deck's own origin is a measured number this standard does not\n"
                "  have. One two-high reference drawing supplies it."
            )
        key = f"u{row['unit']}"
        roles, order, anchors = stack_unit(std, row, key)
        cfg["roles"].update(roles)
        cfg["archetypes"][key] = {
            "description": row.get("description", ""),
            "_from": f"intake row {row['unit']}"
                     + (f", schema_type {row['schema_type']}" if row.get("schema_type") else ""),
            "roles": order,
        }
        wiring = control_wiring(std, row, anchors)
        if wiring:
            cfg["archetypes"][key]["control_wiring"] = wiring
        row["archetype"] = key

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

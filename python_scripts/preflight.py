#!/usr/bin/env python3
"""
Pre-issue checks for a switchgear SLD unit table.

Run this before build_sld.py on any new job. It catches the failure modes that
produce a drawing which builds cleanly and is still wrong -- the ones no error
message will ever tell you about, because nothing crashed.

    python preflight.py 8712_units.csv
    python preflight.py 8712_units.csv --config sld_config_vendorX.json

Exit code 0 = clean or notes only, 1 = at least one WARN, 2 = at least one FAIL.
Standard library only, so it runs anywhere build_sld.py does and then some.
"""

import argparse, csv, json, re, sys
from collections import Counter, defaultdict

OK, NOTE, WARN, FAIL = "ok", "note", "WARN", "FAIL"
RANK = {OK: 0, NOTE: 0, WARN: 1, FAIL: 2}
findings = []

def report(level, check, message, fix=None):
    findings.append((level, check, message, fix))


# ---------------------------------------------------------------- inspection
def column_wiring(cfg, columns):
    """Work out how each CSV column reaches the drawing, if it does at all.

    A column is live if build_sld.py reads it by name, or if some role's
    'attribs' map or 'text' field in the config points at it. Anything else is
    a column you can type into all day with no effect on the output.
    """
    # Read directly by annotate_unit() / the layout pass.
    direct = {"unit", "bus", "tag", "description", "archetype",
              "voltage", "amp_rating", "ka_rating", "relay", "destination"}
    via = {c: "read directly by build_sld.py" for c in columns if c in direct}

    for rname, role in cfg.get("roles", {}).items():
        for tag, src in (role.get("attribs") or {}).items():
            if isinstance(src, str) and not src.startswith("@") and src in columns:
                via.setdefault(src, f"role '{rname}' attribute {tag}")
        txt = role.get("text")
        if isinstance(txt, str) and not txt.startswith("@") and txt in columns:
            via.setdefault(txt, f"role '{rname}' note text")

    # A column can also be wired to a text style that does not exist, in which
    # case build_sld.py's guard silently skips it. arrester is the known case:
    # its rating lives in the ARRESTER block as static MTEXT.
    if "arrester" in via and "arrester_label" not in cfg.get("text", {}):
        via.pop("arrester")
    return via


def archetype_span(cfg, name):
    """Widest left-to-right extent of an archetype's devices, in drawing units."""
    lo = hi = 0.0
    for rname in cfg["archetypes"][name].get("roles", []):
        role = cfg.get("roles", {}).get(rname, {})
        dx = role.get("dx")
        if isinstance(dx, (int, float)):
            lo, hi = min(lo, dx), max(hi, dx)
    for wiring in (cfg["archetypes"][name].get("control_wiring") or {}).values():
        for run in (wiring if isinstance(wiring, list) else []):
            if not isinstance(run, dict):
                continue                      # e.g. bus_dx, a bare number
            for key in ("dx", "dx_from", "dx_to"):
                v = run.get(key)
                if isinstance(v, (int, float)):
                    lo, hi = min(lo, v), max(hi, v)
    return lo, hi


def required_columns(cfg, archetype):
    """Columns this archetype actually consumes through its roles."""
    need = set()
    for rname in cfg["archetypes"].get(archetype, {}).get("roles", []):
        role = cfg.get("roles", {}).get(rname, {})
        for src in (role.get("attribs") or {}).values():
            if isinstance(src, str) and not src.startswith("@"):
                need.add(src)
        txt = role.get("text")
        if isinstance(txt, str) and not txt.startswith("@"):
            need.add(txt)
    return need


# -------------------------------------------------------------------- checks
def check_archetypes(cfg, rows):
    known = set(cfg.get("archetypes", {}))
    bad = sorted({r["archetype"] for r in rows
                  if (r.get("archetype") or "").strip() not in known})
    if bad:
        report(FAIL, "archetype", f"unit table names {len(bad)} archetype(s) the "
               f"config does not define: {', '.join(bad)}",
               "add them to sld_config.json (README section 2), or fix the spelling")
    else:
        report(OK, "archetype", f"all rows use one of the {len(known)} defined archetypes")


def check_dead_columns(cfg, rows, columns):
    via = column_wiring(cfg, columns)
    dead = [c for c in columns if c not in via]
    if not dead:
        report(OK, "columns", "every column reaches the drawing")
        return
    hints = {
        "arrester": "the rating is baked into the ARRESTER block artwork; "
                    "editing the CSV changes nothing",
    }
    for col in dead:
        vals = {(r.get(col) or "").strip() for r in rows} - {""}
        detail = (f"{len(vals)} distinct value(s) entered" if vals else "empty in every row")
        level = WARN if len(vals) > 1 else NOTE
        report(level, "columns",
               f"column '{col}' does not reach the drawing -- {detail}",
               hints.get(col, f"no role's 'attribs' map or 'text' field points at '{col}', "
                              f"and build_sld.py does not read it"))


def check_required_fields(cfg, rows):
    holes = defaultdict(list)
    for r in rows:
        arch = (r.get("archetype") or "").strip()
        if arch not in cfg.get("archetypes", {}):
            continue
        for col in sorted(required_columns(cfg, arch)):
            if not (r.get(col) or "").strip():
                holes[col].append(r.get("unit") or "?")
    if not holes:
        report(OK, "blanks", "no archetype is missing a value it consumes")
        return
    for col, units in sorted(holes.items()):
        report(WARN, "blanks",
               f"'{col}' is blank on unit(s) {', '.join(units)} whose archetype uses it",
               "that label will render empty on the sheet")


def check_bus_label(cfg, rows):
    label = cfg.get("sheet", {}).get("bus_label", "")
    stated = re.findall(r"(\d{3,5})\s*A\b", label)
    actual = Counter((r.get("amp_rating") or "").strip()
                     for r in rows if (r.get("amp_rating") or "").strip())
    if not stated:
        report(NOTE, "bus label", "no ampere rating found in sheet.bus_label to cross-check")
        return
    nums = {re.sub(r"[^\d]", "", a) for a in actual}
    if nums and not (set(stated) & nums):
        report(WARN, "bus label",
               f"bus label says {stated[0]}A but the units are rated "
               f"{', '.join(sorted(actual))}",
               "sheet.bus_label is fixed text -- edit it in the config")
    else:
        report(OK, "bus label", f"bus label rating {stated[0]}A is consistent with the units")

    for token in ("BIL", "kV"):
        if token not in label:
            report(NOTE, "bus label", f"bus label has no '{token}' -- confirm that is intended")


def check_pitch(cfg, rows):
    pitch = cfg.get("sheet", {}).get("pitch")
    if not isinstance(pitch, (int, float)):
        report(FAIL, "pitch", "sheet.pitch is missing or not a number")
        return
    used = sorted({(r.get("archetype") or "").strip() for r in rows}
                  & set(cfg.get("archetypes", {})))
    widest, span = None, 0.0
    for arch in used:
        lo, hi = archetype_span(cfg, arch)
        if hi - lo > span:
            widest, span = arch, hi - lo
    if span > pitch:
        report(FAIL, "pitch",
               f"'{widest}' spans {span:.3f} but sheet.pitch is {pitch:.3f} -- cubicles will overlap",
               f"raise sheet.pitch to at least {span:.3f}")
    elif span > pitch * 0.98:
        report(NOTE, "pitch", f"widest unit '{widest}' uses {span:.3f} of the {pitch:.3f} pitch")
    else:
        report(OK, "pitch",
               f"pitch {pitch:.3f} clears the widest unit '{widest}' ({span:.3f}) "
               f"by {pitch - span:.3f}")


def check_tags(rows):
    tags = [(r.get("tag") or "").strip() for r in rows if (r.get("tag") or "").strip()]
    dupes = [t for t, n in Counter(tags).items() if n > 1]
    if dupes:
        report(FAIL, "tags", f"duplicate device tag(s): {', '.join(sorted(dupes))}",
               "two cubicles cannot carry the same tag")
    else:
        report(OK, "tags", f"{len(tags)} device tags, all unique")

    relays = [(r.get("relay") or "").strip() for r in rows if (r.get("relay") or "").strip()]
    rd = [t for t, n in Counter(relays).items() if n > 1]
    if rd:
        report(WARN, "tags", f"the same relay label appears on more than one unit: {rd[0][:44]}...",
               "usually a copy-paste left over from the template row")


def check_order(rows):
    """Cubicle position comes from row order. Flag a table whose unit numbers
    disagree with it -- legal, but almost always a mistake."""
    for bus in sorted({(r.get("bus") or "").strip() for r in rows}):
        nums = []
        for r in rows:
            if (r.get("bus") or "").strip() != bus:
                continue
            try:
                nums.append(int(re.sub(r"[^\d]", "", r.get("unit") or "")))
            except ValueError:
                nums.append(None)
        seq = [n for n in nums if n is not None]
        if seq != sorted(seq):
            report(WARN, "row order",
                   f"bus {bus}: unit numbers are not ascending in row order",
                   "cubicles are placed in ROW order -- the sheet will not match the numbering")
        else:
            report(OK, "row order", f"bus {bus}: {len(seq)} cubicles, numbering matches row order")


def check_control_buses(cfg, rows):
    for bus in (cfg.get("control", {}).get("buses") or []):
        name = bus.get("name", "?")
        if name != "diff_ct":
            continue
        # Identify tie and PT units from what the archetype actually draws, not
        # from its name -- 'pt_riser_tie' is a PT, not a tie.
        def has_role(arch, prefix):
            roles = cfg.get("archetypes", {}).get(arch, {}).get("roles", [])
            return any(r.startswith(prefix) for r in roles)

        ties = [r for r in rows if has_role(r.get("archetype") or "", "breaker_tie")]
        pts = [r for r in rows if has_role(r.get("archetype") or "", "bus_pt")]
        if ties and pts:
            report(OK, "bus diff", "lineup has both a tie and a PT unit -- the "
                   "bus-differential run will be drawn")
        else:
            missing = "tie unit" if not ties else "PT unit"
            report(NOTE, "bus diff",
                   f"no {missing} in this lineup -- the bus-differential run will be omitted",
                   "expected on a single-bus job; check it if the job has two buses")


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("units", help="unit table CSV for the new job")
    ap.add_argument("--config", default="sld_config.json")
    ap.add_argument("--bus", help="check only this bus")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    with open(args.units, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        rows = [r for r in reader if any((v or "").strip() for v in r.values())]

    # The intake workbook calls the column 'schema_type' -- the engineers' word --
    # while sld_config.json and build_sld.py call it 'archetype'. Accept either.
    if "schema_type" in columns and "archetype" not in columns:
        for r in rows:
            r["archetype"] = r.get("schema_type", "")
        columns = [("archetype" if c == "schema_type" else c) for c in columns]
    if args.bus:
        rows = [r for r in rows if (r.get("bus") or "").strip() == args.bus]

    print(f"\n  {args.units}  ->  {len(rows)} cubicles, {len(columns)} columns")
    print(f"  config: {args.config}\n")

    check_archetypes(cfg, rows)
    check_pitch(cfg, rows)
    check_bus_label(cfg, rows)
    check_dead_columns(cfg, rows, columns)
    check_required_fields(cfg, rows)
    check_tags(rows)
    check_order(rows)
    check_control_buses(cfg, rows)

    width = max(len(c) for _, c, _, _ in findings)
    worst = 0
    for level, check, message, fix in sorted(findings, key=lambda f: -RANK[f[0]]):
        mark = {OK: "  ok ", NOTE: "  .. ", WARN: " WARN", FAIL: " FAIL"}[level]
        print(f"{mark}  {check:<{width}}  {message}")
        if fix and level in (WARN, FAIL):
            print(f"{'':6}  {'':<{width}}  -> {fix}")
        worst = max(worst, RANK[level])

    verdict = {0: "clean -- nothing blocking",
               1: "check the WARN lines before issuing",
               2: "FAIL -- fix before building"}[worst]
    print(f"\n  {verdict}\n")
    return worst


if __name__ == "__main__":
    sys.exit(main())

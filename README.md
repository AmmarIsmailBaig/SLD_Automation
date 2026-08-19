# Switchgear SLD Generator

Generates IOM-style medium-voltage switchgear single line diagrams. Each cubicle
is composed from a shared symbol library at run time, so a new job is data, not a
new hand-drawn sheet.

There are **two paths in, and they are for different jobs.**

| | Input | Use it for |
|---|---|---|
| **Intake** | an Excel workbook | **a new job.** The devices are stacked from what the row states. |
| **Measured config** | `units.csv` + a hand-measured `sld_config*.json` | reproducing a specific reference sheet, to the thousandth. |

New work goes through the intake path. The measured configs exist because
8508, 8513, 8372 and 8478 were each measured off a real drawing and reproduce
it exactly; that is worth keeping and is not worth redoing.

## Quick start

```bash
pip install ezdxf openpyxl matplotlib

# a new job, from a workbook
python python_scripts/build_intake.py excel/intake_template.xlsx out.dxf
python python_scripts/validate_sld.py out.dxf --config out_config.json
python python_scripts/preview.py out.dxf out.png        # PNG check, no AutoCAD

# reproducing a measured reference sheet
python python_scripts/build_sld.py excel/units.csv sld_a.dxf \
    --config python_scripts/sld_config.json \
    --library cad_files/symbol_library.dxf --bus A
```

`build_intake.py` needs no flags — it finds `standard.json` and the symbol
library relative to itself, so it runs from any directory.

## Files

| File | Role |
|---|---|
| `excel/intake_template.xlsx` | **The job sheet. Copy it, fill it in.** |
| `python_scripts/standard.json` | The house drafting standard: frame, device order, gaps, symbols. One file, every job. |
| `python_scripts/build_intake.py` | Workbook → stacked config → DXF. |
| `python_scripts/read_intake.py` | Reads the workbook's Project and Units tabs. |
| `python_scripts/build_sld.py` | The renderer. Both paths end here. |
| `python_scripts/sld_config*.json` | Measured configs, one per reference sheet. |
| `cad_files/symbol_library.dxf` | The real ACADE blocks plus red placeholder stubs. |
| `python_scripts/validate_sld.py` | Reference-free acceptance check on a built DXF. |
| `python_scripts/preflight.py` | Checks a unit table before building. |
| `python_scripts/preview.py` | Renders a DXF to PNG. |
| `python_scripts/measure.py` | Dumps a reference drawing's entities in an x/y window — how a config gets measured. |
| `python_scripts/extract_symbols.py` | Harvests real blocks out of a project drawing into the library. |
| `tests/` | Golden-file regression tests — see [tests/README.md](tests/README.md). |
| `python_scripts/schema_types.json` | **Historical.** The cubicle-type catalogue, superseded by the stack model. Kept because it records which types TPPES specified and which were reverse-engineered off an issued drawing; nothing reads it. |

## Regression tests

```bash
pip install pytest
python -m pytest tests/ -q
```

Eight lineups, twenty-four tests. Failures name which entities moved and by how
much. Intended changes are re-recorded with `python tests/regen_golden.py`, but
read the diff first — a golden regenerated without looking records the
regression instead of catching it.

---

# The intake path

## The one rule

> **A device is drawn if and only if its cell has a value.**

Everything else follows from that. A feeder with one CT has one CT because the
`ct_metering` cell is empty. Fill it and there are two. Nothing is inherited
from a reference drawing, because nothing is copied from one.

This is worth stating because the earlier design did the opposite. Devices sat
at absolute sheet elevations, so a cubicle differing by one device needed a
whole new archetype at new elevations — which is how "two CTs" ended up inside
type *names*. `gen_feeder` carried a metering CT because the 8508 drawing it
was copied from had one, and clearing the cell could not remove it.

## The sheet

Copy `excel/intake_template.xlsx`. Two tabs to fill:

**Project** — job number, title, voltage, bus rating. The bus label is built
from these, so it cannot disagree with the job.

**Units** — one row per cubicle, nineteen columns:

| | |
|---|---|
| structure | `unit` `bus` `deck` |
| identity | `tag` `description` `destination` |
| breaker | `voltage` `amp_rating` `ka_rating` |
| protection | `relay` `relay_fuse` `bus_differential` `ct_protection` `ct_differential` `ct_metering` `ct_ground` `ct_class` |
| PT | `pt` `pt_primary_fuse` |

`amp_rating` does double duty: **blank means no breaker.** That is how a deck
holds only a bus PT — nothing to rack out, no cable to terminate.

Four CT rows, one per job the CT does. A dual-core CT — `1200/5/5A
5P20/5P20`, one piece of iron in the panel schedule — is **two symbols** on
the single line, because its two cores feed different circuits: the feeder
relay and the bus differential. That is why `ct_differential` is its own row
rather than a wider label on `ct_protection`.

`bus_differential` is filled on **one** cubicle: the one whose relay is the
differential relay. Every `ct_differential` in the lineup is wired to it. Leave
it blank everywhere and the differential CTs are still drawn — there is simply
no run, because there is nothing for them to run to.

`ct_class` is the one optional column. Each CT's ratio comes from its own cell;
its accuracy class comes from `ct_class` when the job states one and from the
house default for that CT's job when it does not — 5P20 for protection and
differential, 0.3B1.8 for metering, GND CT. One column rather than three because a cubicle's CTs almost
always share a class; a lineup that genuinely mixes them leaves it blank and
takes the defaults. Anywhere in `standard.json` a `column|@literal` chain does
the same thing: the job's value if there is one, the house value otherwise.

There is no unit-type column. The filled cells describe the cubicle, and a type
column would only invite someone to set it and expect devices to follow.

## The stack

Devices hang below the bus in a fixed order, each dropping its declared gap
below the one above. A device the row does not call for is skipped and the
stack closes up.

```
        bus ═══════════════════════════
 16.144  drawout (upper)
 15.622  BREAKER            ── 0.522     RELAY  14.715  ◄ beside, not in series
 14.252  drawout (lower)    ── 0.463
 12.007  CT (protection)
 10.308  CT (differential)               ← only if ct_differential is filled
  9.034  cable exit
```

Two things are computed rather than placed:

**The dashed CT compartment is sized from the CTs actually present** — 0.772
tall around one, 2.471 around two. On a two-CT unit that lands at 12.378..9.907
against the 12.465..9.912 drawn by hand on 8508, so the rule reproduces the
reference without inheriting its number.

**The cable exit is pinned to the cubicle floor**, so exits line up across the
lineup — but a unit carrying all three CTs stacks past that point, and the pin
gives way rather than leaving a CT below its own termination.

## Two-high cubicles

Set `deck` to `upper` / `lower` on two rows sharing a unit number.

The upper deck is **the lower deck reflected about the bus**: same devices,
same gaps, running upward, taking its cable out through the roof. There is no
second set of elevations, so the decks cannot drift apart — change a gap and
both move.

Four things do not reflect, each for its own reason:

- **the bus PT branch**, which already stacks upward off the bus; mirroring
  drove it back down between the bus and the breaker
- **symbols carrying attributes**, because turning one over turns its text over
  with it and the ratios print upside down. Only the drawout contacts, which
  actually point somewhere, are flipped
- **the cubicle header**, which describes the cubicle and would otherwise print
  twice at the same elevation. The upper deck's tag is in its spec block
- **the conductor direction** — an upper deck sets `conductor_top`, because
  `draw_conductor` always walks downward and is handed the far end

## The bus PT

Whichever row states a `pt` gets the branch. It **taps upward off the bus**, so
neither the PT nor its primary fuse sits in the path between the bus and the
breaker. (The secondary fuse belongs on the three-line diagram, not here.)

Its secondary feeds a reference run spanning the lineup, dropping into every
unit that has a relay. Both ends are derived from the sheet — the source is
wherever `pt` is filled, the destinations are wherever `relay` is filled — so
moving the PT to another cubicle re-routes it.

Each relay's supply is **fused where it leaves the run** — the riser breaks
for the fuse body and the block fills the gap, at the midpoint of the drop,
which is the one elevation that stays sensible when either the run or the relay
box moves. The break is the VFU1 body's own height rather than a number kept in
step with it. `relay_fuse` sets the rating per cubicle; blank takes 8513's 6A.

Two offsets are not interchangeable, and both were wrong once:

- the run's `end_dx` reaches the **last riser**, not the last centreline. At 0
  the final unit's drop hung in mid-air just short of the run
- the PT secondary drops on its own `source_dx`, clear of the relay boxes. It
  runs the full height from the PT to the run, and at the risers' offset it
  went straight through the source cubicle's own relay box

## Where a CT's output goes

A CT drawn with a ratio and nothing else says what it measures and not what it
protects, which is the thing a protection engineer opens the sheet for. So each
CT names its destination in `standard.json`, and the secondary is drawn to it:

| CT | leaves | goes to |
|---|---|---|
| `ct_protection` | upper terminal, +0.249 | its own cubicle's relay box |
| `ct_differential` | lower terminal, −0.249 | the lineup-wide `bus_differential` run |
| `ct_metering` | lower terminal, −0.249 | a test switch, beside the CT at the same elevation |

The test switch — a small crossed square, 8513's symbol — is where a technician
shorts the CT and injects current. The lead runs into it and stops, which is
why that destination takes no riser; the switch hangs off the CT's own terminal
rather than a sheet elevation, so it follows the CT down as the stack grows.

Taking opposite terminals is what keeps the two leads from running one on top
of the other. Both offsets are the block's own — the VXF1CT secondary terminals
sit 0.249 either side of the insertion point, so a lead drawn from the symbol's
centre would land on the iron between them.

The differential run is the second lineup-wide run, and the mirror image of the
PT reference one: the reference run **starts** at the cubicle that has the PT
and drops into every cubicle with a relay, while the differential run **ends**
at the cubicle named by `bus_differential` and is fed by every cubicle with a
differential CT. Both ends of both runs come off the sheet, so moving either
relay to another cubicle re-routes the drawing rather than the drawing needing
an edit.

It runs at 12.7 — above every CT compartment, below the reference run at 13.21.
That band is the only one clear the full width of a lineup. The obvious place
is down among the CTs, which is where 8508 draws its differential run, and that
works there because the run spans two adjacent cubicles; one crossing a dozen
would be drawn straight through every compartment box on the way.

One run means one differential zone. A real two-zone scheme — 87B1 and 87B2
either side of the tie — is two entries in `control.buses` with two columns
behind them, not a change to the mechanism.

## Checking a sheet before you build

```bash
python python_scripts/preflight.py out_units.csv --config out_config.json
```

| Check | Catches |
|---|---|
| pitch | a unit wider than `sheet.pitch`, i.e. cubicles that will overlap |
| bus label | the ampere rating disagreeing with the units |
| columns | a column that reaches nothing, flagged louder if you edited it |
| blanks | a field left empty on a unit that consumes it |
| tags | duplicate device tags, and a relay label repeated across units |
| row order | unit numbers that don't ascend in row order |

Exit code 0 clean, 1 warnings, 2 must fix, so it can gate a build in a script.

One caveat: run against a *generated* config it cannot tell "column never
wired" from "column wired but unused in this job" — the generated config only
contains roles for devices actually present. `ct_metering` reports as dead on a
lineup that has no metering CTs.

## Checking the drawing afterwards

```bash
python python_scripts/validate_sld.py out.dxf --config out_config.json
```

Reports text collisions, tight clearances, symbols connected to nothing, and
geometry outside its cubicle. **It proves internal consistency, not fidelity to
how your shop draws.** A sheet can pass every check and still put the CT on the
wrong side.

## Changing the standard

`standard.json` holds everything that is a drafting convention rather than a
job fact. The two most likely to need a new job's numbers:

| | |
|---|---|
| `sheet.pitch` | cubicle width. Set to the widest unit in the new set. |
| `sheet.bus_y`, `cubicle_top/bottom` | the frame, symmetric about the bus so both decks get equal room. |

Adding a device is an entry in `stack` with a `when` naming its column, a
`gap`, and a role template. Nothing else changes — that is the whole cost of a
surge arrester, for instance, if a job ever specifies one.

---

# The measured-config path

For reproducing a reference sheet exactly. Devices sit at **absolute sheet
elevations** read off the original with `measure.py`, and a unit type is an
`archetypes` entry listing `roles`.

```bash
python python_scripts/measure.py cad_files/8508+A01-000-053.dxf 24.0 27.5
```

Subtract the cubicle's centreline x to get each `dx`; `y` copies straight
across. `--config` takes any file, so a different standard is a copy of the
config rather than a fork of the generator.

| Config | Reproduces |
|---|---|
| `sld_config.json` | 8508, 15kV single-high |
| `sld_config_8513.json` | 8513, 15kV two-high |
| `sld_config_8372.json` | 8372, 4.76kV |
| `sld_config_8478.json` | 8478, 38kV — the one config that declares a device **stack** rather than measured elevations, expanded by `stacker.py` |

## How a cubicle is built

```
units.csv          archetype name + per-unit data (tag, ratings, CT ratios)
      |
sld_config.json    archetype -> ordered list of device roles
      |            role -> block name, dx offset, sheet elevation, attributes
      |
symbol_library.dxf the block geometry itself
```

### Role kinds

| Kind | Draws |
|---|---|
| `block` | a library symbol, optionally with a CT `polarity_dot` |
| `breaker` | the drawout breaker rectangle |
| `box` | a compartment or label outline |
| `bubble` | an ANSI device bubble with leads derived from its own radius |
| `circle` | a bare circle (the Kirk key interlock) |
| `lead` | an explicit run of segments — leaders, tie-outs, panel drops |
| `note` | annotation tied to a device: `@literal` text or a column |
| `relay_functions` | a relay box with one circled ANSI code per function |
| `arc` | a winding arc (the 8372 PT coils) |

### Conductors

Drawn from the bus to the lowest series terminal, broken around each series
device's `gap`. Devices marked `"series": false` do not break it: **a CT
encircles the conductor rather than interrupting it**, which is why the
reference runs unbroken wire past both CTs.

### Attributes

`attribs` maps an ATTDEF tag to a column, or to a literal when prefixed `@`:

```json
"attribs": {"DESC1": "ct_protection", "DESC2": "@CT 5P20"}
```

Roles without `attribs` get blank, invisible attributes. This matters: ACADE
symbols carry ~17 ATTDEFs each, and any left unset would plot the ATTDEF's
default text (an unset `TAG1` on `VXF1CT` renders as `XF`).

---

# Both paths

## Control wiring

Drawn on layer `SLD_CONTROL` from four primitives:

- **Buses** — lineup-wide horizontal runs (`control` in the config)
- **Risers** — verticals carrying signals up to the relay box
- **Spurs** — horizontals from a device out to a riser
- **Hops** — arcs where one run crosses another it doesn't connect to

A unit joins a bus if its `control_wiring` has a riser terminating at, or a
spur running along, that elevation — or if it names the bus in `joins`, which
the PT units need because they feed through their *power* conductor and so have
no riser to infer from.

A bus needs **two** participants or it is dropped, which is how the
bus-differential run disappears from a lineup with no tie. The test is on
participants rather than endpoints: `diff_ct` is the one bus whose `start_dx`
and `end_dx` differ, so with a single unit on it an endpoint test leaves a
1.267-long stub ending in mid-air.

**Hops.** Two families, and using the wrong one is what reads as wrong to a
drafter:

| Arc | Meaning |
|---|---|
| 270°→90°, bulging +x | a **riser** detours around a horizontal it crosses |
| 0°→180°, bulging +y | a **horizontal** detours around a vertical it crosses |

Control runs never break a power conductor — the horizontal hops over it. A
conductor that *terminates* on a bus is a connection, not a crossing, and gets
a junction dot instead.

## Leads follow their symbol

Anything attached to a symbol derives from that symbol's own centre and radius,
never a fixed coordinate. Fixed offsets broke once: the 86 bubble's leads were
measured against the reference circle (r 0.176) and left a visible gap when the
real `DEVICE_BUBBLE` block (r 0.217) went in at a different offset.

## Stubs — replace before issuing

Two symbols have no real block and draw as red placeholders on layer
`SLD_STUB`: `SLD_STUB_GND_SWITCH` and `SLD_STUB_43LR`. `build_sld.py` prints
which are in use on every run. Neither is placed by any current unit type.

To replace one, draw the real block in any DXF and re-run the extractor with
that file as an extra source:

```bash
python python_scripts/extract_symbols.py cad_files/symbol_library.dxf \
    cad_files/8508+A01-000-053.dxf your_working_file.dxf
```

Later sources win on name collisions. Harvesting only ever **adds** — the
library holds `ARRESTER` and `DEVICE_BUBBLE`, drawn by hand and present in no
source drawing, and rebuilding from empty once deleted them silently.

## Not yet implemented

- **Title block, borders, ladder references.** Sheet furniture, not generated.
  This is the main thing between a built DXF and an issuable drawing.
- **Feeder terminal boxes** on the measured 8508 path — the reference ends each
  feeder's risers in a small box carrying a per-unit note.
- **Validator containment for off-centreline branches.** 8508 and 8513
  reproduce hand drawings that are correct by definition, and still report 41
  and 85 findings — transformer and panel branches hanging below the cubicle
  floor on the XFMR feeders. Findings against those two are validator gaps, not
  drawing defects.

## What does not transfer to a new standard

Device dimensions are genuinely fixed — the breaker span measures 1.600 on
8372 sheet 001 and 1.601 on sheet 002, independently drafted. What varies, and
what a new standard has to supply, is the frame, the device order and CT-side
convention, the annotation layout, and which symbol set is used (8372 draws
`VC01PJ_1-` and `1LCT1A` where 8508 draws `VCN1PJ` and `VXF1CT`).

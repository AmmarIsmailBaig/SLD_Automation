# Switchgear SLD Generator

Generates IOM-style medium-voltage switchgear single line diagrams from tabular
unit data. Each cubicle is composed from a shared symbol library at run time, so
adding a unit type is a config edit rather than a new hand-drawn block.

Modelled on `8508+A01-000-053.dxf` (Bus A, units 1-10) and
`8508+A01-000-054.dxf` (Bus B, units 11-19).

## Quick start

```bash
pip install ezdxf matplotlib

python build_sld.py units.csv sld_bus_a.dxf --bus A   # one sheet per bus
python build_sld.py units.csv sld_full.dxf            # whole lineup
python preview.py sld_bus_a.dxf preview.png           # PNG check, no AutoCAD
```

## Files

| File | Role |
|---|---|
| `units.csv` | One row per cubicle. **Edit in Excel.** |
| `sld_config.json` | Geometry (elevations, offsets, pitch) and archetype definitions. |
| `symbol_library.dxf` | The real ACADE blocks plus red placeholder stubs. |
| `build_sld.py` | The generator. |
| `extract_symbols.py` | One-time tool that builds `symbol_library.dxf` from a project drawing. |
| `preview.py` | Renders a DXF to PNG for quick checking. |
| `measure.py` | Dumps a reference drawing's entities in an x/y window — how new archetypes get measured. |

## Starting a new switchgear drawing

Three levels of effort, depending on how far the new job sits from this one.

### 1. Same equipment, different lineup — CSV only

```bash
cp units.csv 8712_units.csv             # edit in Excel
python build_sld.py 8712_units.csv 8712_bus_a.dxf --bus A
python preview.py 8712_bus_a.dxf 8712_a.png
```

Cubicle position comes from **row order**, not the `unit` number, so units need
not be contiguous or start at 1 — they only have to sort. `--bus` filters rows
and is what splits a lineup across sheets; omit it for one wide drawing.

Four things are **not** in the CSV and need checking against the new job:

| Where | What | Why it bites |
|---|---|---|
| `sheet.bus_label` | `17.5KV, 3150A\P3PH, 40KAIC, BIL 95kV\PBUS {bus}` | Fixed text. A 2000A lineup still gets labelled 3150A. |
| `sheet.pitch` | `3.610` | Cubicle width. Set it to the widest unit in the new set. |
| `sheet.bus_y`, `cubicle_top/bottom` | sheet elevations | Only if the new drawing's frame differs. |
| `ARRESTER` block | `L.A. 18kV 15.3kV MCOV` | Baked in as MTEXT, so identical on every unit — see below. |

The `arrester` column in `units.csv` is worth calling out separately: it is not
merely fixed, it is **dead**. `annotate_unit()` guards it behind a text style
named `arrester_label`, and no such style exists, so the column is read and
discarded. Type a different rating into it and nothing changes and nothing
complains. It is the only dead column in the table — everything else reaches
the drawing, `ct_protection` and `ct_metering` through the `attribs` maps in
the config rather than through Python.

#### Check it before you build

`preflight.py` runs all of the above automatically. Standard library only, so
it needs nothing that `build_sld.py` doesn't already have:

```bash
python preflight.py 8712_units.csv                 # add --bus A to check one sheet
python preflight.py 8712_units.csv --config sld_config_vendorX.json
```

It reports:

| Check | Catches |
|---|---|
| archetype | a name the config doesn't define — usually a typo |
| pitch | an archetype wider than `sheet.pitch`, i.e. cubicles that will overlap |
| bus label | the ampere rating in `sheet.bus_label` disagreeing with the units |
| columns | any column that reaches nothing, flagged louder if you edited it |
| blanks | a field left empty on a unit whose archetype consumes it |
| tags | duplicate device tags, and a relay label repeated across units |
| row order | unit numbers that don't ascend in row order |
| bus diff | whether the bus-differential run will be drawn or omitted |

Exit code 0 clean, 1 warnings, 2 must fix — so it can gate a build in a script.
It reads geometry from whatever `--config` you pass, so the width and label
checks follow a vendor config rather than being hard-coded to this one.

### 2. A unit type that doesn't exist yet — config edit

Frame the cubicle in a reference drawing and read its real geometry:

```bash
python measure.py 8508+A01-000-053.dxf 24.0 27.5      # x window = one cubicle
```

Then, in `sld_config.json`, add a `roles` entry per device (subtract the
cubicle's centreline x to get each `dx`; `y` is absolute and copies straight
across), list those role names in a new `archetypes` entry, and give it
`control_wiring` if it carries any. Reference the archetype from `units.csv`.
No code changes — the eight existing archetypes were all built this way.

If the new unit needs a symbol the library lacks, `build_sld.py` prints
`MISSING from library`; see *Stubs* below for how to add one.

### 3. A different switchgear standard — new config

`--config` takes any file, so a vendor or standard with different elevations is
a copy of `sld_config.json` rather than a fork of the generator:

```bash
python build_sld.py units.csv out.dxf --config sld_config_vendorX.json
```

## How a cubicle is built

Three layers of indirection, each editable without touching the others:

```
units.csv          archetype name + per-unit data (tag, ratings, CT ratios)
      |
sld_config.json    archetype -> ordered list of device roles
      |            role -> block name, dx offset, sheet elevation, attributes
      |
symbol_library.dxf the block geometry itself
```

To add a unit type: define its roles in `sld_config.json`, then reference the
archetype from `units.csv`. No new code, no new composite block.

### Fixed elevations, not stacking

Devices sit at **absolute sheet elevations** shared by every unit, matching how
the reference drawings are laid out. A unit missing a device leaves that
elevation empty and the conductor runs through it. This is why the lineup reads
straight across regardless of cubicle contents.

A few archetypes move geometry — the tie unit drops its breaker stack to clear
a third CT — via dedicated roles plus `text_overrides` so labels follow.

### Conductors

The main conductor is drawn from the bus down to the lowest series terminal,
broken around each series device's `gap`. Devices marked `"series": false` do
not break it: **a CT encircles the conductor rather than interrupting it**,
which is why the reference runs unbroken wire past both CTs.

### Attributes

`attribs` maps an ATTDEF tag to a `units.csv` column, or to a literal when
prefixed with `@`:

```json
"attribs": {"DESC1": "ct_protection", "DESC2": "@CT 5P20"}
```

Roles without `attribs` get blank, invisible attributes. This matters: ACADE
symbols carry ~17 ATTDEFs each, and any left unset would otherwise plot the
ATTDEF's default text (an unset `TAG1` on `VXF1CT` renders as `XF`).

## Current archetypes

| Archetype | Reference units | Distinguishing feature |
|---|---|---|
| `gen_feeder` | 1-5, 15-19 | drawout breaker, 2 CTs, arrester, CPT fuse |
| `customer_feeder` | 6, 12 | + control transformer |
| `bess_feeder` | 7, 13 | + second fuse |
| `xfmr_feeder` | 8 | fused disconnect + Kirk key, 1 CT, load-side one-line |
| `xfmr_riser` | 14 | same head as unit 8, but ends on a connector |
| `pt_riser_tie` | 9 | bus PT feeding control power; takes the tie's differential CT |
| `pt_riser` | 11 | same, but its differential CT comes from the other sheet |
| `main_tie` | 10 | 3 CTs, 2 CPT fuses, lowered breaker stack, leaves sideways |

Units 8 and 14 look alike above the CT and diverge below it: 8 carries the
500KVA transformer, PP-1 and LP-1 down to y=3.5, while 14 terminates on a
drawout connector. Units 9 and 11 differ only in where the bus-differential CT
comes from. Both pairs are separate archetypes rather than one with switches,
because the difference is which devices exist, not how they are placed.

### Role kinds

| Kind | Draws |
|---|---|
| `block` | a library symbol, optionally with a CT `polarity_dot` |
| `breaker` | the drawout breaker rectangle |
| `box` | a compartment or label outline |
| `bubble` | an ANSI device bubble with leads derived from its own radius |
| `circle` | a bare circle (the Kirk key interlock) |
| `lead` | an explicit run of segments -- leaders, tie-outs, panel drops |
| `note` | annotation tied to a device: `@literal` text or a `units.csv` column |

## Cubicle pitch

Set to **3.610** — the widest cubicle in the reference set (unit 8), per IOM
practice of drawing all units to the widest one. The reference sheets vary
2.427-3.610 from hand placement; the generator makes them uniform.

Change `sheet.pitch` in `sld_config.json` if the real standard width differs.

## Stubs — replace before issuing drawings

Two symbols still have no real block and are drawn as red placeholders on
layer `SLD_STUB`:

- `SLD_STUB_GND_SWITCH` — ground switch
- `SLD_STUB_43LR` — local/remote selector

`build_sld.py` prints which stubs are in use on every run.

### Replacing a stub

Draw the real block in any DXF, then re-run the extractor with that file as an
extra source and point the role at the new block name:

```bash
python extract_symbols.py symbol_library.dxf 8508+A01-000-053.dxf your_working_file.dxf
```

Later sources win on name collisions, so a symbol redrawn in a working file
supersedes the project-sheet version. This is how `ARRESTER` and
`DEVICE_BUBBLE` replaced their stubs.

### Note on `ARRESTER`

That block is the **entire branch** — tap dot, wire out to the ground symbol,
arrester body, and the `L.A. / 18kV / 15.3kV MCOV` label. So it is a single
`block` role placed on the centreline, not an assembly.

Because the label is baked in as plain MTEXT rather than an ATTDEF, **the
arrester rating is identical on every unit**. The `arrester` column in
`units.csv` is currently unused as a result. To make it vary per unit, convert
that MTEXT to an ATTDEF in the block and add an `attribs` entry to the role.

### Note on `DEVICE_BUBBLE`

Its attribute is named `DB`, not the ACADE-standard `TAG1`. The role's
`attrib_tag` field carries that, so other bubble blocks can use a different
tag name without code changes.

## Control wiring

Drawn on layer `SLD_CONTROL` from four primitives:

- **Buses** — lineup-wide horizontal runs (`control` in the config)
- **Risers** — per-archetype verticals carrying signals up to the relay box
- **Spurs** — per-unit horizontals from a device out to a riser
- **Hops** — arcs where one run crosses another it doesn't connect to

```json
"control": {
  "hop_radius": 0.05,
  "buses": [
    {"name": "control_power", "y": 16.590, "start_dx": 1.431, "end_dx": 1.431},
    {"name": "ct_metering",   "y": 10.970, "start_dx": 1.789, "end_dx": 1.789},
    {"name": "diff_ct",       "y": 10.255, "start_dx": -0.143, "end_dx": 1.124}
  ]
}
```

A unit's CT secondary horizontal at y=10.059 is deliberately *not* here. It
looks lineup-wide on the reference sheet but it is a per-unit spur that steps
up to the metering bus at `dx 1.789` — modelling it as a bus produced one
continuous line through cubicles that should each have their own.

Three things are computed rather than configured:

**Bus extent.** A unit joins a bus if its `control_wiring` has a riser
terminating at, or a spur running along, that elevation — or if it names the
bus in `joins`, which the PT units need because they feed control power
through their *power* conductor and so have no riser to infer from. An
archetype can override where it meets a bus with `bus_dx`: the PT takes the
metering bus into the edge of its relay box, not at the feeders' riser offset.

A bus needs **two** participating units or it is dropped, which is how the
bus-differential run disappears from a lineup with no tie unit. The test is on
participants rather than on the resulting endpoints: `diff_ct` is the one bus
whose `start_dx` and `end_dx` differ, so with a single unit on it the ends do
not coincide and an endpoint test leaves a 1.267-long stub ending in mid-air.

A riser whose end lands on a bus that got dropped is skipped with it — it
exists to reach that bus and has nothing else to connect to. Risers that end
at an elevation no bus declares are left alone, which is what keeps the feeder
CT risers running below the cubicle.

**Hops.** There are two families, and using the wrong one is what reads as
wrong to a drafter:

| Arc | Meaning |
|---|---|
| 270°→90°, bulging +x | a **riser** detours around a horizontal it crosses |
| 0°→180°, bulging +y | a **horizontal** detours around a vertical it crosses |

Control runs never break a power conductor — the horizontal always hops over
it. A conductor that *terminates* on a bus is a connection, not a crossing, and
gets a junction dot instead; that is how the PT unit feeds the control bus.

Where a spur meets a riser the winner is convention, not geometry, so the spur
declares it. The default is that the riser hops; the tie unit's mid-CT
secondary sets `"priority": "horizontal"` to reverse it, matching the
reference.

Adding wiring to an archetype means declaring its risers and spurs — the buses
extend themselves and the hops fall out of the geometry.

## Leads follow their symbol

Anything attached to a symbol is derived from that symbol's own centre and
radius, not written as a fixed coordinate:

- the 86 bubble's lead to the breaker and its drop to the relay box come from
  the bubble's `dx`/`y`/`radius`
- CT polarity dots come from the CT's `polarity_dot` offset

Fixed offsets broke once: the bubble's leads were measured against the
reference circle (r 0.176) and left a visible gap when the real `DEVICE_BUBBLE`
block (r 0.217) went in at a different offset. Deriving them means moving or
resizing a symbol keeps everything attached.

Note the bubble sits at `dx 1.054`, not the `1.321` it was first drawn at — at
that offset an r=0.217 circle spans 1.104–1.538 and collides with the
control-power riser at `dx 1.429`.

## Not yet implemented

- **Feeder terminal boxes.** The reference ends each feeder's two risers in a
  small box below the cubicle carrying a per-unit note ('TO GEN-1A BECKWITH
  3425A'). The risers currently stop at y=8.936 with nothing on them. Needs a
  `units.csv` column for the text.
- **Title block, borders, ladder references.** `LINEREF_ENCASEMENT` rungs and
  the IOM title block are sheet furniture, not generated.
- **Ground switch and 43LR** are defined as roles but not used by any
  archetype — no reference unit contains them.

### Where this deliberately differs from the reference

- Risers hop over the metering bus at y=10.970; the reference crosses them
  plain. Both readings are unambiguous, but hopping is consistent with how the
  same drawing treats y=10.059.
- Unit 8's continuation note reads `CONTINUE BUS-B UNIT 15`, transcribed from
  the leader on sheet 053. It is odd next to unit 14's `CONTINUE BUS-A UNIT 8`
  and worth checking — it lives in the `destination` column, so it is a CSV
  edit either way.
- The tie unit's control-power bus stops at its second CPT fuse rather than
  running 1.0 further to the sheet edge; that stub goes off-sheet to Bus B,
  which this generator draws as part of the same lineup.

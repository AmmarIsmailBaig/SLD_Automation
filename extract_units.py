#!/usr/bin/env python3
"""
Read an existing IOM one-line DXF and write the units.csv that describes it.

The reverse of build_sld.py: it finds the cubicle boxes, works out which
breakers sit in each, and pulls the tag, ratings, relay, destination and CT
ratios off the drawing. Written for 8513-000-052, which is a two-high lineup
(two breakers stacked per cubicle) -- so it reports the deck each breaker
sits on as well.

    python extract_units.py 8513A01000052.dxf 8513_units.csv
"""

import csv, re, sys
import ezdxf

CUBICLE_MIN_H = 10.0        # a cubicle box is tall; a note box is not
TAG_RE = re.compile(r'^(52-[A-Z0-9]+)\\P')


def plain(s):
    """Strip MTEXT formatting codes down to text with \\P line breaks.

    \\P is the line break and must survive; every other backslash code is
    formatting. Park the breaks on a sentinel before stripping, or the
    catch-all below eats them and every label collapses to one line.
    """
    s = s.replace('\\P', '\x00').replace('\n', '\x00')
    s = re.sub(r'\\p[^;]*;', '', s)             # paragraph properties
    s = re.sub(r'\\[A-Za-z][^\\;]*;?', '', s)   # font, height, colour, ...
    s = s.replace('{', '').replace('}', '')
    return s.replace('\x00', '\\P').strip()


def texts(msp):
    out = []
    for e in msp:
        if e.dxftype() == 'MTEXT':
            out.append((e.dxf.insert.x, e.dxf.insert.y, plain(e.text)))
        elif e.dxftype() == 'TEXT':
            out.append((e.dxf.insert.x, e.dxf.insert.y, e.dxf.text.strip()))
    return out


def cubicles(msp):
    boxes = []
    for e in msp.query('LWPOLYLINE'):
        pts = [(p[0], p[1]) for p in e.get_points()]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if max(ys) - min(ys) > CUBICLE_MIN_H:
            boxes.append((min(xs), max(xs), min(ys), max(ys)))
    return sorted(boxes)


def main(src, dst):
    doc = ezdxf.readfile(src)
    msp = doc.modelspace()
    all_text = texts(msp)
    boxes = cubicles(msp)

    cts = [(e.dxf.insert.x, e.dxf.insert.y,
            {a.dxf.tag: a.dxf.text for a in e.attribs})
           for e in msp.query('INSERT') if e.dxf.name in ('VXF1CT', 'HXF1CT')]

    # Bus split: the tie breaker divides A from B.
    tie_x = next((x for x, y, t in all_text if t.startswith('52-T\\P')), None)

    rows, deck_note = [], []
    n = 0
    for x0, x1, y0, y1 in boxes:
        inside = [(x, y, t) for x, y, t in all_text if x0 <= x < x1]
        breakers = sorted([(y, x, t) for x, y, t in inside if TAG_RE.match(t)],
                          key=lambda b: -b[0])          # upper deck first
        if not breakers:
            continue
        mid = (y0 + y1) / 2

        for by, bx, btext in breakers:
            n += 1
            parts = btext.split('\\P')
            tag = parts[0]
            volt = parts[1] if len(parts) > 1 else ''
            amp = parts[2] if len(parts) > 2 else ''
            ka = parts[3] if len(parts) > 3 else ''
            upper = by > mid
            deck = 'upper' if upper else 'lower'
            if len(breakers) == 1:
                deck = 'single'

            # On a single-breaker cubicle everything in the box belongs to it;
            # on a two-high cubicle each deck only owns its own half.
            def mine(y):
                return len(breakers) == 1 or (y > mid) == upper

            # Relay: nearest 'RELAY' caption this breaker owns.
            cand = [(abs(y - by), t) for x, y, t in inside
                    if 'RELAY' in t.upper() and mine(y)]
            relay = ' '.join(min(cand)[1].split('\\P')).strip() if cand else ''

            # Destination sits OUTSIDE the cubicle box -- above the top for the
            # upper deck, below the bottom for the lower. Bounding the band
            # keeps the title block and the arrester label out of it.
            if upper and len(breakers) > 1:
                band = [(y, t) for x, y, t in inside if y1 < y < y1 + 2.5]
                dest = ' '.join(min(band)[1].split('\\P')) if band else ''
            else:
                band = [(y, t) for x, y, t in inside if y0 - 2.0 < y < y0]
                dest = ' '.join(max(band)[1].split('\\P')) if band else ''

            # CTs this breaker owns, highest first: protection above metering.
            ratios = [r for _, r in sorted(
                [(y, a.get('DESC1', '')) for cx, y, a in cts
                 if x0 <= cx < x1 and mine(y) and a.get('DESC1')],
                key=lambda c: -c[0])]
            ct_prot = ratios[0] if ratios else ''
            ct_met = ratios[-1] if len(ratios) > 1 else ''

            arrester = next((' '.join(t.split('\\P')) for x, y, t in inside
                             if t.startswith('L.A.') and mine(y)), '')

            rows.append({
                'unit': n, 'bus': 'A' if (tie_x is None or bx <= tie_x) else 'B',
                'tag': tag,
                'description': 'SPARE' if 'SPARE' in dest.upper() else '',
                'archetype': 'main_tie' if tag == '52-T' else 'gen_feeder',
                'voltage': volt, 'amp_rating': amp, 'ka_rating': ka,
                'relay': relay, 'destination': dest,
                'ct_protection': ct_prot, 'ct_metering': ct_met,
                'arrester': arrester, 'diff_note': '',
            })
            deck_note.append((n, tag, round(x0, 2), round(x1, 2), deck))

    cols = ['unit', 'bus', 'tag', 'description', 'archetype', 'voltage',
            'amp_rating', 'ka_rating', 'relay', 'destination',
            'ct_protection', 'ct_metering', 'arrester', 'diff_note']
    with open(dst, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print(f'{len(boxes)} cubicles -> {len(rows)} breakers -> {dst}\n')
    print(f'{"#":>3} {"tag":10s} {"bus":4s} {"deck":7s} {"cubicle x":15s} '
          f'{"prot CT":14s} {"met CT":14s} destination')
    for r, (n_, tag, a, b, deck) in zip(rows, deck_note):
        print(f'{n_:>3} {tag:10s} {r["bus"]:4s} {deck:7s} {a:6.2f}..{b:<7.2f} '
              f'{r["ct_protection"]:14s} {r["ct_metering"]:14s} {r["destination"][:34]}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '8513_052.dxf',
         sys.argv[2] if len(sys.argv) > 2 else '8513_units.csv')

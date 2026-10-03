"""Dial scale as SVG on stdout: epoch bands on the left, cities on the main line, both in knob (folder) order."""
import re
import unicodedata
from xml.sax.saxutils import escape

from radio import scan

W, H, LINE = 1800, 600, 300
X0, X1 = 520, 1700  # first and last city tick; even steps, so each tick sits over its LEDs
SEP = 400  # right edge of the band frame
FONT = 'font-family="Helvetica Neue" font-weight="bold" font-size="38"'
TICK, MAIN, FRAME = 3, 18, 6  # stroke widths
OVERRUN = 25  # main line past the end ticks
EPOCHS = {"1_early": "Gramophone", "2_middle": "Vinyl", "3_modern": "Digital"}  # scale names for epoch folders


def label(name: str) -> str:
    name = unicodedata.normalize("NFKD", re.sub(r"^\d+_", "", name)).encode("ascii", "ignore").decode()  # Kraków -> Krakow
    return escape(" ".join(w[:1].upper() + w[1:] for w in name.split()))


def svg(locs: list[str], epochs: list[str]) -> str:
    step = (X1 - X0) / (len(locs) - 1)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
           f'<rect width="{W}" height="{H}" fill="#000"/>', '<g stroke="#fff" fill="none">',
           f'<line x1="{X0 - OVERRUN}" y1="{LINE}" x2="{X1 + OVERRUN}" y2="{LINE}" stroke-width="{MAIN}"/>',
           f'<rect x="24" y="{LINE - 190}" width="{SEP - 24}" height="380" stroke-width="{FRAME}"/>']
    for i in range(len(locs)):
        x, dy = X0 + step * i, -80 if i % 2 == 0 else 80
        out.append(f'<line x1="{x:.1f}" y1="{LINE}" x2="{x:.1f}" y2="{LINE + dy}" stroke-width="{TICK}"/>')
    bands = [LINE + 120 * (i - (len(epochs) - 1) / 2) for i in range(len(epochs))]
    out += [f'<circle cx="74" cy="{y:.1f}" r="15" fill="#fff" stroke="none"/>' for y in bands]
    out.append(f'</g><g fill="#fff" {FONT}>')
    for i, loc in enumerate(locs):
        x = X0 + step * i
        name, top = label(loc), i % 2 == 0
        lines = name.split(" ", 1) if len(name) > 10 else [name]  # long names in two lines, growing away from the line
        y = LINE - 100 - 40 * (len(lines) - 1) if top else LINE + 140
        spans = "".join(f'<tspan x="{x:.1f}" dy="{40 * (j > 0)}">{t}</tspan>' for j, t in enumerate(lines))
        out.append(f'<text x="{x:.1f}" y="{y}" text-anchor="middle">{spans}</text>')
    out += [f'<text x="104" y="{y + 15:.1f}">{label(EPOCHS[ep])}</text>' for ep, y in zip(epochs, bands)]
    out.append("</g></svg>")
    return "\n".join(out)


if __name__ == "__main__":
    locs, epochs, _ = scan()
    print(svg(locs, epochs))

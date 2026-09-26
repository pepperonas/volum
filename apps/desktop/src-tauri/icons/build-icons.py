#!/usr/bin/env python3
"""Generate VOLUM's icon set.

The geometry is computed, not hand-drawn: a rounded square and an isometric
cube (a pointy-top hexagon plus the three edges meeting at its centre) built
from signed distance fields, so every size is antialiased from the same
definition rather than resampled from a drawing.

At 32 px the cube reads as a hexagon — deliberate: a mark has to survive the
smallest size it is shown at, and detail that turns to mush there is worse
than no detail.

    python3 build-icons.py            # writes into this directory

No third-party dependencies: PNG is zlib plus four chunks, ICO is a container
around PNGs, and ICNS comes from macOS's own iconutil.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER = 1024

# Dark ground with a cool cast, and the accent VOLUM already uses in its docs.
BACKGROUND = (0x12, 0x16, 0x1F, 0xFF)
STROKE = (0xB3, 0xC5, 0xFF, 0xFF)
STROKE_DIM = (0x6E, 0x86, 0xC8, 0xFF)

#: Corner radius as a share of the side. 22% is the platform-native look
#: without becoming a circle at small sizes.
CORNER = 0.22
#: Cube radius as a share of the side, and stroke width as a share of that.
CUBE = 0.30
STROKE_W = 0.085


def _rounded_square_distance(x: float, y: float, half: float, radius: float) -> float:
    """Signed distance to a rounded square centred on the origin."""
    dx = abs(x) - (half - radius)
    dy = abs(y) - (half - radius)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from a point to the segment a→b."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length_sq = vx * vx + vy * vy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / length_sq))
    return math.hypot(px - (ax + vx * t), py - (ay + vy * t))


def _cube_segments(size: int) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, float, float]]]:
    """The hexagon outline, and the three inner edges, in pixel coordinates.

    Returned apart because the inner edges are drawn dimmer: that is what makes
    a flat hexagon read as a solid seen from a corner.
    """
    centre = size / 2.0
    radius = size * CUBE
    # Pointy-top hexagon: a vertex straight up, then every 60 degrees.
    points = [
        (
            centre + radius * math.cos(math.radians(angle)),
            centre - radius * math.sin(math.radians(angle)),
        )
        for angle in range(90, 90 + 360, 60)
    ]
    outline = [
        (points[i][0], points[i][1], points[(i + 1) % 6][0], points[(i + 1) % 6][1])
        for i in range(6)
    ]
    # The three edges that meet at the centre: to the top, lower-left and
    # lower-right vertices — the ones visible on a cube seen from a corner.
    inner = [(centre, centre, points[i][0], points[i][1]) for i in (0, 2, 4)]
    return outline, inner


def _blend(base: tuple[int, int, int, int], over: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    if alpha <= 0:
        return base
    if alpha >= 1:
        return over
    return (
        round(base[0] + (over[0] - base[0]) * alpha),
        round(base[1] + (over[1] - base[1]) * alpha),
        round(base[2] + (over[2] - base[2]) * alpha),
        round(base[3] + (over[3] - base[3]) * alpha),
    )


def render_master(size: int = MASTER) -> list[list[tuple[int, int, int, int]]]:
    """Render the icon once, at full size, with analytic antialiasing."""
    half = size / 2.0
    radius = size * CORNER
    outline, inner = _cube_segments(size)
    stroke_half = size * CUBE * STROKE_W
    # One pixel of feathering: enough to read as smooth, narrow enough that the
    # stroke keeps its weight when the image is reduced.
    feather = 1.0

    def coverage(distance: float) -> float:
        return max(0.0, min(1.0, 0.5 - distance / feather))

    transparent = (0, 0, 0, 0)
    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        py = y + 0.5
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            px = x + 0.5
            ground = coverage(_rounded_square_distance(px - half, py - half, half, radius))
            pixel = _blend(transparent, BACKGROUND, ground)
            row.append(pixel)
        rows.append(row)

    # Only the neighbourhood of each segment is touched, so the strokes cost a
    # fraction of a full pass.
    for segments, colour in ((inner, STROKE_DIM), (outline, STROKE)):
        for ax, ay, bx, by in segments:
            reach = stroke_half + feather + 1
            x0 = max(0, int(min(ax, bx) - reach))
            x1 = min(size - 1, int(max(ax, bx) + reach))
            y0 = max(0, int(min(ay, by) - reach))
            y1 = min(size - 1, int(max(ay, by) + reach))
            for y in range(y0, y1 + 1):
                py = y + 0.5
                row = rows[y]
                for x in range(x0, x1 + 1):
                    px = x + 0.5
                    d = _segment_distance(px, py, ax, ay, bx, by) - stroke_half
                    alpha = coverage(d)
                    if alpha > 0:
                        row[x] = _blend(row[x], colour, alpha)
    return rows


def downsample(master: list[list[tuple[int, int, int, int]]], target: int) -> list[list[tuple[int, int, int, int]]]:
    """Box-average the master down to ``target``. Premultiplied, so the
    transparent corners do not bleed dark pixels into the edge."""
    source = len(master)
    if target == source:
        return master
    step = source / target
    out: list[list[tuple[int, int, int, int]]] = []
    for ty in range(target):
        y0, y1 = int(ty * step), int((ty + 1) * step)
        row: list[tuple[int, int, int, int]] = []
        for tx in range(target):
            x0, x1 = int(tx * step), int((tx + 1) * step)
            r = g = b = a = 0.0
            count = 0
            for y in range(y0, y1):
                src = master[y]
                for x in range(x0, x1):
                    pr, pg, pb, pa = src[x]
                    weight = pa / 255.0
                    r += pr * weight
                    g += pg * weight
                    b += pb * weight
                    a += pa
                    count += 1
            if count == 0:
                row.append((0, 0, 0, 0))
                continue
            a_mean = a / count
            if a_mean <= 0:
                row.append((0, 0, 0, 0))
                continue
            weight_sum = a / 255.0
            row.append(
                (
                    round(r / weight_sum),
                    round(g / weight_sum),
                    round(b / weight_sum),
                    round(a_mean),
                )
            )
        out.append(row)
    return out


def write_png(path: Path, rows: list[list[tuple[int, int, int, int]]]) -> Path:
    height = len(rows)
    width = len(rows[0])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0
        for pixel in row:
            raw.extend(pixel)
    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += chunk(b"IEND", b"")
    path.write_bytes(data)
    return path


def write_ico(path: Path, pngs: list[Path]) -> Path:
    """An ICO containing PNGs. Valid since Windows Vista and far simpler than
    the old DIB form."""
    entries, blobs = b"", b""
    offset = 6 + 16 * len(pngs)
    for png in pngs:
        payload = png.read_bytes()
        width, height = struct.unpack(">II", payload[16:24])
        entries += struct.pack(
            "<BBBBHHII",
            0 if width >= 256 else width,
            0 if height >= 256 else height,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        blobs += payload
        offset += len(payload)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(pngs)) + entries + blobs)
    return path


def main() -> int:
    print(f"rendering the master at {MASTER}px ...", flush=True)
    master = render_master()

    sizes = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
    }
    rendered: dict[int, list[list[tuple[int, int, int, int]]]] = {MASTER: master}

    def at(size: int) -> list[list[tuple[int, int, int, int]]]:
        if size not in rendered:
            rendered[size] = downsample(master, size)
        return rendered[size]

    for name, size in sizes.items():
        write_png(HERE / name, at(size))
        print(f"  {name}")

    # Windows wants the small sizes in one file.
    ico_parts = []
    for size in (16, 32, 48, 64, 128, 256):
        part = HERE / f".ico-{size}.png"
        write_png(part, at(size))
        ico_parts.append(part)
    write_ico(HERE / "icon.ico", ico_parts)
    for part in ico_parts:
        part.unlink()
    print("  icon.ico")

    # macOS builds the .icns itself from a named set.
    iconset = HERE / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        write_png(iconset / f"icon_{base}x{base}.png", at(base))
        write_png(iconset / f"icon_{base}x{base}@2x.png", at(base * 2))
    if shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")],
            check=True,
        )
        shutil.rmtree(iconset)
        print("  icon.icns")
    else:
        print("  icon.icns SKIPPED - iconutil is macOS only; the iconset is left in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())

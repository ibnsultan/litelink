#!/usr/bin/env python3
"""Render LiteLink motion designs to PNG frames for Plymouth.

Source: assets/brand/Litelink logo design/LiteLink Motion.dc.html
  01 "Boot - Draw-on + signal": kite drawn stroke by stroke, dims, then a
     signal pulse runs up the string (the design's progress bar is omitted).
  02 "Loading - Shimmer": a band of light passes over the mark (used for
     shutdown / restart).

Plymouth can't run SVG/CSS animation, so each frame is written as a static
SVG with the animation state baked in, then rasterised with rsvg-convert.
librsvg ignores pathLength, so dash lengths are computed from real path
lengths here.

  03 "Connecting - Signal" (with the `connect` set): the shell's Connecting
     screen.

usage: render_frames.py <kite.svg> <outdir> [fps] [scale] [plymouth|connect]
"""
import math
import os
import re
import subprocess
import sys
import tempfile

KITE_SVG, OUT = sys.argv[1], sys.argv[2]
FPS = int(sys.argv[3]) if len(sys.argv) > 3 else 25
SCALE = float(sys.argv[4]) if len(sys.argv) > 4 else 1.4

# ---------------------------------------------------------------- design data
COL = "#f2f5fa"     # mark colour ("White" option)
SIG = "#7cc0f2"     # signal colour
D = re.search(r'\sd="([^"]+)"', open(KITE_SVG).read()).group(1)

TAIL = ('M44 471 C80 468 112 464 132 450 C150 436 172 412 162 390 C152 368 122 364 112 386 '
        'C102 408 122 424 156 422 C180 421 215 410 246 390 C282 366 320 334 351 300 '
        'C380 268 405 232 440 218 C450 214 460 212 468 211')
RIGHT = ' C420 194 386 168 362 140 C340 113 326 84 318 38'
TOP = ' C292 80 250 115 212 136 C185 150 160 162 140 172'
LEFT = ' C162 190 182 220 196 260 C206 292 212 330 212 372'
SPINE = 'M318 50 C318 110 314 160 302 208 C292 250 276 296 252 334'
SEGS = [(TAIL, .04, .36), ('M468 211' + RIGHT, .34, .46), ('M318 38' + TOP, .44, .55),
        ('M140 172' + LEFT, .53, .63), (SPINE, .58, .68)]

TB = 5.2            # stroke reveal timeline
P = 2.2             # loop period
D0 = .68 * TB       # loop starts when the drawing finishes


# ---------------------------------------------------------------- helpers
def bezier(x1, y1, x2, y2):
    """CSS cubic-bezier timing function."""
    def sample(a1, a2, t):
        return ((1 - 3 * a2 + 3 * a1) * t + (3 * a2 - 6 * a1)) * t * t + 3 * a1 * t

    def f(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if sample(x1, x2, mid) < x:
                lo = mid
            else:
                hi = mid
        return sample(y1, y2, (lo + hi) / 2)
    return f


E = bezier(.55, .05, .25, 1)
EB = bezier(.65, 0, .35, 1)
EASE_OUT = bezier(0, 0, .58, 1)
SHIM = bezier(.45, 0, .3, 1)


def path_length(d):
    """Length of a path made of absolute M and C commands."""
    nums = [float(n) for n in re.findall(r'-?\d+\.?\d*', d)]
    cmds = re.findall(r'[MC]', d)
    i, x, y, total = 0, 0.0, 0.0, 0.0
    for c in cmds:
        if c == 'M':
            x, y = nums[i], nums[i + 1]
            i += 2
        else:
            p = [(x, y), (nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3]), (nums[i + 4], nums[i + 5])]
            i += 6
            px, py = p[0]
            for k in range(1, 65):
                t = k / 64
                mt = 1 - t
                qx = mt**3 * p[0][0] + 3 * mt * mt * t * p[1][0] + 3 * mt * t * t * p[2][0] + t**3 * p[3][0]
                qy = mt**3 * p[0][1] + 3 * mt * mt * t * p[1][1] + 3 * mt * t * t * p[2][1] + t**3 * p[3][1]
                total += math.hypot(qx - px, qy - py)
                px, py = qx, qy
            x, y = p[3]
    return total


LEN = {d: path_length(d) for d in [TAIL] + [s[0] for s in SEGS]}


def clamp01(v):
    return max(0.0, min(1.0, v))


def mark(fill, extra=''):
    return f'<path d="{D}" fill="{fill}" fill-rule="evenodd" {extra}/>'


# ---------------------------------------------------------------- 01 boot
KITE = 150          # design px
W, H = KITE + 10, KITE + 10


def boot_frame(t):
    """SVG for the boot animation at time t (seconds)."""
    s = KITE / 552.0
    # Stroke reveal mask
    strokes = []
    for i, (d, a, b) in enumerate(SEGS):
        p = E(clamp01((t / TB - a) / (b - a)))
        if p <= 0:
            continue
        L = LEN[d]
        strokes.append(f'<path d="{d}" fill="none" stroke="#fff" stroke-width="48" '
                       f'stroke-linecap="round" stroke-linejoin="round" '
                       f'stroke-dasharray="{L:.2f} {L:.2f}" stroke-dashoffset="{(1 - p) * L:.2f}"/>')

    loop = t >= D0
    tl = t - D0
    phase = (tl % P) / P if loop else 0.0
    dim = 1 - .6 * EASE_OUT(clamp01(tl / .6)) if loop else 1.0

    # Signal pulse travelling up the string
    L = LEN[TAIL]
    off = (.2 - 1.2 * EB(phase)) if loop else .2
    sig = (f'<path d="{TAIL}" fill="none" stroke="#fff" stroke-width="48" stroke-linecap="butt" '
           f'stroke-dasharray="{.2 * L:.2f} {3 * L:.2f}" stroke-dashoffset="{off * L:.2f}"/>')

    # Flash when the pulse reaches the kite (80% -> 90% -> 100%)
    if loop and phase > .8:
        flash = (phase - .8) / .1 if phase < .9 else 1 - (phase - .9) / .1
    else:
        flash = 0.0

    # The design's indeterminate progress bar is left out on purpose: the
    # owner prefers the kite animation alone.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>
 <mask id="reveal" maskUnits="userSpaceOnUse" x="-200" y="-200" width="912" height="912">{''.join(strokes)}</mask>
 <mask id="sig" maskUnits="userSpaceOnUse" x="-200" y="-200" width="912" height="912">{sig}</mask>
</defs>
<g transform="translate({(W - KITE) / 2} 0) scale({s}) translate(20 20)">
 <g mask="url(#reveal)">
  {mark(COL, f'opacity="{dim:.3f}"')}
  {mark(SIG, 'mask="url(#sig)"') if loop else ''}
  {mark(COL, f'opacity="{flash:.3f}"') if flash > 0 else ''}
 </g>
</g>
</svg>'''


# ---------------------------------------------------------------- 02 shimmer
TS = 2.8
SK = 160            # design px


def shimmer_frame(t):
    s = SK / 552.0
    x = -120 + 1000 * SHIM(clamp01((t % TS) / TS / .65))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SK}" height="{SK}" viewBox="0 0 {SK} {SK}">
<defs>
 <linearGradient id="g" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity="1"/>
  <stop offset="1" stop-color="#fff" stop-opacity="0"/>
 </linearGradient>
 <mask id="sh" maskUnits="userSpaceOnUse" x="-200" y="-200" width="912" height="912">
  <g transform="translate({x:.2f} 0)"><rect x="-260" y="-200" width="260" height="912" fill="url(#g)" transform="rotate(20 -130 256)"/></g>
 </mask>
</defs>
<g transform="scale({s}) translate(20 20)">
 {mark(COL, 'opacity=".3"')}
 {mark(COL, 'mask="url(#sh)"')}
</g>
</svg>'''


# ---------------------------------------------------------------- 03 connect
TC = 2.6
SIG_EASE = bezier(.5, 0, .75, .7)
FLASH_EASE = bezier(0, 0, .58, 1)


def connect_frame(t, col=COL, sig_col=SIG, base=.35):
    """Motion 03 "Connecting - Signal": a pulse runs up the string and the
    kite lights when it arrives. Default colours are for dark screens."""
    s = KITE / 552.0
    ph = (t % TC) / TC
    L = LEN[TAIL]
    off = .2 - 1.2 * SIG_EASE(clamp01(ph / .55))
    if ph <= .5:
        flash = 0.0
    elif ph <= .6:
        flash = (ph - .5) / .1
    else:
        flash = 1 - FLASH_EASE((ph - .6) / .4)
    sig = (f'<path d="{TAIL}" fill="none" stroke="#fff" stroke-width="48" stroke-linecap="butt" '
           f'stroke-dasharray="{.2 * L:.2f} {3 * L:.2f}" stroke-dashoffset="{off * L:.2f}"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs><mask id="sig" maskUnits="userSpaceOnUse" x="-200" y="-200" width="912" height="912">{sig}</mask></defs>
<g transform="translate({(W - KITE) / 2} 0) scale({s}) translate(20 20)">
 {mark(col, f'opacity="{base}"')}
 {mark(sig_col, 'mask="url(#sig)"')}
 {mark(col, f'opacity="{flash:.3f}"') if flash > 0 else ''}
</g>
</svg>'''


def install_frame(t):
    """Same motion for light surfaces (installer card): brand blue on white."""
    return connect_frame(t, col="#2f6fd6", sig_col="#2f6fd6", base=.18)


# ---------------------------------------------------------------- render
def render(name, frame_fn, start, end):
    n = round((end - start) * FPS)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            svg = os.path.join(tmp, 'f.svg')
            with open(svg, 'w') as f:
                f.write(frame_fn(start + i / FPS))
            subprocess.run(['rsvg-convert', '-z', str(SCALE), svg, '-o',
                            os.path.join(OUT, f'{name}-{i:03d}.png')], check=True)
    return n


os.makedirs(OUT, exist_ok=True)
if len(sys.argv) > 5 and sys.argv[5] == 'connect':
    # Shell "Connecting" screen
    n = render('connect', connect_frame, 0, TC)
    print(f'render_frames: connect={n} @ {FPS}fps x{SCALE}')
    sys.exit(0)
if len(sys.argv) > 5 and sys.argv[5] == 'install':
    # Installer wizard (light card)
    n = render('install', install_frame, 0, TC)
    print(f'render_frames: install={n} @ {FPS}fps x{SCALE}')
    sys.exit(0)

# Plymouth. Intro: drawing + dim + first pulse. Loop: one steady pulse period.
n_intro = render('boot-intro', boot_frame, 0, D0 + P)
n_loop = render('boot-loop', boot_frame, D0 + P, D0 + 2 * P)
n_shim = render('shimmer', shimmer_frame, 0, TS)
with open(os.path.join(OUT, 'frames.txt'), 'w') as f:
    f.write(f'fps={FPS}\nboot_intro={n_intro}\nboot_loop={n_loop}\nshimmer={n_shim}\n')
print(f'render_frames: intro={n_intro} loop={n_loop} shimmer={n_shim} @ {FPS}fps x{SCALE}')

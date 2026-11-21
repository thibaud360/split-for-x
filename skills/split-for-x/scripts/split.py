#!/usr/bin/env python3
"""split-for-x helper: split one image into panels that line up in an X
(Twitter) multi-image post or an Instagram carousel.

Claude chooses layout / panel count / framing after looking at the image; this
script only executes the choices and draws previews (seams.jpg, mockup.jpg).
Python 3 + Pillow + numpy, no network.
"""
import argparse
import math
import os
import re
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ----------------------------------------------------------------------------
# Measured platform geometry (X desktop web, Nov 2026).  Edit here.
# ----------------------------------------------------------------------------
X2_COLUMN_PX = 516            # media column width
X2_GAP_PX = 6                 # gap between the two tiles
X2_TILE_PX = (X2_COLUMN_PX - X2_GAP_PX) / 2     # 255 for two equal panels
X2_ASPECT_RANGE = (0.6, 1.2)  # panel aspect (w/h) that X does not crop

CAR_TILE_PX = 305.8           # tile width when all panels share one aspect
CAR_GAP_PX = 6
CAR_VISIBLE_PX = 533          # visible part of the lane
CAR_MAX_ASPECT = 1.1          # wider tiles get cropped by X (to 412 px)
CAR_MAX_PANELS = 10
CAR_DEFAULT_ASPECT = 3 / 4

IG_W = 1080                   # px; panels are 1080x1350 (4:5) or 1080x1080
IG_ASPECTS = (4 / 5, 1.0)
IG_DEFAULT_ASPECT = 4 / 5
IG_MAX_PANELS = 20

# Output resolution policy
DEFAULT_MAX_SIDE = 4096       # cap on a panel's long side
MIN_LONG_SIDE = 1080          # below this (native), upscale a little
MAX_UPSCALE = 1.5             # ... but never more than this (X layouts)
JPEG_QUALITY = 95

# Previews
SEAMS_MAX_W = 1600
MOCK_W = 1200                 # mockup width in px
MOCK_SCALE = 2                # CSS px -> mockup px
MOCK_CORNER = 16              # CSS px, media group outer corners
MOCK_PAD = 16                 # card left padding (CSS px)
MOCK_AVATAR = 40
MOCK_AVATAR_GAP = 12
MOCK_COL_LEFT = MOCK_PAD + MOCK_AVATAR + MOCK_AVATAR_GAP    # 68
MOCK_HEADER_H = 60            # CSS px above the media
MOCK_BG = (0, 0, 0)
MOCK_GREY = (51, 54, 57)
MOCK_LABEL = (113, 118, 123)
MAGENTA = (255, 0, 255)

# Seam-vs-focus warning radius, as a fraction of panel width
FOCUS_SEAM_WARN = 0.12


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def S(v, scale=MOCK_SCALE):
    return int(round(v * scale))


def get_font(size):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def parse_aspect(s):
    try:
        if ":" in s:
            a, b = s.split(":")
            v = float(a) / float(b)
        else:
            v = float(s)
    except Exception:
        raise argparse.ArgumentTypeError("aspect must be W:H (e.g. 3:4) or a number")
    if v <= 0:
        raise argparse.ArgumentTypeError("aspect must be positive")
    return v


def parse_focus(s):
    try:
        x, y = [float(t) for t in s.split(",")]
    except Exception:
        raise argparse.ArgumentTypeError("focus must be 'x,y' with values 0..1")
    return (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))


def parse_bg(s):
    if s in ("auto", "blur", "mirror"):
        return s
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", s):
        return "#" + s.lstrip("#")
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", s):
        h = s.lstrip("#")
        return "#" + "".join(c * 2 for c in h)
    raise argparse.ArgumentTypeError("bg must be auto|blur|mirror|#hex")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def load_image(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        base = Image.new("RGB", im.size, (255, 255, 255))
        base.paste(im, mask=im.getchannel("A"))
        return base
    if im.mode in ("I;16", "I"):
        arr = np.asarray(im).astype(np.float32)
        arr = arr / max(arr.max(), 1) * 255
        im = Image.fromarray(arr.astype(np.uint8))
    return im.convert("RGB")


# ----------------------------------------------------------------------------
# Background fills for --fit contain
# ----------------------------------------------------------------------------
def border_color(img):
    """Median border colour if the border is near-uniform, else None."""
    sm = img.copy()
    sm.thumbnail((256, 256))
    a = np.asarray(sm).astype(np.float32)
    h, w, _ = a.shape
    t = max(1, int(0.03 * min(h, w)))
    strips = np.concatenate([a[:t].reshape(-1, 3), a[-t:].reshape(-1, 3),
                             a[:, :t].reshape(-1, 3), a[:, -t:].reshape(-1, 3)])
    if strips.std(axis=0).max() < 10:
        return tuple(int(round(v)) for v in np.median(strips, axis=0))
    return None


def small_blur(im, radius_frac, work=512):
    """Blur at reduced resolution (fast), return at original size."""
    w, h = im.size
    k = min(1.0, work / max(w, h))
    sw, sh = max(1, round(w * k)), max(1, round(h * k))
    s = im.resize((sw, sh), Image.BICUBIC).filter(
        ImageFilter.GaussianBlur(max(0.5, radius_frac * max(sw, sh))))
    return s.resize((w, h), Image.BICUBIC)


def make_background(img, size, mode, focus, place):
    """size=(W,H) canvas; place=(ox,oy,nw,nh) where the image will sit."""
    W, H = size
    ox, oy, nw, nh = place
    if mode.startswith("#"):
        return Image.new("RGB", size, hex_to_rgb(mode))
    if mode == "blur":
        k = min(1.0, 512 / max(W, H))
        sw, sh = max(1, round(W * k)), max(1, round(H * k))
        cov = ImageOps.fit(img, (sw, sh), Image.LANCZOS, centering=focus)
        z = 1.15   # scaled-up copy
        cw, ch = sw / z, sh / z
        cov = cov.crop((round((sw - cw) / 2), round((sh - ch) / 2),
                        round((sw + cw) / 2), round((sh + ch) / 2)))
        cov = cov.resize((sw, sh), Image.BICUBIC)
        cov = cov.filter(ImageFilter.GaussianBlur(0.05 * max(sw, sh)))
        cov = Image.eval(cov, lambda v: int(v * 0.72))   # slightly darkened
        return cov.resize((W, H), Image.BICUBIC)
    # mirror: edge reflection, blurred progressively away from the image edge
    placed = img.resize((nw, nh), Image.LANCZOS)
    arr = np.asarray(placed)
    pad = ((oy, H - oy - nh), (ox, W - ox - nw), (0, 0))
    refl = Image.fromarray(np.pad(arr, pad, mode="symmetric"))
    blurred = small_blur(refl, 0.02)
    dx = np.maximum(np.maximum(ox - np.arange(W), np.arange(W) - (ox + nw - 1)), 0)
    dy = np.maximum(np.maximum(oy - np.arange(H), np.arange(H) - (oy + nh - 1)), 0)
    d = np.hypot(dx[None, :], dy[:, None]).astype(np.float32)
    ramp = 0.03 * max(W, H)
    mask = Image.fromarray((np.clip(d / ramp, 0, 1) * 255).astype(np.uint8))
    return Image.composite(blurred, refl, mask)


# ----------------------------------------------------------------------------
# Previews
# ----------------------------------------------------------------------------
def draw_seams_preview(path, base, k, used, panels_x, pw, ph, mscale, focus_pt,
                       img_rect):
    """base: image to show; k: base->preview scale; used: (x0,y0,x1,y1) in base px
    of the used area; panels_x: left x of each panel in canvas px; mscale:
    preview px per canvas px; origin of canvas = used[0:2] * k."""
    pv = base.resize((max(1, round(base.width * k)), max(1, round(base.height * k))),
                     Image.LANCZOS).convert("RGBA")
    ux0, uy0, ux1, uy1 = [v * k for v in used]
    # dim everything outside the used area
    dim = Image.new("RGBA", pv.size, (0, 0, 0, 150))
    keep = Image.new("L", pv.size, 0)
    ImageDraw.Draw(keep).rectangle([round(ux0), round(uy0), round(ux1) - 1, round(uy1) - 1], fill=255)
    pv = Image.composite(pv, Image.alpha_composite(pv, dim), keep)
    ov = Image.new("RGBA", pv.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    fsz = max(14, int(0.03 * min(pv.size)))
    f = get_font(fsz)
    top, bot = uy0, uy1
    lw = 3
    for i, x in enumerate(panels_x):
        l = ux0 + x * mscale
        r = l + pw * mscale
        if i + 1 < len(panels_x):
            nl = ux0 + panels_x[i + 1] * mscale
            if nl - r > 0.5:   # dropped strip: shade + hatch
                d.rectangle([r, top, nl, bot], fill=(0, 0, 0, 175))
                step = 10
                for t in range(int(-(bot - top)), int(nl - r) + 1, step):
                    d.line([(r + t, bot), (r + t + (bot - top), top)],
                           fill=(255, 255, 255, 70), width=1)
    for i, x in enumerate(panels_x):
        l = ux0 + x * mscale
        r = l + pw * mscale
        for xx in (l, r):
            d.line([(xx, top), (xx, bot)], fill=MAGENTA + (255,), width=lw)
        d.text((l + 8, top + 6), str(i + 1), font=f, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 255))
    d.rectangle([ux0, uy0, ux1 - 1, uy1 - 1], outline=(255, 220, 0, 255), width=2)
    if img_rect is not None:
        x0, y0, x1, y1 = [v * mscale for v in img_rect]
        d.rectangle([ux0 + x0, uy0 + y0, ux0 + x1 - 1, uy0 + y1 - 1],
                    outline=(0, 255, 255, 255), width=1)
    if focus_pt is not None:
        fx, fy = ux0 + focus_pt[0] * mscale, uy0 + focus_pt[1] * mscale
        rr = max(6, fsz // 2)
        d.ellipse([fx - rr, fy - rr, fx + rr, fy + rr], outline=(0, 255, 0, 255), width=2)
        d.line([(fx - rr - 4, fy), (fx + rr + 4, fy)], fill=(0, 255, 0, 255), width=2)
        d.line([(fx, fy - rr - 4), (fx, fy + rr + 4)], fill=(0, 255, 0, 255), width=2)
    out = Image.alpha_composite(pv, ov).convert("RGB")
    out.save(path, quality=90)


def rounded_mask(w, h, r, ss=4):
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


ACCOUNT = {}   # filled from --name/--handle/--avatar/--text; empty = generic skeleton header


def _font(size, bold=False):
    names = (["Inter-Bold.otf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]
             if bold else
             ["Inter-Regular.otf", "/usr/share/fonts/opentype/inter/Inter-Regular.otf", "DejaVuSans.ttf", "Arial.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            pass
    return get_font(size)


def _wrap(text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            t = (cur + " " + word).strip()
            if font.getlength(t) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def account_header_h():
    """CSS px height of the header when a real account is shown."""
    f = _font(15)
    lines = _wrap(ACCOUNT.get("text", ""), f, X2_COLUMN_PX) if ACCOUNT.get("text") else []
    return 12 + 20 + 6 + 20 * len(lines) + 12


def post_header(height_px):
    """Post header: the user's account (avatar, name, handle, text) when given, else a grey skeleton."""
    if ACCOUNT:
        return account_header(height_px)
    ss = 2
    W, H = MOCK_W * ss, height_px * ss
    im = Image.new("RGB", (W, H), MOCK_BG)
    d = ImageDraw.Draw(im)
    k = MOCK_SCALE * ss
    ax, ay = MOCK_PAD * k, 12 * k
    d.ellipse([ax, ay, ax + MOCK_AVATAR * k, ay + MOCK_AVATAR * k], fill=MOCK_GREY)
    cx = MOCK_COL_LEFT * k
    d.rounded_rectangle([cx, 15 * k, cx + 120 * k, 25 * k], radius=5 * k, fill=MOCK_GREY)
    d.rounded_rectangle([cx, 34 * k, cx + 430 * k, 44 * k], radius=5 * k, fill=(41, 44, 47))
    return im.resize((MOCK_W, height_px), Image.LANCZOS)


def account_header(height_px):
    ss = 2
    W, H = MOCK_W * ss, height_px * ss
    k = MOCK_SCALE * ss
    im = Image.new("RGB", (W, H), MOCK_BG)
    d = ImageDraw.Draw(im)
    ax, ay, a = MOCK_PAD * k, 12 * k, MOCK_AVATAR * k
    av = ACCOUNT.get("avatar")
    if av:
        try:
            src = Image.open(av).convert("RGB")
            s = min(src.size)
            src = src.crop(((src.width - s) // 2, (src.height - s) // 2, (src.width + s) // 2, (src.height + s) // 2))
            src = src.resize((a, a), Image.LANCZOS)
            m = Image.new("L", (a, a), 0)
            ImageDraw.Draw(m).ellipse([0, 0, a - 1, a - 1], fill=255)
            im.paste(src, (ax, ay), m)
        except Exception:
            av = None
    if not av:
        d.ellipse([ax, ay, ax + a, ay + a], fill=MOCK_GREY)
    cx = MOCK_COL_LEFT * k
    fb, fr = _font(15 * k, bold=True), _font(15 * k)
    name = ACCOUNT.get("name") or ACCOUNT.get("handle") or ""
    y = 12 * k
    d.text((cx, y), name, font=fb, fill=(231, 233, 234))
    hx = cx + fb.getlength(name) + 4 * k
    handle = ACCOUNT.get("handle")
    meta = (f"@{handle.lstrip('@')} · now" if handle else "now")
    d.text((hx, y), meta, font=fr, fill=(113, 118, 123))
    y += 26 * k
    if ACCOUNT.get("text"):
        for line in _wrap(ACCOUNT["text"], fr, X2_COLUMN_PX * k):
            d.text((cx, y), line, font=fr, fill=(231, 233, 234))
            y += 20 * k
    return im.resize((MOCK_W, height_px), Image.LANCZOS)


def label(draw, xy, text):
    draw.text(xy, text, font=get_font(S(10.5)), fill=MOCK_LABEL)


def mockup_x2(panels, asp):
    tw, th = S(X2_TILE_PX), S(X2_TILE_PX / asp)
    gap = S(X2_GAP_PX)
    hh = S(MOCK_HEADER_H)
    H = hh + th + S(16)
    im = Image.new("RGB", (MOCK_W, H), MOCK_BG)
    im.paste(post_header(hh), (0, 0))
    grp = Image.new("RGB", (2 * tw + gap, th), MOCK_BG)
    for i, p in enumerate(panels):
        grp.paste(p.resize((tw, th), Image.LANCZOS), (i * (tw + gap), 0))
    im.paste(grp, (S(MOCK_COL_LEFT), hh), rounded_mask(grp.width, grp.height, S(MOCK_CORNER)))
    return im


def mockup_carousel(panels, asp):
    n = len(panels)
    tw, th = round(CAR_TILE_PX * MOCK_SCALE), S(CAR_TILE_PX / asp)
    step = (CAR_TILE_PX + CAR_GAP_PX) * MOCK_SCALE
    hh = S(MOCK_HEADER_H)
    vpw = min(S(CAR_VISIBLE_PX), MOCK_W - S(MOCK_COL_LEFT))
    # row 2 geometry
    full_w = (n * CAR_TILE_PX + (n - 1) * CAR_GAP_PX) * MOCK_SCALE
    avail = MOCK_W - 2 * S(MOCK_PAD * 2)
    sc = min(1.0, avail / full_w)
    t2w, t2h = round(tw * sc), round(th * sc)
    step2 = step * sc
    strip_w = round(step2 * (n - 1)) + t2w
    lab_h = 0
    row2_top = hh + th + S(16) + S(14) + lab_h
    H = row2_top + t2h + S(24)
    im = Image.new("RGB", (MOCK_W, H), MOCK_BG)
    im.paste(post_header(hh), (0, 0))
    lane = Image.new("RGB", (round(step * (n - 1)) + tw, th), MOCK_BG)
    for i, p in enumerate(panels):
        lane.paste(p.resize((tw, th), Image.LANCZOS), (round(i * step), 0))
    vp = lane.crop((0, 0, vpw, th)) if lane.width >= vpw else lane
    im.paste(vp, (S(MOCK_COL_LEFT), hh), rounded_mask(vp.width, vp.height, S(MOCK_CORNER)))
    d = ImageDraw.Draw(im)
    y = hh + th + S(16)
    d.line([(0, y), (MOCK_W, y)], fill=(47, 51, 54), width=2)
    strip = Image.new("RGB", (strip_w, t2h), MOCK_BG)
    for i, p in enumerate(panels):
        strip.paste(p.resize((t2w, t2h), Image.LANCZOS), (round(i * step2), 0))
    im.paste(strip, (S(MOCK_PAD * 2), row2_top),
             rounded_mask(strip.width, strip.height, max(4, round(S(MOCK_CORNER) * sc))))
    return im


def mockup_ig(panels, asp):
    n = len(panels)
    m = S(16)
    sep = 3
    pwm = (MOCK_W - 2 * m - (n - 1) * sep) // n
    phm = round(pwm / asp)
    H = m + phm + S(10) + 14 + m
    im = Image.new("RGB", (MOCK_W, H), MOCK_BG)
    d = ImageDraw.Draw(im)
    x0 = (MOCK_W - (n * pwm + (n - 1) * sep)) // 2
    d.rectangle([x0, m, x0 + n * pwm + (n - 1) * sep, m + phm], fill=(60, 60, 64))
    for i, p in enumerate(panels):
        im.paste(p.resize((pwm, phm), Image.LANCZOS), (x0 + i * (pwm + sep), m))
    cy = m + phm + S(10) + 7
    gapd, r = 20, 5
    tot = (n - 1) * gapd
    for i in range(n):
        cx = MOCK_W / 2 - tot / 2 + i * gapd
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 149, 246) if i == 0 else (85, 85, 90))
    return im


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="split.py",
        description="Split one image into seamless panels for an X multi-image post "
                    "or an Instagram carousel. Writes panels (01.jpg...), seams.jpg "
                    "and mockup.jpg into OUTDIR (old NN.jpg/NN.png there are removed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""layouts:
  x2           2 tiles side by side in X's 516 px column (6 px gap).
  x-carousel   3-10 tiles in X's swipe carousel (6 px gap, aspect <= 1.1).
  ig-carousel  2-20 panels, 1080x1350 (4:5) or 1080x1080 (1:1), no gap.
--gap auto drops 6/255 (x2) or 6/305.8 (carousel) of a panel width between panels
so lines crossing a seam line up despite X's gap; use none for the mobile app.
--fit cover crops the image to the strip aspect around --focus (0..1 point of the
source); --fit contain keeps the whole image and fills the rest with --bg.""")
    p.add_argument("image")
    p.add_argument("-o", "--outdir", required=True)
    p.add_argument("--layout", required=True, choices=["x2", "x-carousel", "ig-carousel"])
    p.add_argument("--panels", type=int, help="x2: always 2; carousels: default 3")
    p.add_argument("--aspect", type=parse_aspect,
                   help="panel aspect W:H (x2: from image, clamped .6-1.2; x-carousel 3:4; ig 4:5)")
    p.add_argument("--fit", choices=["cover", "contain"], default="cover")
    p.add_argument("--focus", type=parse_focus, default=None, help="x,y in 0..1 (default 0.5,0.5; seam-vs-focus warnings only when given)")
    p.add_argument("--bg", type=parse_bg, default="auto", help="contain fill: auto|blur|mirror|#hex")
    p.add_argument("--gap", default="auto", help="auto|none|<fraction of panel width>")
    p.add_argument("--format", choices=["jpg", "png"], default="jpg")
    p.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE, help="max panel long side px")
    p.add_argument("--name", help="mockup: display name to show instead of the grey skeleton")
    p.add_argument("--handle", help="mockup: @handle")
    p.add_argument("--avatar", help="mockup: path to a profile picture")
    p.add_argument("--text", help="mockup: the post's text")
    return p


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    global MOCK_HEADER_H
    if a.name or a.handle or a.avatar or a.text:
        ACCOUNT.update({k: v for k, v in (("name", a.name), ("handle", a.handle), ("avatar", a.avatar), ("text", a.text)) if v})
        MOCK_HEADER_H = account_header_h()
    warns = []
    layout = a.layout
    img = load_image(a.image)
    iw, ih = img.size
    imgA = iw / ih

    # panels
    if layout == "x2":
        if a.panels not in (None, 2):
            ap.error("x2 always has exactly 2 panels")
        N = 2
    elif layout == "x-carousel":
        N = a.panels or 3
        if not 2 <= N <= CAR_MAX_PANELS:
            ap.error(f"x-carousel supports 2..{CAR_MAX_PANELS} panels")
        if N == 2:
            warns.append("x-carousel with 2 panels: X shows 2 images as 'x2' (different geometry); use --layout x2")
    else:
        N = a.panels or 3
        if not 1 <= N <= IG_MAX_PANELS:
            ap.error(f"ig-carousel supports 1..{IG_MAX_PANELS} panels")

    # gap fraction (of panel width)
    if layout == "ig-carousel":
        gf = 0.0
        if a.gap not in ("auto", "none"):
            warns.append("ig-carousel never has a gap; --gap ignored")
    elif a.gap == "none":
        gf = 0.0
    elif a.gap == "auto":
        gf = X2_GAP_PX / X2_TILE_PX if layout == "x2" else CAR_GAP_PX / CAR_TILE_PX
    else:
        try:
            gf = float(a.gap)
        except ValueError:
            ap.error("--gap must be auto, none or a number")
        if not 0 <= gf <= 0.5:
            ap.error("--gap fraction must be within 0..0.5")

    # aspect
    if a.aspect:
        asp = a.aspect
    elif layout == "x2":
        asp = min(X2_ASPECT_RANGE[1], max(X2_ASPECT_RANGE[0], imgA / (2 + gf)))
    elif layout == "x-carousel":
        asp = CAR_DEFAULT_ASPECT
    else:
        asp = IG_DEFAULT_ASPECT
    if layout == "x2" and not X2_ASPECT_RANGE[0] <= asp <= X2_ASPECT_RANGE[1]:
        warns.append(f"panel aspect {asp:.2f} is outside {X2_ASPECT_RANGE}: X will crop the tiles")
    if layout == "x-carousel" and asp > CAR_MAX_ASPECT:
        warns.append(f"panel aspect {asp:.2f} > {CAR_MAX_ASPECT}: X crops wide carousel tiles (to 412 px wide); seams will not match the mockup")
    if layout == "ig-carousel" and not any(abs(asp - v) < 0.01 for v in IG_ASPECTS):
        warns.append("Instagram carousels expect 4:5 or 1:1 panels")

    focus_given = a.focus is not None
    fx, fy = a.focus or (0.5, 0.5)
    total_units = N + (N - 1) * gf           # strip width in panel widths
    Sasp = total_units * asp                 # strip aspect (w/h)

    # source-space frame (crop box for cover, canvas for contain)
    if a.fit == "cover":
        if imgA >= Sasp:
            bw, bh = ih * Sasp, ih
        else:
            bw, bh = iw, iw / Sasp
    else:
        if imgA >= Sasp:
            bw, bh = iw, iw / Sasp
        else:
            bw, bh = ih * Sasp, ih
    nat_pw = bw / total_units
    nat_ph = nat_pw / asp
    long_nat = max(nat_pw, nat_ph)

    if layout == "ig-carousel":
        pw = IG_W
        scale = pw / nat_pw
    else:
        scale = 1.0
        if long_nat > a.max_side:
            scale = a.max_side / long_nat
        elif long_nat < MIN_LONG_SIDE:
            scale = min(MAX_UPSCALE, MIN_LONG_SIDE / long_nat)
        pw = max(2, round(nat_pw * scale))
    ph = max(2, round(pw / asp))
    if max(pw, ph) > a.max_side and layout != "ig-carousel":
        r = a.max_side / max(pw, ph)
        pw, ph = max(2, round(pw * r)), max(2, round(ph * r))
    scale_eff = pw / nat_pw
    if scale_eff > 1.001:
        warns.append(f"source is small: panels upscaled x{scale_eff:.2f} (LANCZOS); "
                     f"native panel would be {nat_pw:.0f}x{nat_ph:.0f}px")
    g_out = gf * pw
    step = pw + g_out
    xs = [round(i * step) for i in range(N)]
    Wc = xs[-1] + pw
    g_src = gf * nat_pw

    # canvas
    imgrect = None
    if a.fit == "cover":
        S2 = Wc / ph
        if imgA >= S2:
            bw, bh = ih * S2, ih
        else:
            bw, bh = iw, iw / S2
        x0 = min(max(fx * iw - bw / 2, 0), iw - bw)
        y0 = min(max(fy * ih - bh / 2, 0), ih - bh)
        canvas = img.resize((Wc, ph), Image.LANCZOS, box=(x0, y0, x0 + bw, y0 + bh))
        focus_c = ((fx * iw - x0) * Wc / bw, (fy * ih - y0) * ph / bh)
        fill_desc = f"cover, source crop {bw:.0f}x{bh:.0f} at ({x0:.0f},{y0:.0f}) of {iw}x{ih}"
    else:
        f = min(Wc / iw, ph / ih)
        nw, nh = min(Wc, round(iw * f)), min(ph, round(ih * f))
        ox = round((Wc - nw) * fx)
        oy = round((ph - nh) * fy)
        mode = a.bg
        if mode == "auto":
            col = border_color(img)
            mode = "#%02x%02x%02x" % col if col else "blur"
        bgim = make_background(img, (Wc, ph), mode, (fx, fy), (ox, oy, nw, nh))
        canvas = bgim
        canvas.paste(img.resize((nw, nh), Image.LANCZOS), (ox, oy))
        imgrect = (ox, oy, ox + nw, oy + nh)
        focus_c = (ox + fx * nw, oy + fy * nh)
        free = "none" if (nw >= Wc and nh >= ph) else ("sides" if nw < Wc else "top/bottom")
        fill_desc = f"contain, bg={mode}, filled area: {free}"

    # focus vs seams
    for i in range(N - 1 if focus_given else 0):
        r, l = xs[i] + pw, xs[i + 1]
        if (r - FOCUS_SEAM_WARN * pw) <= focus_c[0] <= (l + FOCUS_SEAM_WARN * pw) \
                and 0 <= focus_c[1] <= ph:
            warns.append(f"seam {i + 1}|{i + 2} is close to the focus point: move --focus or change panel count/aspect")

    # write panels
    os.makedirs(a.outdir, exist_ok=True)
    for fn in os.listdir(a.outdir):
        if re.fullmatch(r"\d\d\.(jpg|png)", fn):
            os.remove(os.path.join(a.outdir, fn))
    panels = []
    for i in range(N):
        p = canvas.crop((xs[i], 0, xs[i] + pw, ph))
        assert p.size == (pw, ph)
        panels.append(p)
        fn = os.path.join(a.outdir, f"{i + 1:02d}.{a.format}")
        if a.format == "jpg":
            p.save(fn, quality=JPEG_QUALITY, subsampling=0)
        else:
            p.save(fn)

    # seams.jpg
    if a.fit == "cover":
        k = min(1.0, SEAMS_MAX_W / iw, 2000 / ih)
        draw_seams_preview(os.path.join(a.outdir, "seams.jpg"), img, k,
                           (x0, y0, x0 + bw, y0 + bh), xs, pw, ph, k * bw / Wc,
                           focus_c, None)
    else:
        k = min(1.0, SEAMS_MAX_W / Wc, 2000 / ph)
        draw_seams_preview(os.path.join(a.outdir, "seams.jpg"), canvas, k,
                           (0, 0, Wc, ph), xs, pw, ph, k, focus_c, imgrect)

    # mockup.jpg
    if layout == "x2":
        mk = mockup_x2(panels, pw / ph)
    elif layout == "x-carousel":
        mk = mockup_carousel(panels, pw / ph)
    else:
        mk = mockup_ig(panels, pw / ph)
    mk.save(os.path.join(a.outdir, "mockup.jpg"), quality=92)

    # summary
    print(f"layout: {layout} | panels: {N} | panel size: {pw}x{ph}px "
          f"(aspect {pw / ph:.3f}) | format: {a.format}")
    if gf > 0:
        print(f"gap: {gf * 100:.2f}% of panel width dropped per seam = "
              f"{g_out:.1f}px in output ({g_src:.1f}px of source); strip {Wc}px wide")
    else:
        print("gap: none (plain cut)")
    print(f"fit: {fill_desc}; focus {fx:.2f},{fy:.2f}")
    print(f"files: {', '.join(f'{i + 1:02d}.{a.format}' for i in range(N))}, seams.jpg, mockup.jpg -> {a.outdir}")
    if warns:
        for w in warns:
            print("WARNING:", w)
    else:
        print("warnings: none")


if __name__ == "__main__":
    main()

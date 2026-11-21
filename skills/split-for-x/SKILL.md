---
name: split-for-x
description: Split one artwork or photo into panels that line up seamlessly in a multi-image post on X (Twitter) or in an Instagram carousel, with seams placed away from faces and text, and a mockup of the post. Use when the user wants to post a drawing, panorama or wide image across several images, asks how to crop art "so it looks even" on Twitter/X, or wants a seamless or panorama carousel.
---

# Split for X

You are the art director. The script cuts and draws previews; you decide how many panels, which format, where the seams fall and how to frame the image, after looking at it.

Script: `scripts/split.py` next to this file (Python 3, `pip install pillow numpy`, add `--break-system-packages` if pip asks). If it's missing, get it with `git clone --depth 1 https://github.com/thibaud360/split-for-x /tmp/sfx` and use `/tmp/sfx/skills/split-for-x/scripts/split.py`.

```
python split.py IMAGE -o OUT --layout x2|x-carousel|ig-carousel [--panels N] [--aspect 3:4]
                [--fit cover|contain] [--focus X,Y] [--bg auto|blur|mirror|#hex] [--gap auto|none|F]
                [--name NAME --handle HANDLE --avatar PIC --text "post text"]
```
It writes `01.jpg`, `02.jpg`… plus `seams.jpg` (the source with the cut lines) and `mockup.jpg` (how the post will look).

## How X shows several images (measured, desktop, Nov 2026)

- **2 images:** side by side, same height, filling the post width, with a thin gap. Good for images from about 1.3:1 to 2.4:1.
- **3 images or more:** a sideways swipe carousel. All images get the same height. Only the first panel and a slice of the second are visible before swiping. Panels wider than about 1.1:1 get cropped, so carousel panels must be portrait or square (3:4 is the safe default).
- X leaves a small gap between images. With `--gap auto` the script drops a matching sliver of the image at each seam, so lines continue as if seen through a window. The phone app can render slightly differently. If the user says lines don't meet on their phone, re-run with `--gap none`.

Instagram carousels show one panel per swipe with no gap: 4:5 (1080×1350) or 1:1.

## 1. Look

Open the image and note:
- its shape (aspect ratio) and size. Tiny sources will upscale soft; say so.
- where the important things are, as rough horizontal positions from 0 (left) to 1 (right): faces and especially eyes, hands, text, logos, the focal point.
- lines that cross the whole image (horizon, ground, a character's limbs). These are what make a seamless split satisfying.
- the edges: plain background (easy to extend) or busy (blur or crop instead).

## 2. Decide

- **Platform:** X unless the user says Instagram. If they say "both", make both.
- **Layout and panel count.** Each panel's share of the width is 1/N of the used area.
  - X, image about 1.3:1 to 2.4:1 → `x2`.
  - X, wider than about 2.2:1 → `x-carousel`, N ≈ image aspect ÷ 0.75, between 3 and 6. More panels means a longer swipe, so fewer is usually better.
  - Instagram → `ig-carousel`, N ≈ image aspect ÷ 0.8, 4:5 panels.
  - Tall images (portrait drawings, comic pages) don't split seamlessly sideways. Suggest posting them whole, or splitting comic panels in reading order, which is not seamless.
- **Seams.** The cuts sit at 1/N, 2/N… of the used area. If one falls on a face, eyes, a hand or text, fix it: move `--focus`, change N, change `--aspect`, or switch to `--fit contain` and let the background extend. A seam through a body at the waist or through scenery is fine; through an eye never is.
- **The first panel is the thumbnail.** On X only panel 1 (plus a sliver of panel 2) shows in the feed, so it should hold the hook: a face, the title, the most striking part. Shift `--focus` to make that happen when the composition allows.
- **Fit.** `cover` crops to the exact strip shape (cleanest when little is lost). `contain` keeps the whole artwork and fills around it. Use `--bg` with a `#hex` matching a plain background (the most invisible option), `blur` for photos, or `mirror` for textures.

## 3. Run and check

Run the script, then **open `seams.jpg` and `mockup.jpg` and look at them**:
- No seam through eyes, faces, hands or text?
- Does panel 1 work alone as the thumbnail?
- In the full strip, do lines continue across seams?
- Does any fill look cheap or obvious?

Fix and re-run, two passes at most.

## 4. Deliver

- Send the panels in order and the mockup. If the user gave their name, handle, profile picture or the post's text, pass them with `--name`, `--handle`, `--avatar` and `--text` so the mockup shows their real post; otherwise it shows a neutral placeholder.
- Tell the user to upload them **in order 01, 02, 03… in a single post**, and to check the order in the composer before posting.
- Write one short alt text per panel (what's in that slice). X lets you add it per image, and it helps reach and accessibility.
- Mention that the gaps were measured on X desktop, and that re-running with `--gap none` fixes things if the phone shows a tiny offset.
- Offer one line of options: another panel count, Instagram version, different framing.

## Without the script

If the script isn't available but you can run Python, do the same with Pillow: crop to the strip (N·panel width + (N−1)·gap), cut equal panels, skip the gap strip between them on X (about 2% of a panel's width for 3:4 carousel panels, about 2.4% for the 2-image layout), save the panels at identical sizes, and still check the seams yourself before delivering.

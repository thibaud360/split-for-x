Copy everything below the line, paste it into Claude (with code execution / file creation turned on), and attach your image.

---

Split the attached image into panels that line up seamlessly when I post them together on X (Twitter). Work like an art director, not a cropping tool.

1. **Look.** Tell me in 2 lines the image's shape and where the important parts sit horizontally (faces, eyes, hands, text, focal point).
2. **Decide.**
   - Images from about 1.3:1 to 2.4:1 → 2 panels side by side, same height.
   - Wider images → a swipe carousel of 3–6 portrait panels at 3:4 (panels wider than about 1.1:1 get cropped by X).
   - Instagram → 4:5 panels, one per swipe.
   - Place the seams so none crosses eyes, faces, hands or text. Adjust the crop, the number of panels, or extend the background to make that work.
   - Make panel 1 the strongest, since it's the thumbnail people see first.
3. **Cut.** With Python and Pillow: crop to the strip, cut equal panels with identical pixel sizes, and on X skip a thin strip between panels to match the gap X shows (about 2% of a panel's width in the carousel, about 2.4% for 2 panels). Name them 01, 02, 03…
4. **Check.** Draw the seams on the original and a mockup of the post on a dark background, look at both, and fix anything off.
5. **Deliver.** The panels, the mockup, the order to upload them, and one short alt text per panel.

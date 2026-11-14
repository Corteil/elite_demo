"""Draw several stations' tunnels (docking and launching) side by side into
tests/shots/tunnels.png, to judge their shapes and spacing. Needs Pillow."""
import os, sys
_R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_R, 'tests')); sys.path.insert(0, os.path.dirname(_R))
import badge_stubs as bs
bs.install()
from badge_stubs import PilCtx
from ace_commander import cockpit
from PIL import Image, ImageDraw
D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tests', 'shots') + os.sep
seeds = [8, 1, 2, 3, 99, 200]
rows = [("dock start", True, 0.0), ("dock 0.05", True, 0.05), ("dock 0.35", True, 0.35), ("dock end", True, 1.0), ("launch 0.4", False, 0.4)]
W = 240
sheet = Image.new('RGB', (W * len(seeds), W * len(rows) + 20 * len(rows)), (40, 40, 40))
dr = ImageDraw.Draw(sheet)
for c, sd in enumerate(seeds):
    tun = cockpit.Tunnel(sd, "DODO" if sd == 99 else "CORIOLIS")
    for r, (lab, inward, f) in enumerate(rows):
        ctx = PilCtx(1)
        cockpit.draw_tunnel(ctx, f, f * 2.0, cockpit.STATION_COLOURS[sd % 8], inward, tun)
        ctx.finish(D + 'tmp.png')
        sheet.paste(Image.open(D + 'tmp.png').resize((W, W)), (c * W, r * (W + 20) + 20))
        dr.text((c * W + 4, r * (W + 20) + 4), "%d %s>%s>%s %s | %s" % ((sd,) + tun.names + (lab,)), fill=(255, 255, 255))
sheet.save(D + 'tunnels.png')

"""Make the "Satellite images by year" pictures for every featured lake in
advance, so they are on disk (reports/web/<lake_id>/) and the website opens
them instantly. Normally a year is rendered only when someone opens it.

Run from the project root (internet needed; already-made images are skipped):
    python ai_agent/scripts/prerender_images.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent.app import imagery, lake_analytics as la   # noqa: E402


def main():
    ids = sorted(la.featured_ids())
    t0, done, failed = time.time(), 0, 0
    for n, lake_id in enumerate(ids, start=1):
        for pick in imagery.year_picks(lake_id):
            if pick["missing"]:
                continue
            try:
                imagery.render(lake_id, pick["item_id"])
                done += 1
            except Exception as error:                  # noqa: BLE001 - one image never stops the rest
                failed += 1
                print(f"  {lake_id} {pick['year']}: {str(error)[:120]}", flush=True)
        print(f"[{n}/{len(ids)}] {lake_id} done ({time.time() - t0:.0f} s)", flush=True)
    print(f"Rendered {done} images, {failed} failed.")


if __name__ == "__main__":
    main()

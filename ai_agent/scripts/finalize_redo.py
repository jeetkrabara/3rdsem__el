"""After the full redo: put the new results in place and rerun the summaries.

  python ai_agent/scripts/finalize_redo.py          # checks, swaps, reruns
  python ai_agent/scripts/finalize_redo.py --check  # only compare, change nothing
"""
import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path


def stats(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    items = {(r["source"], r["item_id"]) for r in rows}
    ok = sum(1 for r in rows if r["status"] == "ok")
    lakes = {r["lake_id"] for r in rows}
    return {"rows": len(rows), "items": len(items), "ok": ok, "lakes": len(lakes)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="swap even if the new run has fewer scenes")
    args = ap.parse_args()
    root = Path(args.root)
    old = root / "reports" / "lakes" / "lake_water_timeseries.csv"
    new = root / "reports" / "lakes_v2" / "lake_water_timeseries.csv"
    if not new.exists():
        raise SystemExit(f"No new results at {new}. Is the redo finished?")
    n = stats(new)
    print(f"new run : {n['rows']:,} rows, {n['items']} scenes, {n['lakes']} lakes, "
          f"{n['ok']:,} rows with status ok")
    if old.exists():
        o = stats(old)
        print(f"current : {o['rows']:,} rows, {o['items']} scenes, {o['lakes']} lakes, "
              f"{o['ok']:,} rows with status ok")
        if n["items"] < 0.95 * o["items"] and not args.force:
            raise SystemExit(
                f"\nThe new run has fewer scenes ({n['items']}) than the current "
                f"results ({o['items']}). It may be unfinished: rerun the redo "
                "command (it resumes), or use --force.")
    if args.check:
        print("\n--check only: nothing changed.")
        return
    if old.exists():
        backup = old.with_name("lake_water_timeseries.backup_before_v2.csv")
        if not backup.exists():
            shutil.copy(old, backup)
            print(f"\nbacked up current results to {backup}")
    old.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(new, old)
    print(f"new results are now in {old}")
    script = Path(__file__).resolve().parent / "lake_seasonality.py"
    if script.exists() and args.root == str(Path(__file__).resolve().parents[1]):
        print("\nrerunning seasonality with the date check ...\n")
        subprocess.run([sys.executable, str(script), "--exclude-flagged"], check=False)
    print("\nNEXT: restart the AI service (pkill -f uvicorn, then start both again),"
          "\n      then python ai_agent/scripts/gee_crosscheck.py --top 12 --per-lake 8"
          "\n      and python ai_agent/scripts/make_validation_sample.py --n 28 --seed 1")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Copy the exact official Lucide SVGs listed in ICON_MANIFEST.json into ./lucide/.

Usage:
  npm pack lucide-static@<PINNED_VERSION>      # on a machine with network access
  tar xzf lucide-static-*.tgz                  # -> package/icons/*.svg, package/LICENSE
  python tools/fetch_lucide_icons.py package/icons

Never hand-edit the copied files. Missing names are reported with the manifest fallback
(if any); the Author must approve any substitution.
"""
import json, shutil, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "assets" / "icons"
if len(sys.argv) != 2:
    sys.exit(__doc__)
src = Path(sys.argv[1])
if not src.is_dir():
    sys.exit(f"Not a directory: {src}")
manifest = json.loads((root / "ICON_MANIFEST.json").read_text())
out = root / "lucide"
out.mkdir(exist_ok=True)
missing = []
for ic in manifest["icons"]:
    if ic["source"] != "lucide":
        continue
    f = src / f'{ic["name"]}.svg'
    if f.is_file():
        shutil.copyfile(f, out / f.name)
    else:
        fb = [n for n in ic.get("fallback", []) if (src / f"{n}.svg").is_file()]
        missing.append((ic["id"], ic["name"], fb))
lic = next((p for p in (src.parent / "LICENSE", src.parent / "LICENSE.txt") if p.is_file()), None)
if lic:
    shutil.copyfile(lic, root / "LICENSE-LUCIDE.txt")
else:
    print("WARNING: Lucide LICENSE not found next to the icons folder; add it manually (ISC notice must ship).")
total = sum(i["source"] == "lucide" for i in manifest["icons"])
print(f"Copied {total - len(missing)}/{total} Lucide icons.")
for id_, name, fb in missing:
    print(f"MISSING {id_}: '{name}'  available fallbacks: {fb or 'none'}")
sys.exit(1 if missing else 0)

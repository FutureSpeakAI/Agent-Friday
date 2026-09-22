"""One-shot registry update, 2026-09-17 (docs/design/active/model-soup.md
step 2, registry half only). Backs up models.json with a timestamp, describes
the FridayWeaver seat as base + adapter + projector with local-disk
preferences, retires the 2026-09-02 merged checkpoint, and sets the stale
residency/gguf_models.json aside. Copies no weights. Re-runnable."""
import json
import os
import shutil
import time
from pathlib import Path

rt = Path(os.path.expanduser("~/.friday/runtime"))
stamp = time.strftime("%Y%m%d-%H%M%S")
mj = rt / "models" / "models.json"
bak = mj.with_name("models.json.backup-" + stamp)
shutil.copy2(mj, bak)
d = json.loads(mj.read_text(encoding="utf-8"))
m = d["models"]
share = r"\\wsl.localhost\Ubuntu-24.04\root\friday-models-storage\gguf-intermediate"
local = str(rt / "models" / "gguf")

fw = m["gemma4:e2b-fridayweaver-1.0"]
fw["lora"] = share + r"\fridayweaver-lora.gguf"
fw["local_files"] = {
    "path": local + r"\base-e2b-q8_0.gguf",
    "lora": local + r"\fridayweaver-lora.gguf",
    "mmproj": local + r"\mmproj-e2b.gguf",
}
fw["label"] = "FridayWeaver-1.0"
fw["origin"]["note"] = (
    "Base Gemma 4 E2B (Q8_0) + FridayWeaver-1.0 LoRA (trained 2026-09-07/09, "
    "8986 steps) applied at serve time via llama-server --lora, with the E2B "
    "vision+audio tower (mmproj). As of 2026-09-17 the residency Arbiter "
    "spawns this seat itself with --lora and --mmproj "
    "(docs/design/active/model-soup.md). The files are preferred from "
    "local_files under ~/.friday/runtime/models/gguf/ when present and fall "
    "back to the WSL share otherwise; the share is unreachable as this note "
    "is written, so the seat is absent until the copy lands.")
fw["origin"]["registry_updated"] = "2026-09-17"

old = m["gemma4:e2b-friday-v1"]
old["retired"] = (
    "2026-09-02 merged checkpoint, possibly numerically identical to the base "
    "because of the merge.py adapter-load bug found 2026-09-09 "
    "(Friday-Models/docs/DECISIONS.md); no mmproj. Retired 2026-09-17 so "
    "local_seats.resolve() can never fall back to it. File left in place.")
old.setdefault("retired_at", time.time())

tmp = mj.with_suffix(".json.tmp")
tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
os.replace(tmp, mj)

gm = rt / "residency" / "gguf_models.json"
if gm.exists():
    gm.rename(gm.with_name("gguf_models.json.stale-" + stamp))
print("backup:", bak.name)
print("registry files:", sorted(p.name for p in (rt / "residency").glob("gguf_models*")))
print("fridayweaver lora:", fw["lora"])
print("friday-v1 retired:", bool(old.get("retired")))

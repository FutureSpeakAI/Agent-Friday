# A heavy local job swaps seats through one routine, and ComfyUI keeps the display reserve

**Date:** 2026-10-01
**Status:** decided, implemented in `services/residency_arbiter.py` (`Arbiter.heavy_job`), `services/local_image.py`, `services/build_hours.py`; tests in `tests/unit/test_heavy_job_swap.py`.

## The rule

Any job that needs the graphics card to itself (an image, a video, music, a
leased reasoner) runs through `Arbiter.heavy_job`, which performs, in order
and with each step written to a receipt under `runtime/residency/heavy_jobs/`:

1. record which seats are resident, or that the brain is parked for build hours;
2. evict them cleanly and take the lease;
3. run the job on its own thread, bounded by a timeout;
4. confirm the job finished: it returned, and every output file it reported exists and is not empty, or its failure is recorded;
5. evict the job's model (ComfyUI stops; a leased seat is evicted);
6. restore the previous seats, unless build hours own them;
7. verify the restore with a real completion on each restored seat.

Every failure path, including a timeout, still runs steps 5 to 7. A restore
that fails is tried once more from a clean card; a second failure marks the
Arbiter degraded and says so in the receipt. Nothing calls `grant()` and
`release()` around a job by hand.

During build hours (the flag file the build-hours daemon writes; path
configurable with `FRIDAY_BUILD_HOURS_FLAG`) the previous state is *parked*:
the Arbiter does not relaunch a pinned GPU language seat at boot, on release,
on rollback or after a job, and the verification of the restore is that no
seat of Friday's is answering on its port.

## Why: what the z-image renders actually died of

The forensic task records show every Z-Image Turbo render reaching ComfyUI's
sampling node (step 0 of 8, once step 4 of 8) and ending with status
`cancelled`, never with a ComfyUI error. The cancellation came from Friday:

```
machine_monitor: display breached -- 324 MiB free against a 2560 MiB display reserve (short)
[arbiter] display reserve breached (324 MiB free ...) -- cancelled 1 render(s), released every lease (headroom.md §7, HR7)
[arbiter] display reserve breached (641 MiB free ...) -- cancelled 1 render(s), released every lease
```

ComfyUI's dynamic VRAM loading stages the 7,671 MB text encoder and the
5,869 MB diffusion model and uses the card up to its own default reserve of
about 600 MB. The machine monitor, sampling every five seconds while a lease
is held, reads under 2,560 MiB free as a display-reserve breach, and the
monitor response cancels the in-flight render and releases the lease. The
same model on the same card renders to completion when ComfyUI runs alone
(a full 8-step render in 178 s cold, 27 s warm, recorded in the ComfyUI log
from the runtime's standalone run), because nothing is watching the reserve
then.

The other suspects were ruled out by the same records: the brain was not
resident during the attempts (the build-hours daemon had parked it; the card
showed 1.2 to 1.5 GB used before each job), the weights load with
`torch.float8_e4m3fn` and a bfloat16 manual cast, the workflow's text encoder
and VAE match the ones that rendered standalone, and ComfyUI has no watchdog
that fired. System RAM and disk were under pressure at the same time (the
system volume fell to 1,470 MiB free during one attempt), which is why the
rule also refuses a lease under the disk floor, but the cancel was the
display rule's.

Two consequences follow, both now in code: ComfyUI is launched with
`--reserve-vram` set to the reconciled display reserve, so the renderer and
the monitor enforce one number; and ComfyUI's output goes to
`runtime/logs/comfyui.log` instead of being discarded, so the next failure
has a log.

## Why the restore has to be verified, and why build hours are special

After each cancelled render the release relaunched the brain, and the
build-hours daemon killed it within seconds ("a seat came back; re-parking
it", ten times in one night). `endpoints.json` then pointed at a port nothing
served, and every turn logged that the seat was not serving. A restore that
is not verified by a completion is a claim; a relaunch during build hours is
a relaunch against the owner's own daemon. Both are now refused by the rule.

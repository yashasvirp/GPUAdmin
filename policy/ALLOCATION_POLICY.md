# Compute Ledger — GPU Allocation Policy

### System specifications

- 20 NVIDIA A100 80GB GPUs across 2 nodes
- 100 active researchers under an ANRF grant
- Grant window: 3 months, fixed compute budget
- Usage patterns: 2 hours (eval) to 7 days (pretraining), intermittent

## System assumptions

```
20 GPUs × 24 h/day × 90 days = 43,200 GPU-hours
```

- This is a ceiling, not a target — planning assumes well below 100%
  utilization; idle time, maintenance, and queueing are normal.
- Modeled on Slurm-style clusters: researchers submit batch jobs, monitor
  status, cancel as needed. Quotas, priority, preemption, and reserved
  capacity are all enforced server-side, visible only as the limits and
  notifications a researcher experiences.
- The **Compute Ledger CLI** (`ledger.py`, Component B) simulates this same
  request → approve → run → charge lifecycle for local testing, without a
  full Slurm accounting stack.

| Tier | Duration | Typical use |
|---|---|---|
| Small | 0–36 h | Quick eval |
| Medium | 36–72 h | Fine-tuning, mid-length experiments |
| Large (pretraining) | 3–7 days | Long unattended batch runs |

## 1. Request and approval workflow

- Researchers register once, receiving cluster credentials, head-node
  access, and per-user storage for checkpoints/outputs.
- A request is a standard Slurm submission — `--gres=gpu:N`, `--time=...`.
  Mirrors what the CLI collects: `ledger.py request --user rao_lab --gpus 4 --hours 96`.
- **Small/medium jobs (≤72h):** auto-admitted the moment GPUs and quota
  (§2) are available — no human review.
- **Large jobs (>72h):** require PI sign-off from the GPU admin before
  submission is accepted, keeping the highest-impact jobs under review
  while routine work flows through unattended.
- Auto-admitted jobs start within the scheduler's normal loop (under a
  minute once free); large-job sign-off targets same-business-day turnaround.

## 2. Fair-use constraints

- Fairness is enforced on **cumulative GPU-hours per PI over the full
  grant**, not per-job limits — a per-job cap alone doesn't stop continuous
  resubmission over 90 days.
- **Quota model:**
  - 15% of budget (6,480 h) held as a flexible reserve; 85% (36,720 h)
    split as baseline per-PI quotas (~367 h/PI), adjusted by declared
    usage pattern (eval-heavy vs. pretraining-heavy).
  - Submissions exceeding quota are rejected outright at request time.
  - Unspent quota returns to the reserve, opened for reallocation in the
    grant's final month.
  - Admins retain a logged manual override for exceptional cases.
- **Per-job guardrails:** default max 2 GPUs / 24h; larger needs go
  through the large-tier path in §1.
- **Queue priority:** combines age-in-queue with fairshare (recent heavy
  usage lowers priority, recovering as usage tapers) — also naturally
  favors requesters with fewer active jobs, without a separate rule.

## 3. Preemption rules

- **(a) Self-overrun:** scheduler auto-terminates at the wall-clock limit;
  Ledger flags `overrun` on next `status` check.
  - Automated warnings at 50%/80% of allotted duration.
  - If capacity is idle, admin may grant a bounded extension (up to 6h)
    instead of forcing termination.
- **(b) External preemption:** a higher-priority job needs the GPU early.
  - Process gets a short warning (minutes) to checkpoint and exit before
    being force-stopped; job is **requeued**, not deleted, and resumes
    once capacity frees (if it checkpointed in time).
  - Short notice applies only to genuine last-minute reclamation. Planned
    windows (e.g. a scheduled event) use advance scheduling instead —
    researchers are told ahead of time not to submit jobs overlapping the
    window, so nothing normally needs force-killing. A job that
    unexpectedly overlaps still falls back to this same short-notice path.
- **Preemption-safety:** long-running jobs must implement checkpoint/resume
  and handle SIGTERM gracefully, writing to a fixed path convention
  (`<user_id>/<job_id>/checkpoint/`) so the system — and the admin — can
  verify a checkpoint exists.
- **No refund for lost work:** GPU-hours are charged for time held,
  regardless of checkpoint success — mirrors real billing (occupancy, not
  completed work) and incentivizes checkpointing without policing code.

## 4. Budget tracking

Tracked in GPU-hours per job, aggregated per PI and system-wide.

- **Burn rate:** rolling 7-day average vs. baseline (43,200 ÷ 90 ≈ 480
  h/day). >1.5× baseline triggers an early warning; <0.5× flags
  underused capacity for possible reallocation.
- **Monthly pacing cap:** any month exceeding 15,000 h (vs. ~14,400 target)
  alerts independently, catching a fast month early.
- **Absolute threshold:** at 80% consumed (`gpu_budget_remaining_percent
  < 20`), all users + admin notified, priority shifts toward PIs with more
  quota left. At 95–100%, new large-tier approvals freeze except by
  override.
- GPU hardware telemetry and job/budget accounting feed one dashboard, so
  hardware and budget problems are both visible from one place.

## 5. AISEhack burst (72h hackathon, 200 external participants)

- Tracked on a **separate account/budget** — doesn't draw against the
  100 researchers' 43,200h allocation.
- **Capacity carve-out:** admin reserves GPUs exclusively for the window,
  ahead of time.
  - 15 of 20 GPUs reserved for the hackathon.
  - 2 GPUs reserved for up to 2 exclusive high-priority research jobs
    (1 GPU each), unaffected.
  - 3 GPUs held flexible, assigned to whichever pool shows higher demand
    after day 1–2.
- Affected researchers notified 7–10 days ahead, confirmed 2 days prior.
  Participants offload non-GPU work to CPU and free sessions promptly.
- **Access model:** 15 × 72h = 1,080 GPU-hours across 200 participants
  (~5.4h/person if split evenly) — simultaneous access for all 200 isn't
  physical on unshared A100s.
  - Served via **time-boxed rotating slots** (45-min default runtime);
    GPU returns to rotation the moment a slot ends.
  - Heavier users naturally queue behind lighter ones.
  - MIG partitioning (where supported) can further increase concurrency.
  - Organizers may permit external resources (e.g. Colab) alongside
    reserved hardware.

# Compute Ledger — GPU Allocation Policy

## System overview

The cluster consists of 20 NVIDIA A100 80GB GPUs across 2 nodes, serving 100 active researchers under a fixed 3-month ANRF grant window. Usage ranges from short evaluation runs (~2 hours) to long pretraining jobs (up to 7 days), with intermittent, bursty demand in between rather than continuous per-user load.

The grant's fixed compute budget is set at the cluster's theoretical full-utilization capacity:

```
20 GPUs × 24 h/day × 90 days = 43,200 GPU-hours
```

This is a ceiling, not a target — planning assumes well below 100% utilization, since idle time, maintenance, and queueing are normal.

This policy is expressed in terms of **Slurm**, the workload manager operating the cluster. From a researcher's side, the interaction is the familiar job lifecycle: submit with `sbatch` (batch jobs) or `srun` (short, interactive work like a quick eval), watch it move from queued to running with `squeue`, and end it early with `scancel` if needed. Everything below this — quotas, priority, preemption, reserved capacity — is enforced server-side by the scheduler and the admin, and shows up to the researcher only as the limits and notifications they experience. The **Compute Ledger CLI** (`ledger.py`, Component B) implements a lightweight, single-binary simulation of this same request → approve → run → charge lifecycle, useful for local testing and for giving researchers a simple budget view without standing up a full Slurm accounting stack.

Jobs are grouped into three duration tiers, which drive approval and preemption rules throughout this document:

| Tier | Duration | Typical use |
|---|---|---|
| Small | 0–36 h | Quick eval / debugging, often interactive (`srun`) |
| Medium | 36–72 h | Fine-tuning, mid-length experiments |
| Large (pretraining) | 3–7 days | Long unattended batch runs (`sbatch`) |

## 1. Request and approval workflow

Researchers register once with the allocation system and receive cluster credentials, head-node access, and dedicated per-user storage for checkpoints and output artifacts. A request looks like a standard job submission:

```bash
#!/bin/bash
#SBATCH --job-name=resnet_pretrain
#SBATCH --gres=gpu:4
#SBATCH --time=3-00:00:00
srun python train.py
```

The information that matters for policy — requester/PI identity (tied to the account the job runs under), GPU count, and requested duration — is exactly what the scheduler uses to decide tier and downstream treatment, and mirrors the fields the Compute Ledger CLI collects: `ledger.py request --user rao_lab --gpus 4 --hours 72`.

**Approval is tiered by job size:**
- **Small and medium jobs (≤72 h)** are auto-admitted: the moment GPUs are free and the requester still has quota remaining (§2), the job moves from pending to running — visible to the researcher simply by watching `squeue`. No human reviews these.
- **Large jobs (>72 h, the pretraining tier)** require the PI to request sign-off from the GPU admin before submission is even accepted — in practice, the admin grants that account permission to submit into the large-job tier once they've reviewed the request, rather than the scheduler admitting it automatically. This keeps the highest-impact jobs — the ones that can consume days of shared budget in one run — under explicit human review, while routine work flows through without any waiting on a person.

Auto-admitted jobs typically start within the scheduler's normal loop (well under a minute once resources are free); large-job sign-off is targeted for same-business-day turnaround so a PI on a real deadline isn't stuck waiting.

## 2. Fair-use constraints

Fairness is enforced on **cumulative GPU-hours per PI over the full grant window**, not just per-job limits — a per-job cap alone doesn't stop a lab from resubmitting continuously for 90 days and consuming the entire budget between them.

**Quota model:** 15% of the total budget (6,480 GPU-hours) is held back as a flexible reserve pool; the remaining 85% (36,720 GPU-hours) is distributed as baseline per-PI quotas — an even split gives ~367 GPU-hours per researcher, adjusted up or down based on declared usage pattern (a PI running mostly short eval jobs is allotted less than one running sustained pretraining). Every submission is tied to an account/PI, and the scheduler's accounting system keeps a running cumulative total against that account; once the quota is exhausted, further submissions are rejected outright at request time rather than merely flagged to an admin afterward. Unspent quota from PIs who finish under budget is returned to the reserve pool, which opens up for reallocation to active projects during the final month of the grant, so unused capacity isn't sitting idle while other labs are still queued. Admins retain a manual override for exceptional cases, logged for auditability.

**Per-job guardrails** (secondary to the cumulative quota, preventing any single job from monopolizing capacity): default max 2 GPUs and 24 hours per job; jobs needing more go through the large-tier manual path in §1, up to the 7-day pretraining ceiling.

**Queue priority** combines two factors rather than pure first-come-first-served: age in queue (so nobody waits forever), and a fairshare weighting that automatically lowers an account's scheduling priority after a burst of heavy recent usage, then lets that priority recover as usage tapers off. A researcher can informally observe this working simply by noticing their job climbing the `squeue` ordering faster during a quiet stretch than during a period where their lab has been running several jobs back-to-back. This also naturally favors requesters with fewer currently-active jobs over those already running several, without needing a separate rule for it.

## 3. Preemption rules

Preemption has two distinct triggers, each with different notice expectations.

**(a) Self-overrun** — a job runs past its own requested duration. The scheduler terminates it automatically at the wall-clock limit it was submitted with; the Compute Ledger view flags this as `overrun` on the next `status` check so it's visible to the researcher without them needing to know exactly when their job hit its limit. Researchers get advance warning via automated email at 50% and 80% of allotted duration elapsed. If cluster capacity happens to be idle at the moment a job would be preempted for overrun, the admin may grant a bounded extension (up to 6 hours) rather than forcing immediate termination — no reason to kill a job for running long if nobody else needs the GPU right now.

**(b) External preemption** — a higher-priority job or a reserved event (e.g., AISEhack, §5) needs the GPU before the running job's requested time is up. The researcher's own process receives a warning signal a short window (a few minutes) before it is forcibly stopped, giving their training script a chance to write a checkpoint and exit cleanly instead of being killed mid-step; if it doesn't exit in that window, it is terminated outright. The job is then **requeued** — put back in the pending queue, not deleted — and resumes automatically once capacity frees up, provided it actually saved a checkpoint in that window. Because the whole point of this kind of preemption is reclaiming capacity quickly, notice here is necessarily short. Where the triggering event is planned well ahead of time (like a hackathon), affected researchers instead get 7–10 days' advance notice per §5, rather than a same-minute warning with no lead time.

**Preemption-safety requirements:** every long-running submission must implement checkpoint/resume logic and handle being stopped mid-run gracefully, and must write checkpoints to a predictable, fixed path convention (e.g. `<user_id>/<job_id>/checkpoint/`) so the system can verify a checkpoint exists before approving long jobs, and so a researcher — or the admin, when troubleshooting — can quickly confirm whether a preempted job has anything to resume from.

**No refund for lost work.** GPU-hours are charged for time the resource was held, regardless of whether the job checkpointed successfully. This mirrors how GPU-hours are billed generally — for exclusive occupancy of the hardware, not for useful work completed — and creates the right incentive (checkpoint your own long jobs) without the system needing to police individual training code.

## 4. Budget tracking

Budget is tracked in GPU-hours per job, aggregated per PI and system-wide by the scheduler's accounting system — researchers can check their own historical usage the same way they'd check past job accounting today, and the admin has the aggregate, cluster-wide view. Three complementary signals drive alerting:

- **Burn rate** — GPU-hours consumed per day, tracked as a rolling 7-day average against the expected baseline pace of 43,200 ÷ 90 ≈ **480 GPU-hours/day**. A sustained rate above 1.5× baseline (720 h/day) triggers an early warning — the budget is being consumed faster than it can sustainably last the full grant window, worth investigating before it becomes urgent. A rate below 0.5× baseline is informational, flagging underused capacity that could be pulled from the reserve pool and reallocated.
- **Monthly pacing cap** — a tighter, near-term check: any calendar month exceeding 15,000 GPU-hours (vs. the ~14,400/month even-pace target) alerts independently of the rate-deviation check above, catching a fast month even if the trailing 7-day average hasn't crossed the 1.5× line yet.
- **Absolute threshold** — at 80% of total budget consumed (`gpu_budget_remaining_percent < 20`), all active users and the admin are notified, and remaining allocation priority shifts further toward PIs with more quota left (reinforcing the fairshare behavior from §2). At 95–100% consumed, new large-tier approvals are frozen except by explicit admin override for near-completion critical work.

GPU hardware telemetry (temperature, utilization, ECC errors) and the scheduler's own job/budget accounting feed the same monitoring dashboard, so a hardware problem and a budget problem are both visible from one place rather than two disconnected systems. This is what Component C's `/metrics` endpoint and `alerts.yml` implement for this assignment's simplified deployment.

## 5. AISEhack burst (72-hour hackathon, 200 external participants)

Hackathon compute is tracked on a **completely separate account and budget** — it does not draw against the 100 researchers' 43,200-hour grant allocation, since hackathon participants are not grant researchers and shouldn't silently eat into a budget meant to last 90 days.

**Capacity carve-out for the 72-hour window:** ahead of the event, the admin reserves a block of GPUs exclusively for the hackathon — capacity set aside for a fixed time window and made unavailable to the regular research queue during that window, the same underlying idea as booking a conference room in advance rather than hoping one happens to be free.

- 15 of 20 GPUs are reserved for the hackathon for the duration of the window.
- 2 GPUs remain outside the reservation for a small number (max 2) of exclusive, high-priority ongoing research jobs, 1 GPU each, entirely unaffected by the hackathon.
- 3 GPUs are held in a flexible reserve, assigned to whichever pool (research or hackathon) shows higher demand after the first 1–2 days, based on observed usage.

**Access model:** 15 GPUs × 72 h = 1,080 GPU-hours of hackathon capacity across 200 participants (≈5.4 hours/person if evenly split) — simultaneous access for all 200 isn't physically possible on unshared A100s. Access is instead served through **time-boxed rotating slots**: participants submit short jobs (e.g., 45-minute default runtime) against the hackathon's own capacity pool, and once a slot ends — whether the job finishes naturally or the participant ends it early — that GPU immediately returns to the rotation for the next person in the queue. Heavier users naturally queue behind lighter ones rather than receiving a fixed static allocation. Where the hardware supports NVIDIA MIG (a way of hardware-partitioning a single GPU into several smaller, isolated instances), the reserved GPUs can additionally be split up to increase concurrency and reduce queueing pressure beyond the base 15-GPU rotation.

Regular researchers affected by the capacity carve-out are notified 7–10 days ahead of the hackathon window, with final confirmation 2 days prior. Participants are expected to offload non-GPU-bound work to CPU and free their session promptly on completion so capacity returns to the rotation queue immediately rather than sitting idle under one participant's name.

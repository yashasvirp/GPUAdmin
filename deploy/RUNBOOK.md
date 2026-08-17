# Runbook: Session Marked Overrun

**Ticket:** "My session shows overrun after 6 hours — is my job dead? Did I lose data?"

*`overrun` only means requested time has passed, not that the process was
killed. The ledger updates lazily (on a poll), so an already-ended process
can still show `overrun`.*

## 1. Initial Node Check (ledger drift detection)

- Get the job's PID via `ps` (by user/process name).
- Check `nvidia-smi` — is the process still holding GPU memory?
- If terminated but ledger still shows `overrun`: real drift - the system is still charging/holding a GPU that's actually free.

## 2. Process Verification

- Poll `nvidia-smi -l n` for a few samples (set n to any number).
- Not appearing → **terminated**.
- Appearing, high utilization → **active**.
- Appearing, ~0% utilization → **zombie** (holding resources, not computing).

## 3. Data Integrity

- Check the standard checkpoint path for existence and timestamp.
- Checkpoint present and <6h old, then mildly good news, resumable.
- Check stdout/stderr logs for errors or deadlock signs.
- If active, then data/checkpoints not compromised yet.

## 4. Researcher Communication

- **Terminated:** likely normal completion as we never auto-kill on overrun. Abnormal termination would show in logs; so, advise checking them.
- **Active, recent checkpoint:** advise ending the session (`ledger.py end`).
- **Active, stale checkpoint:** ask them to confirm once a fresh checkpoint saves, then end it.
- **Zombie:** admin terminates the process directly — shared resource, don't wait on the researcher.

## 5. System Improvements

- Push-based status updates (process notifies ledger on exit / heartbeat) instead of lazy polling — this eliminates drift at the source.
- Notify researcher on overrun; let them confirm checkpoint status and negotiate a timeframe before the admin ends the job.
- Build real overrun-preemption handling (currently unimplemented) for overrun-pending / overrun-termination cases.
- Automating status updates to ledger database if any overrun process ends.
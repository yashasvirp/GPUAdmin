# Compute Ledger — Deployment

Takes the Compute Ledger service (Component B) from a local container to a
running, publicly reachable, reboot-surviving deployment on a single Ubuntu
22.04 server, fronted by nginx.

**The flow, at a glance:**

1. Bootstrap the VM once (`setup.sh`)
2. Verify the bootstrap actually worked
3. Point `redeploy.sh` at your VM
4. Set your real GPU count / budget (optional)
5. Deploy (`redeploy.sh`)
6. Verify it's reachable, and correctly locked down
7. Exercise the full lifecycle against the live service

Then, once it's running: prove the alerting rules and failure-handling
actually work, and know how to redeploy and check logs going forward.

Two scripts do all the work: `setup.sh` (run once, *on* the server) and
`redeploy.sh` (run from your laptop, every time you ship new code).

## Prerequisites

*Tested against a standard cloud-VM image and against `multipass` — both
default to an `ubuntu` initial user, which this guide assumes throughout.
Vagrant (different default user, SSH reached via `vagrant ssh-config` rather
than a plain IP) and Docker-in-Docker (no systemd/SSH daemon by default,
which `setup.sh` relies on) would need real adaptation, not just a different
`VM_IP` — treat this guide as multipass/cloud-VM-specific.*

- An Ubuntu 22.04 machine reachable over SSH, with an initial user named
  **`ubuntu`** that has passwordless `sudo` — the default on most cloud
  providers (AWS, GCP, DigitalOcean, etc.) and on `multipass` VMs.
  `setup.sh` specifically copies SSH access from this `ubuntu` user to the
  `deploy` user it creates, so the initial username has to match — this
  isn't configurable.

  If you don't have a cloud VM, use `multipass` locally:

  ```bash
  multipass launch 22.04 --name compute-ledger --cpus 2 --memory 2G --disk 10G
  multipass list   # note the IPv4 address, then: VM_IP=<that address>
  ```

  Set `VM_IP` once in the shell — every command below reuses it, so you only
  type the real address once:

  ```bash
  VM_IP=<your-vm-actual-ip>   # e.g. VM_IP=10.59.27.236
  ```

  (Don't leave `<vm-ip>` as a literal placeholder in a real command — `<`/`>`
  are shell redirection syntax and will fail in a confusing way.)

- An SSH keypair on your local machine, with the **public** key already
  authorized for that `ubuntu` user. If you don't have one yet:

  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/compute_ledger_deploy -C "compute-ledger-deploy"
  ```

  - On a real cloud VM, this is usually handled at VM creation time (you
    paste your public key into the provider's UI/CLI, injected via
    cloud-init before the VM even boots).
  - On a local `multipass` VM, inject it manually after launch:

    ```bash
    PUBKEY=$(cat ~/.ssh/compute_ledger_deploy.pub)
    multipass exec compute-ledger -- bash -c \
      "mkdir -p ~/.ssh && echo '$PUBKEY' >> ~/.ssh/authorized_keys && \
       chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
    ```

  Verify it actually worked before moving on:

  ```bash
  ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP whoami
  ```

- This repository, cloned locally.

## 1. Bootstrap the VM

**What:** copies `setup.sh` to the VM and runs it once, as `ubuntu`.  
**Why:** It creates the day-to-day `deploy` user, installs Docker, locks down the firewall, hardens SSH, and
installs nginx. It's idempotent, so re-running it later (interrupted run, or
just to re-verify) is always safe.

```bash
scp -i ~/.ssh/compute_ledger_deploy deploy/setup.sh ubuntu@$VM_IP:~/setup.sh
ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP 'sudo bash ~/setup.sh'
```

Specifically, it:

- Creates a `deploy` user with passwordless `sudo` and Docker group membership
- Installs Docker and enables it to start on boot
- Installs and configures `ufw`, allowing only SSH (22) and HTTP (80)
- Disables password login and root login (hardens SSH)
- Installs nginx and removes its default site (the real site config gets
  placed by `redeploy.sh`, once app code is actually on the box)

**Expect:** a series of `==>` progress lines, script exits `0`.

**From here on, log in as `deploy`, not `ubuntu`**, for day-to-day work —
password and root SSH login are now disabled. `ubuntu` still works for
key-based login (nothing above revokes it), which the checks in the next
step rely on.

## 2. Verify the bootstrap

**What:** confirm `setup.sh` worked as intended.  
**Why:** check hardening and idempotency directly.

`deploy` can log in via key — everything from Step 3 onward assumes
this works, so confirm it now rather than discovering otherwise later:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP whoami
```

**Expect:** `deploy`.

Hardening took effect:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'sudo ufw status'
```

**Expect:** `22/tcp` and `80/tcp` ALLOW, everything else implicitly denied.

Password auth is genuinely rejected, not just configured:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  -o BatchMode=yes deploy@$VM_IP
```

**Expect:** `Permission denied (publickey)` — never a password prompt.

Running `setup.sh` a second time should not change anything. This one stays as
`ubuntu`, not `deploy` — the script was `scp`'d to `/home/ubuntu/setup.sh`
in Step 1 and never copied anywhere else, so `~/setup.sh` only resolves
correctly from that account:

```bash
ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP 'sudo bash ~/setup.sh'
```

**Expect:** every step reports "already exists / already installed,
skipping" — no errors, nothing duplicated (e.g. `cat /etc/sudoers.d/deploy`
still shows exactly one line, not one appended per run).

## 3. Point `redeploy.sh` at your VM

**What:** edit the top of `deploy/redeploy.sh` and set `VM_HOST` to VM's actual IP (the same value as `$VM_IP` above).  

```bash
VM_HOST="<your-vm-actual-ip>"
SSH_KEY="$HOME/.ssh/compute_ledger_deploy"
```

If it is redeployed to a different VM later (e.g. after recreating it), this is the one line that MUST be updated. Else, 
`redeploy.sh` will silently try to reach a VM that may no longer exist.

## 4. Set the cluster's actual GPU count and budget (optional)

**What:** override the default 20 GPUs / 43,200 GPU-hour budget with required real numbers.  
**Why:** skip this and the defaults apply — nothing breaks either way, so
only relevant if the cluster numbers have to be experimented with.

```bash
cp deploy/.env.example deploy/.env
# then edit deploy/.env with actual TOTAL_GPUS / TOTAL_BUDGET
```

`deploy/.env` is gitignored — deployment-specific config, not code. It still
reaches the VM despite that: `redeploy.sh`'s `rsync` copies whatever's on local disk in `deploy/`, regardless of `.gitignore`.

**Set this before your the deploy, not partway through** — changing it on
a deployment that already has usage history doesn't reset anything; existing
GPU-hours just get reinterpreted against the new total, producing confusing
percentages.

**Testing this without a VM at all:**

- **Via Docker Compose, locally:** same `deploy/.env` file, then run
  `docker compose -f deploy/docker-compose.prod.yml up -d --build` from the
  repo root — the exact mechanism `redeploy.sh` uses on the VM, minus the
  SSH/rsync/nginx steps.
- **Via the raw CLI, no Docker:** `deploy/.env` isn't involved here at all —
  `DB.py` reads straight from the shell environment:

  ```bash
  export TOTAL_GPUS=40 TOTAL_BUDGET=90000
  python ledger.py status
  ```

## 5. Deploy

redeploy.sh script is the one that carries out deployment - first time and many times later as required.

```bash
./deploy/redeploy.sh
```

One command does five things:

1. `rsync`s the repo to the VM (excludes `.git`, the local venv,
   `__pycache__`, and any local `.db` files — a stray local test database
   can never overwrite the VM's real, persisted one)
2. Tags the currently-running image as a rollback target (skipped on the
   very first deploy, since nothing is running yet)
3. Places the nginx config and reloads nginx
4. Rebuilds the app image from the freshly-synced code and restarts the
   container via `docker compose`
5. Polls `/health` for up to ~30 seconds — if it never becomes healthy, it
   **automatically rolls back** to the previous image and exits non-zero, so
   a failed deploy never leaves the service down or half-broken

**Expect:** `Deployment successful`.

## 6. Verify

```bash
curl http://$VM_IP/health
curl http://$VM_IP/metrics
```

**Expect:**

- `/health` → `{"status":"ok"}`
- `/metrics` from your laptop → `403 Forbidden` — correct, not a bug; it's
  restricted to localhost. To actually see it:

  ```bash
  ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost/metrics'
  ```

## 7. Exercise the full lifecycle

**What:** drive the CLI against the live container, then confirm `/metrics`
agrees with it.  
**Why:** proves this is a real, working system. The CLI (`ledger.py`) and the HTTP API share
the exact same SQLite file, so this checks both sides reflect the same circumstances.

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user alice --gpus 2 --hours 1'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_001'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py status'
```

**Expect:** `gpu_slots_active` in `/metrics` now reads `2`:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost/metrics'
```

End the session and confirm the numbers move back down:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py end req_001'
```

**Edge cases worth showing too** — the assignment specifically calls out
over-request and overrun handling; these exercise both, plus a few other
non-obvious behaviors. Note whatever `request_id` each `request` call
actually prints — later commands below reuse it.

Invalid input, rejected before it ever touches the database:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user test --gpus 0 --hours 1'
```

**Expect:** `invalid request`.

A single request exceeding the entire grant budget — a different rejection
path than "not enough GPUs free":

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user test --gpus 20 --hours 3000'
```

**Expect:** rejected for exceeding the total grant budget (20 × 3000 =
60,000 > 43,200).

Over-request against currently available GPUs — the exact scenario named in
the assignment:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user bob --gpus 25 --hours 1'
```

**Expect:** `rejected - requested 25 GPUs, only <N> available`.

Approving something that was never requested:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_999'
```

**Expect:** `req_999 not found!`.

Double-approve:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user carol --gpus 1 --hours 1'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_XXX'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_XXX'
```

**Expect:** the second call says `Cannot approve a non-pending request!`.
**Clean up immediately** — `end req_XXX` — to free the GPU slot.

`end` on a request that was never approved — cancels rather than errors,
worth narrating since it isn't obvious behavior:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user dave --gpus 1 --hours 1'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py end req_YYY'
```

**Expect:** `Request req_YYY was pending. Now stands cancelled`. Never held
a GPU slot at all (pending doesn't count toward usage) — no cleanup needed.

Double-`end`:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py end req_YYY'
```

**Expect:** `Request req_YYY already cancelled`.

Overrun flagging — the other edge case the assignment names explicitly:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user eve --gpus 1 --hours 0.001'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_ZZZ'
sleep 5
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py status'
```

**Expect:** the active-sessions table shows `req_ZZZ` with status
`overrun`. **Clean it up immediately** — this one matters more than the
others, since it also drives `session_overrun_count` in `/metrics`, which
the live alerting section below assumes starts at zero:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py end req_ZZZ'
```

**Checkpoint before moving on:**

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py status'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'curl -s http://localhost/metrics | grep -E "session_overrun_count|gpu_slots_active"'
```

**Expect:** `session_overrun_count 0`, `gpu_slots_active 0`. If either
isn't zero, something above wasn't cleaned up — fix it before continuing;
the live alerting sections assume a clean starting state.

## Testing Resilience and idempotency

### Alerting rules

`deploy/alerts.yml` isn't wired into a running Prometheus anywhere in this
stack — there's no Prometheus/Alertmanager service in
`docker-compose.prod.yml`, so nothing evaluates it against live metrics by
default. It's tested instead with `promtool`'s unit-testing feature:
`deploy/alerts_test.yml` feeds synthetic time series through the rules and
asserts which alerts should (and shouldn't) fire — including a negative case
proving `QueueStarved` needs *both* halves of its compound condition, not
just one.

```bash
docker run --rm --entrypoint promtool -v "$(pwd)/deploy:/work" -w /work \
  prom/prometheus:latest test rules alerts_test.yml
```

**Expect:** `SUCCESS`. Syntax-only check (structure, not logic):

```bash
docker run --rm --entrypoint promtool -v "$(pwd)/deploy:/work" -w /work \
  prom/prometheus:latest check rules alerts.yml
```

### Alerting rules, live (optional)

The unit tests above prove the rule *logic* is correct against synthetic
data — that's what's actually required. This section goes a step further,
proving the same rules against the *real, deployed* service: not required
by the assignment, but a stronger demonstration than the unit tests alone.

Run a temporary Prometheus instance directly on the VM, using the scrape
config already checked into this repo (`deploy/prometheus.yml`):

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'mkdir -p /tmp/prom-test'
scp -i ~/.ssh/compute_ledger_deploy deploy/prometheus.yml deploy@$VM_IP:/tmp/prom-test/prometheus.yml
scp -i ~/.ssh/compute_ledger_deploy deploy/alerts.yml deploy@$VM_IP:/tmp/prom-test/alerts.yml
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP '
docker run -d --name prom-test --network host \
  -v /tmp/prom-test/prometheus.yml:/etc/prometheus/prometheus.yml \
  -v /tmp/prom-test/alerts.yml:/etc/prometheus/alerts.yml \
  prom/prometheus:latest --config.file=/etc/prometheus/prometheus.yml \
  --web.listen-address=127.0.0.1:9090
'
```

`--network host` matters here: it lets this temporary container reach the
app's `127.0.0.1:8080` binding directly, and puts Prometheus's own UI on the
VM's own `localhost:9090` — without opening anything new externally (`ufw`
still only allows 22/80 in from outside; port 9090 is unreachable off-box).

View it in a browser from the laptop via an SSH tunnel:

```bash
ssh -i ~/.ssh/compute_ledger_deploy -f -N -L 9090:localhost:9090 deploy@$VM_IP
```

Then open `http://localhost:9090` locally. Confirm the target is healthy and
the rules loaded:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost:9090/api/v1/targets'
```

**Expect:** `"health":"up"` for the `compute-ledger` job, and all three
rules listed under `compute_ledger_alerts`, initially `"state":"inactive"`.

To watch one actually fire, force a real overrun and wait for the next
scrape (use whatever `request_id` the `request` command actually prints):

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker exec compute-ledger python ledger.py request --user demo --gpus 1 --hours 0.001'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker exec compute-ledger python ledger.py approve req_XXX'
sleep 15
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost:9090/api/v1/alerts'
```

**Expect:** `SessionOverrun` now shows `"state":"firing"`.

**`QueueStarved`, next — reaching `pending`, not `firing`.** Its condition
is `allocation_queue_depth > 5 and gpu_slots_active < 4`, sustained for a
full 10 minutes before it actually fires. Sitting through 10 real minutes
isn't practical here, so this demonstrates the compound condition being
correctly *detected* — entering `pending` state — without waiting out the
full duration to `firing`. Still uses the same `prom-test` instance, so do
this before tearing anything down:

```bash
for u in q1 q2 q3 q4 q5 q6; do
  ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
    "docker exec compute-ledger python ledger.py request --user $u --gpus 1 --hours 1"
done
sleep 6
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost:9090/api/v1/alerts'
```

Six pending requests (`allocation_queue_depth` = 6 > 5), none approved, so
`gpu_slots_active` stays at whatever it already was — 0, if the checkpoint
above was clean.

**Expect:** `QueueStarved` now shows `"state":"pending"`.

**`BudgetWarning` — not demonstrated live, deliberately.** Triggering it
for real means consuming 80% of the 43,200 GPU-hour budget — at 20 GPUs
held continuously, that's 72 days. The only way to fake it faster is an
artificially tiny budget, which either pollutes this real deployment's
usage history (see the caveat in Step 4) or means standing up a second,
throwaway deployment just for one alert — disproportionate effort for an
already-optional demonstration. The `promtool` unit test earlier already
proves this rule's logic (5 minutes sustained below 20%) correctly; that's
the intended level of proof for this one.

**Tear down when done** — several things need cleaning up here, not just
the Prometheus container. Everything below is real ledger state that
outlives `prom-test` and will keep holding GPU slots / queue entries
indefinitely if skipped — substitute in the six IDs the `request` loop
above actually printed:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker exec compute-ledger python ledger.py end req_XXX'   # the overrun session
for id in req_A req_B req_C req_D req_E req_F; do   # the six queue entries
  ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
    "docker exec compute-ledger python ledger.py end $id"
done
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker rm -f prom-test && rm -rf /tmp/prom-test'
# also kill the local SSH tunnel process (or close the terminal it's running in)
```

**Expect:** afterward, `session_overrun_count`, `gpu_slots_active`, and
`allocation_queue_depth` in `/metrics` are all back to zero — check with
`docker exec compute-ledger python ledger.py status` if unsure.

### Resilience

**Reboot survival** — must come back with zero manual steps:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'sudo reboot'
sleep 30
curl http://$VM_IP/health
```

**Redeploy idempotency** — running it again with no code changes should be
clean:

```bash
./deploy/redeploy.sh
```

**Expect:** the same `Deployment successful` outcome, and exactly one
`compute-ledger` container running afterward (`docker ps` on the VM) — not a
second one alongside it.

**Rollback on failure** — break the app on purpose, confirm `redeploy.sh`
catches it instead of leaving the service down. For example, temporarily
introduce a syntax error in `src/api.py`, then:

```bash
./deploy/redeploy.sh
```

**Expect:** the health check fails for ~30 seconds, `redeploy.sh` prints
"Rolling back...", exits non-zero, and `curl http://$VM_IP/health`
afterward still returns `{"status":"ok"}` — the previous, working image, not
the broken one. Revert the local change once confirmed, and redeploy clean.

## Ongoing operations

**Redeploy whenever the code changes:**

```bash
./deploy/redeploy.sh
```

`setup.sh` never needs to run again on the same VM — it only bootstraps a
blank machine once. Everything after that is `redeploy.sh`.

**Logs:**

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker logs compute-ledger'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'sudo journalctl -u nginx -n 100'
```

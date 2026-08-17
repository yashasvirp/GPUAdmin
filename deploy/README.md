# Compute Ledger — Deployment

Takes the Compute Ledger service (Component B) from a local container to a
running, publicly reachable, reboot-surviving deployment on a single Ubuntu
22.04 server, fronted by nginx.

Two scripts do all the work: `setup.sh` (run once, on the server, to bootstrap
a bare VM) and `redeploy.sh` (run from your laptop, every time you want to
ship the current code).

Throughout this guide, set `VM_IP` once in your shell and every command below
just reuses it — this avoids the common mistake of typing a placeholder like
`<vm-ip>` literally into a command (`<` and `>` are shell redirection syntax,
so that fails in a confusing way):

```bash
VM_IP=<your-vm-actual-ip>   # e.g. VM_IP=10.59.27.236 — set this once, for real
```

## Prerequisites

- An Ubuntu 22.04 machine reachable over SSH, with an initial user named
  **`ubuntu`** that has passwordless `sudo` — this is the default on most
  cloud providers (AWS, GCP, DigitalOcean, etc. all provision an `ubuntu`
  user this way) and on `multipass` VMs. `setup.sh` specifically copies SSH
  access from this `ubuntu` user to the `deploy` user it creates, so the
  initial username has to match — this isn't configurable.

  If you don't have a cloud VM, use `multipass` locally:

  ```bash
  multipass launch 22.04 --name compute-ledger --cpus 2 --memory 2G --disk 10G
  multipass list   # note the IPv4 address, then: VM_IP=<that address>
  ```

- An SSH keypair on your local machine, with the **public** key already
  authorized for that `ubuntu` user. If you don't have one yet:

  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/compute_ledger_deploy -C "compute-ledger-deploy"
  ```

  - On a real cloud VM, this is usually handled for you at VM creation time
    (you paste your public key into the provider's UI/CLI, and it gets
    injected via cloud-init before the VM even boots).
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

## 1. One-time VM bootstrap

Copy `setup.sh` to the VM and run it as `ubuntu`:

```bash
scp -i ~/.ssh/compute_ledger_deploy deploy/setup.sh ubuntu@$VM_IP:~/setup.sh
ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP 'sudo bash ~/setup.sh'
```

This step is **idempotent** — safe to run more than once (useful if it's
interrupted, or if you want to re-verify a box's configuration later).

What it does:

- Creates a `deploy` user with passwordless `sudo` and Docker group membership
- Installs Docker and enables it to start on boot
- Installs and configures `ufw`, allowing only SSH (22) and HTTP (80)
- Hardens SSH: disables password login and root login
- Installs nginx and removes its default site (the actual Compute Ledger
  site config gets placed by `redeploy.sh`, once the app code is on the box)

**After this step, log in as `deploy`, not `ubuntu`** for day-to-day work —
password and root SSH login are now disabled. `ubuntu` still works for
key-based login (nothing above revokes it), which is what the verification
and idempotency checks below rely on.

## 2. Verify the bootstrap

Don't just trust the script's own `==>` log lines — confirm it actually did
what it claims before building on top of it.

**Hardening actually took effect:**

```bash
ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP 'sudo ufw status'
# expect: 22/tcp and 80/tcp ALLOW, everything else implicitly denied
```

Confirm password auth is genuinely rejected, not just configured — this
should fail with "Permission denied (publickey)", never prompt for a
password:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  -o BatchMode=yes deploy@$VM_IP
```

**Idempotency** — the entire point of `setup.sh` being idempotent is that
running it twice is safe. Prove it, don't just assume it:

```bash
ssh -i ~/.ssh/compute_ledger_deploy ubuntu@$VM_IP 'sudo bash ~/setup.sh'
```

Expect every step to report "already exists / already installed, skipping,"
no errors, and nothing duplicated — e.g. `cat /etc/sudoers.d/deploy` should
still show exactly one line, not one appended per run.

## 3. Point `redeploy.sh` at your VM

Edit the top of `deploy/redeploy.sh` and set `VM_HOST` to your VM's actual IP
(the same value you put in `$VM_IP` above) — this file can't read your shell
variable, so this has to be a real, literal edit:

```bash
VM_HOST="<your-vm-actual-ip>"
SSH_KEY="$HOME/.ssh/compute_ledger_deploy"
```

**If you're redeploying to a different VM later** (e.g. after recreating it),
this is the one line you must remember to update — forgetting to will make
`redeploy.sh` silently try to reach a VM that may no longer exist.

## 4. Set your cluster's actual GPU count and budget (optional)

By default the service assumes 20 GPUs and a 43,200 GPU-hour budget. To point
it at your real cluster instead, copy the example env file and edit it:

```bash
cp deploy/.env.example deploy/.env
# then edit deploy/.env with your actual TOTAL_GPUS / TOTAL_BUDGET
```

`deploy/.env` is gitignored on purpose — it's deployment-specific
configuration, not code, so it shouldn't be committed or shared across
different deployments. It still reaches the VM correctly despite that:
`redeploy.sh`'s `rsync` operates on your local filesystem directly (it has
no idea what's in `.gitignore`, and doesn't need to), so as long as the file
exists locally in `deploy/`, it gets synced to the VM on every deploy right
alongside the code. If you skip this step entirely, `docker-compose.prod.yml`
falls back to the same 20/43,200 defaults, so nothing breaks either way.

**Set this before your first deploy, not partway through.** Changing these
values on a deployment that already has usage history doesn't reset
anything — GPU-hours already charged just get reinterpreted against the new
total, which can produce confusing percentages. Fine to change on a VM
you're just starting to exercise; avoid changing it on one with real,
ongoing usage.

**Testing without the VM at all:**

- **Via Docker Compose, locally:** put `deploy/.env` in place as above, then
  run `docker compose -f deploy/docker-compose.prod.yml up -d --build` from
  the repo root — same mechanism `redeploy.sh` uses on the VM, just without
  the SSH/rsync/nginx steps.
- **Via the raw CLI, no Docker:** `DB.py` reads `TOTAL_GPUS`/`TOTAL_BUDGET`
  straight from the environment — `deploy/.env` isn't involved at all here —
  so just export them in your shell first:

  ```bash
  export TOTAL_GPUS=40 TOTAL_BUDGET=90000
  python ledger.py status
  ```

## 5. Deploy

From the repo root, on your local machine:

```bash
./deploy/redeploy.sh
```

This single command:

1. `rsync`s the current repo to the VM (excluding `.git`, the local Python
   venv, `__pycache__`, and any local `.db` files — so a stray local test
   database can never overwrite the VM's real, persisted one)
2. Tags the currently-running image as a rollback target (skipped
   automatically on the very first deploy, since nothing is running yet)
3. Places the nginx config and reloads nginx
4. Rebuilds the app image from the freshly-synced code and restarts the
   container via `docker compose`
5. Polls `/health` for up to ~30 seconds. If the new deployment never
   becomes healthy, it **automatically rolls back** to the previous image
   and exits with a non-zero status — a failed deploy never leaves the
   service down or half-broken.

## 6. Verify

```bash
curl http://$VM_IP/health
curl http://$VM_IP/metrics
```

Expected:

- `/health` → `{"status":"ok"}`
- `/metrics` from your laptop → `403 Forbidden` — this is correct, not a
  bug; it's restricted to localhost. To actually see it:

  ```bash
  ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost/metrics'
  ```

## 7. Exercise the full lifecycle

Confirm the deployed service is a real, working system, not just a container
that happens to answer `/health`. The CLI (`ledger.py`) is fully available
inside the running container, sharing the exact same SQLite file as the HTTP
API — so driving it through the CLI and reading the result back through
`/metrics` proves both halves agree on the same state:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py request --user alice --gpus 2 --hours 1'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py approve req_001'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py status'
```

Confirm `/metrics` reflects it — `gpu_slots_active` should now read `2`:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'curl -s http://localhost/metrics'
```

End the session and confirm the numbers move back down:

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP \
  'docker exec compute-ledger python ledger.py end req_001'
```

## Testing the alerting rules

`deploy/alerts.yml` isn't wired into a running Prometheus anywhere in this
stack — there's no Prometheus/Alertmanager service in
`docker-compose.prod.yml`, so nothing evaluates it against live metrics by
default. It's tested instead with `promtool`'s built-in unit-testing feature:
`deploy/alerts_test.yml` defines synthetic time series for each metric and
asserts which alerts should (and shouldn't) be firing at specific points in
simulated time — including a negative case for `QueueStarved` that checks it
does *not* fire when only one half of its compound condition is true.

No local Prometheus install needed — run it via the official image:

```bash
docker run --rm --entrypoint promtool -v "$(pwd)/deploy:/work" -w /work \
  prom/prometheus:latest test rules alerts_test.yml
```

Expected output: `SUCCESS`. You can also sanity-check the rules file's
syntax alone (no logic testing, just structure) with:

```bash
docker run --rm --entrypoint promtool -v "$(pwd)/deploy:/work" -w /work \
  prom/prometheus:latest check rules alerts.yml
```

## Testing resilience

Three things the deployment claims but doesn't prove on its own — worth
checking deliberately rather than trusting the scripts:

**Reboot survival** — the app must come back with zero manual steps:

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

Expect the same "Deployment successful" outcome, no errors, and exactly one
`compute-ledger` container running afterward (`docker ps` on the VM) — not a
second one alongside it.

**Rollback on failure** — deliberately break the app, then confirm
`redeploy.sh` catches it and rolls back instead of leaving the service down.
For example, temporarily introduce a syntax error in `src/api.py`, then:

```bash
./deploy/redeploy.sh
```

Expect: the health check fails for ~30 seconds, `redeploy.sh` prints
"Rolling back...", exits non-zero, and `curl http://$VM_IP/health` still
returns `{"status":"ok"}` afterward — the previous, working image, not the
broken one. Revert the local change once confirmed, and redeploy clean again.

## Redeploying later

Whenever the code changes, run:

```bash
./deploy/redeploy.sh
```

`setup.sh` never needs to run again on the same VM — it only bootstraps a
blank machine once. Everything after that is `redeploy.sh`.

## Logs

```bash
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'docker logs compute-ledger'
ssh -i ~/.ssh/compute_ledger_deploy deploy@$VM_IP 'sudo journalctl -u nginx -n 100'
```

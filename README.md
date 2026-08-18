# Compute Ledger

A take-home for the GPU Administrator / Cloud Ops role: allocate GPU time
across researchers on a fixed grant budget, track it in GPU-hours, and run
the whole thing as a real, deployed, monitored service.

Three equally-weighted components:

| | What | Where |
|---|---|---|
| **A** | Allocation policy — request/approval, fairness, preemption, budget tracking, burst handling | [`policy/`](policy/Compute%20Ledger%20-%20GPU%20Allocation%20Policy.pdf) |
| **B** | The CLI itself — request/approve/status/end lifecycle, SQLite-backed, containerized | [`src/`](src/) |
| **C** | Deployment — hardened VM, reverse proxy, metrics, alerting, and a runbook | [`deploy/`](deploy/) |

## Clone

```bash
git clone https://github.com/yashasvirp/GPUAdmin.git
cd GPUAdmin
```

Every command below assumes you're at this repo root, unless a section says
otherwise.

## Component A — Allocation Policy

Written policy document, no code to run:
[`policy/Compute Ledger - GPU Allocation Policy.pdf`](policy/Compute%20Ledger%20-%20GPU%20Allocation%20Policy.pdf).

## Component B — Compute Ledger CLI

A Python CLI (`ledger.py`) simulating the allocation lifecycle, backed by
SQLite, fully containerized. No VM, no config, nothing beyond Docker:

```bash
cd src
bash demo.sh
```

This builds the image and runs the full lifecycle — request → approve →
status → end → budget check — inside an ephemeral container, including a
rejected over-request. See [`src/demo.sh`](src/demo.sh) for exactly what it
does, or drive `ledger.py` directly yourself:

```bash
docker build -t compute-ledger .
docker run --rm compute-ledger python ledger.py request --user alice --gpus 4 --hours 12
```

## Component C — Deployment + Observability

Takes the same service from Component B and deploys it on a real (or
`multipass`) VM: hardened SSH, `ufw`, an nginx reverse proxy, `/health` and
`/metrics` endpoints, Prometheus alerting rules, and a runbook for the "is my
job dead?" support scenario.

The full walkthrough — bootstrap, deploy, verify, exercise the live service,
test resilience — is in [`deploy/README.md`](deploy/README.md). Start there;
it covers everything from a blank VM onward.

## Repo layout

```
policy/     Component A — the policy document
src/        Component B — CLI, Dockerfile, demo.sh
deploy/     Component C — deploy scripts, nginx config, alerts, runbook
```

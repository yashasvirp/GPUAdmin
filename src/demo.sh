#!/bin/bash
set -e

IMAGE_NAME="compute-ledger"

echo "=== Building the Compute Ledger image ==="
docker build -t "$IMAGE_NAME" .

echo ""
echo "=== Running the full request -> approve -> status -> end -> budget check lifecycle inside a container ==="
echo ""

docker run --rm "$IMAGE_NAME" sh -c '
  set -e

  echo "--- Step 1: alice requests 2 GPUs for 1 hour ---"
  python ledger.py request --user alice --gpus 2 --hours 1
  echo ""

  echo "--- Step 2: approving alices request ---"
  python ledger.py approve req_001
  echo ""

  echo "--- Step 3: status while the request is active ---"
  python ledger.py status
  echo ""

  echo "--- Step 4: bob over-requests 25 GPUs (only 18 are free) - should be rejected ---"
  python ledger.py request --user bob --gpus 25 --hours 1
  echo ""

  echo "--- Step 5: simulating some GPU work, then ending alices request ---"
  sleep 3
  python ledger.py end req_001
  echo ""

  echo "--- Step 6: final status and budget check ---"
  python ledger.py status
'

echo ""
echo "=== Demo complete ==="

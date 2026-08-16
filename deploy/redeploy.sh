#!/usr/bin/env bash
set -euo pipefail

VM_HOST="10.59.27.127"
SSH_KEY="$HOME/.ssh/compute_ledger_deploy"
REMOTE_DIR="/home/deploy/compute-ledger"
HEALTH_URL="http://${VM_HOST}/health"

ssh_vm() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "deploy@${VM_HOST}" "$@"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "==> Syncing code to VM"
ssh_vm "mkdir -p $REMOTE_DIR"
rsync -az --delete \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.git' \
    --exclude '.gpu_env' \
    --exclude '__pycache__' \
    --exclude '*.db' \
    --exclude '*.db-journal' \
    --exclude 'deploy/data' \
    "$REPO_ROOT/" "deploy@${VM_HOST}:${REMOTE_DIR}/"

IMAGE_NAME="compute-ledger-app"

echo "==> Tagging current image as rollback target (if one exists)"
if ssh_vm "docker image inspect ${IMAGE_NAME}:latest" &>/dev/null; then
    ssh_vm "docker tag ${IMAGE_NAME}:latest ${IMAGE_NAME}:rollback"
    echo "==> Tagged current image as rollback target"
else
    echo "==> No existing image found (first deploy), skipping rollback tag"
fi

echo "==> Placing nginx config"
ssh_vm "sudo cp ${REMOTE_DIR}/deploy/nginx.conf /etc/nginx/sites-available/compute-ledger"
ssh_vm "sudo ln -sf /etc/nginx/sites-available/compute-ledger /etc/nginx/sites-enabled/compute-ledger"
ssh_vm "sudo nginx -t"
ssh_vm "sudo systemctl reload nginx"

echo "==> Rebuilding and restarting the app"
ssh_vm "docker compose -f ${REMOTE_DIR}/deploy/docker-compose.prod.yml up -d --build"

echo "==> Waiting for the new deployment to become healthy"
HEALTHY=false
for i in $(seq 1 10); do
    if curl -sf "$HEALTH_URL" > /dev/null; then
        echo "==> Healthy after ${i} attempt(s)"
        HEALTHY=true
        break
    fi
    echo "==> Attempt ${i}/10 not healthy yet, retrying in 3s..."
    sleep 3
done

if [ "$HEALTHY" = false ]; then
    echo "==> Deployment failed health check. Rolling back..."
    if ssh_vm "docker image inspect ${IMAGE_NAME}:rollback" &>/dev/null; then
        ssh_vm "docker compose -f ${REMOTE_DIR}/deploy/docker-compose.prod.yml down"
        ssh_vm "docker tag ${IMAGE_NAME}:rollback ${IMAGE_NAME}:latest"
        ssh_vm "docker compose -f ${REMOTE_DIR}/deploy/docker-compose.prod.yml up -d"
        echo "==> Rolled back to previous version"
    else
        echo "==> No rollback image available (this was the first deploy). Manual intervention required."
    fi
    exit 1
fi

echo "==> Deployment successful"

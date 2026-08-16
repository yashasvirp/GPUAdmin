#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER="deploy"

echo "==> Starting Compute Ledger server bootstrap"

# Creating a non-root user - deploy, and giving sudo privileges.
if ! id "$DEPLOY_USER" &>/dev/null; then
    useradd --create-home --shell /bin/bash --groups sudo "$DEPLOY_USER"
    echo "==> Created user $DEPLOY_USER"
else
    echo "==> User $DEPLOY_USER already exists, skipping"
fi

echo "$DEPLOY_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/"$DEPLOY_USER"
chmod 440 /etc/sudoers.d/"$DEPLOY_USER"

mkdir -p /home/"$DEPLOY_USER"/.ssh
chmod 700 /home/"$DEPLOY_USER"/.ssh
cp /home/ubuntu/.ssh/authorized_keys /home/"$DEPLOY_USER"/.ssh/authorized_keys
chmod 600 /home/"$DEPLOY_USER"/.ssh/authorized_keys
chown -R "$DEPLOY_USER":"$DEPLOY_USER" /home/"$DEPLOY_USER"/.ssh


# Installing docker
if ! command -v docker &>/dev/null; then
    echo "==> Installing Docker"
    apt-get update
    apt-get install -y ca-certificates curl gnupg

    install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
else
    echo "==> Docker already installed, skipping"
fi

systemctl enable --now docker
usermod -aG docker "$DEPLOY_USER"

# ufw firewall
echo "==> Configuring ufw firewall"
if ! command -v ufw &>/dev/null; then
    apt-get install -y ufw
fi

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw --force enable

# Hardening ssh
echo "==> Hardening SSH"
cat > /etc/ssh/sshd_config.d/hardening.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF

systemctl reload ssh

# Installing nginx (config placement happens in redeploy.sh, once the repo is synced)
echo "==> Installing nginx"
if ! command -v nginx &>/dev/null; then
    apt-get install -y nginx
fi

rm -f /etc/nginx/sites-enabled/default
systemctl enable --now nginx

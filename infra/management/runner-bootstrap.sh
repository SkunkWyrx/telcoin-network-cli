#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git jq python3 unzip
curl -fsSL https://aka.ms/InstallAzureCLIDeb -o /tmp/install-azure-cli.sh
bash /tmp/install-azure-cli.sh
install -d -m 700 -o azureuser -g azureuser /var/lib/oprail-observers
install -d -m 750 -o root -g azureuser /etc/oprail-observers /etc/oprail-observers/adiri /etc/oprail-observers/ssh
if [ ! -f /etc/oprail-observers/ssh/id_ed25519 ]; then
  ssh-keygen -q -t ed25519 -N '' -C adiri-observer-runner -f /etc/oprail-observers/ssh/id_ed25519
  chown azureuser:azureuser /etc/oprail-observers/ssh/id_ed25519*
fi
install -d -m 700 -o azureuser -g azureuser /home/azureuser/.ssh
install -d -m 755 -o azureuser -g azureuser /opt/actions-runner
touch /var/lib/oprail-observers/bootstrap-ready

#!/usr/bin/env bash
set -Eeuo pipefail

# Installs reliable Compose autostart. The idle shutdown units remain available
# for diagnostics, but the production timer is disabled by default.
# Run from the cloned repository: sudo bash deploy/install-vm-services.sh

if (( EUID != 0 )); then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DOCKER_BIN="$(command -v docker || true)"

if [[ -z "$DOCKER_BIN" ]] || ! "$DOCKER_BIN" compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available as 'docker compose'." >&2
  exit 1
fi

replace_project_dir() {
  local source="$1"
  local destination="$2"
  # Project paths used on Linux cannot contain a newline. Escape sed separators.
  local escaped_project_dir="${PROJECT_DIR//|/\\|}"
  local escaped_docker_bin="${DOCKER_BIN//|/\\|}"
  sed \
    -e "s|__PROJECT_DIR__|${escaped_project_dir}|g" \
    -e "s|__DOCKER_BIN__|${escaped_docker_bin}|g" \
    "$source" >"$destination"
}

install -d -m 0755 "${PROJECT_DIR}/data/nginx"
# Скрипту нужен путь проекта: он спрашивает очередь через docker compose,
# чтобы не погасить машину посреди фонового поиска.
replace_project_dir "${SCRIPT_DIR}/chemsource-idle-shutdown" \
  /usr/local/sbin/chemsource-idle-shutdown
chmod 0755 /usr/local/sbin/chemsource-idle-shutdown
install -m 0644 "${SCRIPT_DIR}/chemsource-idle-shutdown.service" \
  /etc/systemd/system/chemsource-idle-shutdown.service
install -m 0644 "${SCRIPT_DIR}/chemsource-idle-shutdown.timer" \
  /etc/systemd/system/chemsource-idle-shutdown.timer

replace_project_dir "${SCRIPT_DIR}/chemsource.service.example" \
  /etc/systemd/system/chemsource.service
replace_project_dir "${SCRIPT_DIR}/idle-shutdown.conf.example" \
  /etc/chemsource-idle-shutdown.conf

systemctl daemon-reload
systemctl enable docker.service chemsource.service
# qwen.service exists only on a VM with a local GPU model. Production can use
# an external OpenAI-compatible endpoint, so a CPU-only VM has no such unit.
if [[ -f /etc/systemd/system/qwen.service ]]; then
  systemctl enable qwen.service
  QWEN_STATE="enabled"
else
  QWEN_STATE="not installed (external LLM)"
fi
systemctl disable --now chemsource-idle-shutdown.timer || true
systemctl restart chemsource.service

echo
echo "Installed successfully."
echo "Autostart: chemsource.service (qwen.service: ${QWEN_STATE})"
echo "Idle shutdown: disabled (production VM stays running)"
echo
systemctl is-enabled chemsource-idle-shutdown.timer || true
systemctl is-active chemsource-idle-shutdown.timer || true

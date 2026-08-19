#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_COMMIT="${1:-}"
BACKUP_DIR="$PROJECT_DIR/data/backups"
TIMER_UNIT="chemsource-idle-shutdown.timer"
COMPOSE=(
  docker compose
  --project-directory "$PROJECT_DIR"
  -f "$PROJECT_DIR/docker-compose.yml"
)

disable_idle_shutdown() {
  sudo systemctl disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
}

disable_idle_shutdown
trap disable_idle_shutdown EXIT

server_changes="$(
  git -C "$PROJECT_DIR" status --porcelain --untracked-files=all |
    grep -vE '^\?\? data/backups/' || true
)"
if [[ -n "$server_changes" ]]; then
  echo "На сервере есть незакоммиченные изменения. Развёртывание остановлено." >&2
  printf "%s\n" "$server_changes"
  exit 1
fi

actual_commit="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
if [[ -n "$EXPECTED_COMMIT" && "$actual_commit" != "$EXPECTED_COMMIT" ]]; then
  echo "Сервер получил коммит $actual_commit вместо $EXPECTED_COMMIT." >&2
  exit 1
fi

"${COMPOSE[@]}" up -d db redis

db_ready=0
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T db sh -c \
    'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    db_ready=1
    break
  fi
  sleep 2
done
if [[ "$db_ready" != "1" ]]; then
  echo "PostgreSQL не перешёл в состояние готовности." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
backup_file="$BACKUP_DIR/pre_${actual_commit:0:8}_$(date -u +%Y%m%dT%H%M%SZ).dump"
"${COMPOSE[@]}" exec -T db sh -c \
  'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$backup_file"
if [[ ! -s "$backup_file" ]]; then
  echo "Резервная копия базы данных не создана." >&2
  exit 1
fi
echo "Резервная копия БД: $backup_file"

bash "$PROJECT_DIR/deploy/apply-migrations.sh" "$PROJECT_DIR"
"${COMPOSE[@]}" up -d --build
# Nginx resolves the backend container name when it starts. If only backend was
# recreated, an unchanged frontend container can retain the old container IP.
"${COMPOSE[@]}" restart frontend

sudo systemctl reset-failed chemsource.service || true
sudo systemctl start chemsource.service
# Re-assert boot autostart on every deployment: a VM started manually from
# the Yandex Cloud console must bring up Qwen and the Compose stack itself.
sudo systemctl enable docker.service qwen.service chemsource.service || true

wait_for_url() {
  local url="$1"
  local label="$2"
  for _ in $(seq 1 120); do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      echo "$label: доступен"
      return 0
    fi
    sleep 5
  done
  echo "$label не ответил за 10 минут: $url" >&2
  return 1
}

wait_for_url "http://127.0.0.1/api/health" "Backend"
wait_for_url "http://127.0.0.1/api/health/llm" "Локальная ИИ-модель"

"${COMPOSE[@]}" ps
disable_idle_shutdown
trap - EXIT
systemctl is-active chemsource.service qwen.service
if systemctl is-enabled --quiet "$TIMER_UNIT" || \
    systemctl is-active --quiet "$TIMER_UNIT"; then
  echo "Таймер автоостановки должен быть отключён." >&2
  exit 1
fi
echo "Таймер автоостановки: disabled/inactive"
echo "Развёрнут коммит: $actual_commit"

#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MIGRATIONS_DIR="$PROJECT_DIR/backend/migrations"
COMPOSE=(
  docker compose
  --project-directory "$PROJECT_DIR"
  -f "$PROJECT_DIR/docker-compose.yml"
)

db_psql() {
  "${COMPOSE[@]}" exec -T db sh -c \
    'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' \
    sh "$@"
}

db_psql -c \
  "CREATE TABLE IF NOT EXISTS schema_migrations (
     version VARCHAR(255) PRIMARY KEY,
     applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
   )"

shopt -s nullglob
migrations=("$MIGRATIONS_DIR"/*.up.sql)

for migration in "${migrations[@]}"; do
  version="$(basename "$migration")"
  if [[ ! "$version" =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "Недопустимое имя миграции: $version" >&2
    exit 1
  fi

  applied="$(db_psql -Atc \
    "SELECT 1 FROM schema_migrations WHERE version = '$version'")"
  if [[ "$applied" == "1" ]]; then
    echo "Миграция уже применена: $version"
    continue
  fi

  echo "Применяется миграция: $version"
  {
    cat "$migration"
    printf "\nINSERT INTO schema_migrations (version) VALUES ('%s');\n" "$version"
  } | db_psql --single-transaction
done

echo "Миграции базы данных актуальны."

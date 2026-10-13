#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pg_bin="$(brew --prefix postgresql@16)/bin"
pg_data="$repo_root/instance/postgres"
pg_socket="$repo_root/instance"
pg_port=5433
pg_database=edge_tracker_local

case "${1:-}" in
  start)
    mkdir -p "$pg_socket"
    if [[ ! -f "$pg_data/PG_VERSION" ]]; then
      "$pg_bin/initdb" -D "$pg_data" --data-checksums \
        --auth-local=peer --auth-host=reject
      cat >> "$pg_data/postgresql.conf" <<EOF
listen_addresses = ''
unix_socket_directories = '$pg_socket'
unix_socket_permissions = 0700
port = $pg_port
EOF
    fi
    if ! "$pg_bin/pg_ctl" -D "$pg_data" status >/dev/null 2>&1; then
      "$pg_bin/pg_ctl" -D "$pg_data" \
        -l "$repo_root/instance/postgres-local.log" start
    fi
    if [[ -z "$("$pg_bin/psql" -h "$pg_socket" -p "$pg_port" \
      -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname = '$pg_database'")" ]]; then
      "$pg_bin/createdb" -h "$pg_socket" -p "$pg_port" "$pg_database"
    fi
    echo "Local PostgreSQL ready. Use: $0 url"
    ;;
  stop)
    "$pg_bin/pg_ctl" -D "$pg_data" -m fast stop
    ;;
  status)
    "$pg_bin/pg_ctl" -D "$pg_data" status
    ;;
  backup)
    umask 077
    backup_dir="$repo_root/instance/backups"
    mkdir -p "$backup_dir"
    backup_file="$backup_dir/edge-tracker-$(date +%Y%m%d-%H%M%S)-$$.dump"
    temporary_file="$(mktemp "$backup_dir/.edge-tracker-XXXXXX")"
    trap 'rm -f "$temporary_file"' EXIT
    "$pg_bin/pg_dump" -h "$pg_socket" -p "$pg_port" \
      -d "$pg_database" --format=custom --file="$temporary_file"
    "$pg_bin/pg_restore" --list "$temporary_file" >/dev/null 2>&1
    mv "$temporary_file" "$backup_file"
    trap - EXIT
    echo "Backup written to $backup_file"
    ;;
  url)
    echo "postgresql:///$pg_database?host=$pg_socket&port=$pg_port"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|backup|url}" >&2
    exit 2
    ;;
esac

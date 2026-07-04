#!/usr/bin/env bash
# Очистить все чаты в PostgreSQL (Docker должен быть запущен)
set -euo pipefail
docker compose exec db psql -U belener -d belnipiai -c "TRUNCATE messages, conversations CASCADE;"

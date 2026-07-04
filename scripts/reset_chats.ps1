# Очистить все чаты в PostgreSQL (Docker должен быть запущен)
docker compose exec db psql -U belener -d belnipiai -c "TRUNCATE messages, conversations CASCADE;"

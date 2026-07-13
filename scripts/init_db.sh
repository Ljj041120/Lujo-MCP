#!/bin/bash
set -e

echo "Initializing database..."

if [ -z "$PG_HOST" ]; then
    PG_HOST="localhost"
fi
if [ -z "$PG_PORT" ]; then
    PG_PORT="5432"
fi
if [ -z "$PG_USER" ]; then
    PG_USER="postgres"
fi
if [ -z "$PG_DATABASE" ]; then
    PG_DATABASE="ai_debug_mcp"
fi

for file in $(ls migrations/*.sql 2>/dev/null | sort); do
    echo "Executing $file..."
    PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -f "$file"
done

echo ""
echo "Database initialization completed."
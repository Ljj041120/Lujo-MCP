#!/bin/bash
set -e

echo "Running lint checks..."

if command -v ruff &> /dev/null; then
    ruff check .
else
    echo "ruff not found, installing..."
    pip install ruff
    ruff check .
fi

echo ""
echo "Lint checks completed."
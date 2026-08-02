#!/bin/bash
set -e

echo "Running tests..."
python -m pytest tests/ -q
echo ""
echo "Test run completed."
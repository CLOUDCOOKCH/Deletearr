#!/usr/bin/env sh
set -eu
python -m ruff check deletarr tests
python -m ruff format --check deletarr tests
python -m pytest -q
npm run build

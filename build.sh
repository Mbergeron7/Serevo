#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Run database migrations
flask db upgrade

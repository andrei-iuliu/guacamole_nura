#!/usr/bin/env bash
# User-run: connect to the phone over USB networking with normal host-key checks.
set -euo pipefail
exec ssh user@172.16.42.1

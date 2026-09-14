#!/usr/bin/env bash
# arduino-cli, with every directory it would otherwise scatter through the user
# profile pinned inside firmware/tools-local. Nothing outside this tree is touched.
#
#   ./acli.sh core install esp32:esp32
#   ./acli.sh compile --fqbn esp32:esp32:esp32 esp32_srxl2_bridge
#   ./acli.sh upload   --fqbn esp32:esp32:esp32 -p COM5 esp32_srxl2_bridge
set -euo pipefail

ROOT="/d/personal/Tesi/firmware/tools-local/arduino-cli"

export ARDUINO_DIRECTORIES_DATA="$ROOT/data"
export ARDUINO_DIRECTORIES_DOWNLOADS="$ROOT/downloads"
export ARDUINO_DIRECTORIES_USER="$ROOT/user"
export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS="https://espressif.github.io/arduino-esp32/package_esp32_index.json"

mkdir -p "$ARDUINO_DIRECTORIES_DATA" "$ARDUINO_DIRECTORIES_DOWNLOADS" "$ARDUINO_DIRECTORIES_USER"

exec "$ROOT/arduino-cli.exe" "$@"

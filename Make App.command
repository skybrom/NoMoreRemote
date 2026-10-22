#!/bin/bash
# Builds "Trinnov Functions.app" next to this file: a native menu-bar app (needs the Swift compiler).
cd "$(dirname "$0")" || exit 1
bash app/build_app.sh

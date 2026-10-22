#!/bin/bash
# Stops and removes the Trinnov Functions background service.
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 server.py --uninstall-service

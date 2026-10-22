#!/bin/bash
# Used by sudo -A: asks for the Mac password in a normal macOS dialog. Cancel makes sudo fail.
/usr/bin/osascript -e 'text returned of (display dialog "Trinnov Functions needs your Mac password to set up the Trinnov shutdown helper (it switches the Nova off when this Mac turns off)." default answer "" with hidden answer with title "Trinnov Auto Off" with icon caution buttons {"Cancel", "OK"} default button "OK")'

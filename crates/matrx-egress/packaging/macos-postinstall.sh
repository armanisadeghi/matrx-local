#!/bin/bash
#
# macOS .pkg postinstall — runs as root.
#
# Everything the helper does belongs to ONE person: their keychain item, their internet
# connection, their menu bar. So nothing here runs as root beyond the file copy the installer
# already did: the launch agent is registered as the console user, through `matrx-egress install`,
# which writes ~/Library/LaunchAgents/com.aimatrx.home-connection.plist for THAT user.
#
# A failure here never fails the install: the person can open the app from Applications and it
# will register itself. It says so rather than failing silently.
set -u

APP="/Applications/AI Matrx Home Connection.app"
BINARY="$APP/Contents/MacOS/matrx-egress"

CONSOLE_USER="$(/usr/bin/stat -f%Su /dev/console 2>/dev/null || true)"
if [ -z "$CONSOLE_USER" ] || [ "$CONSOLE_USER" = "root" ] || [ "$CONSOLE_USER" = "loginwindow" ]; then
    echo "No one is signed in to this Mac, so the AI Matrx Home Connection was not set to start automatically. Open it from your Applications folder once and it will set itself up."
    exit 0
fi

CONSOLE_UID="$(/usr/bin/id -u "$CONSOLE_USER" 2>/dev/null || true)"
if [ -z "$CONSOLE_UID" ]; then
    echo "Could not identify the signed-in person, so the AI Matrx Home Connection was not set to start automatically. Open it from your Applications folder once and it will set itself up."
    exit 0
fi

# `launchctl asuser` puts the command in that person's GUI session, which is the only session a
# LaunchAgent (and a keychain) belongs to.
/bin/launchctl asuser "$CONSOLE_UID" /usr/bin/sudo -u "$CONSOLE_USER" "$BINARY" install \
    || echo "The AI Matrx Home Connection was installed but could not set itself to start automatically. Open it from your Applications folder once and it will."

exit 0

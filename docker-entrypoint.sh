#!/bin/sh
# Normalise ownership of the bind-mounted data directory, then drop privileges.
#
# Why this exists: docker-compose bind-mounts ./data so that permission-change audit
# records and send results are readable on the host. Those artifacts are written 0700/0600
# because they hold rollback values and recipient addresses. If the container writes them
# as root, they land on the host owned by root and unopenable by the developer -- the audit
# trail exists and cannot be read, which defeats its purpose. But the container cannot
# simply run as the host uid either: the Docker daemon creates a MISSING bind-mount source
# as root:root, so a non-root container cannot write to it at all.
#
# So: start as root, fix ownership, then drop. This covers every case automatically --
# a missing data/, one created by an earlier root container, a host uid that is not 1000,
# and subdirectories left behind by either.
set -eu

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"

if [ "$(id -u)" -ne 0 ]; then
    # The container user was overridden, so we can neither chown nor drop privileges.
    # Do not pretend otherwise: run as-is and let check_required_paths_writable() in
    # app/ui/state.py report anything this user cannot write.
    exec "$@"
fi

# Best effort. On Docker Desktop for macOS and Windows the file-sharing layer translates
# ownership and chown is a no-op or fails outright; the mount is writable regardless, so a
# failure here must not stop the container from starting.
chown -R "${APP_UID}:${APP_GID}" /app/data 2>/dev/null || true

# setpriv ships with python:3.13-slim (util-linux), so this needs no extra package.
# --clear-groups drops root's supplementary groups, which would otherwise be inherited.
exec setpriv --reuid="${APP_UID}" --regid="${APP_GID}" --clear-groups "$@"

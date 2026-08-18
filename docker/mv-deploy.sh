#!/bin/bash

SSH_HOST="mv.fornex.tgbot"
REMOTE_DIR="/srv/monkey-village/rwms"
IMAGE_TAR_NAME="rwms-amd64.tar"

ssh "${SSH_HOST}" "\
    if [ -f '${REMOTE_DIR}/${IMAGE_TAR_NAME}' ]; then \
        if [ -f '${REMOTE_DIR}/${IMAGE_TAR_NAME}.bak' ]; then \
            ts=\$(date +%Y%m%d-%H%M%S); \
            mv '${REMOTE_DIR}/${IMAGE_TAR_NAME}.bak' '${REMOTE_DIR}/${IMAGE_TAR_NAME}.'\"\${ts}\"'.bak'; \
        fi; \
        mv '${REMOTE_DIR}/${IMAGE_TAR_NAME}' '${REMOTE_DIR}/${IMAGE_TAR_NAME}.bak'; \
    fi"

scp "./${IMAGE_TAR_NAME}" "${SSH_HOST}:${REMOTE_DIR}"

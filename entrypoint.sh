#!/bin/sh
# Decode OPML RSS subscriptions from environment variable (if provided).
if [ -n "$FOLLOW_OPML_B64" ]; then
    echo "$FOLLOW_OPML_B64" | base64 -d > /app/feeds/follow.opml
    echo "Loaded feeds/follow.opml from FOLLOW_OPML_B64"
fi

exec "$@"

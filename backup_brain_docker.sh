#!/bin/bash
# Safe brain backup automation using Docker
# Backs up the SQLite database (SQLCipher encrypted) from the running container

set -euo pipefail

PROJECT_DIR="/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai"
CONTAINER_NAME="assistant-backend"
DATA_CONTAINER_PATH="/app/data"
BACKUP_DIR="$PROJECT_DIR/backups"
KEEP_BACKUPS=5  # Keep last N backups
LOG_FILE="$PROJECT_DIR/logs/backup.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[BACKUP]${NC} $1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE" 2>/dev/null || true
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING: $1" >> "$LOG_FILE" 2>/dev/null || true
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: $1" >> "$LOG_FILE" 2>/dev/null || true
}

mkdir -p "$PROJECT_DIR/logs" "$BACKUP_DIR"

log "=== Starting brain backup ==="

# Set DATA_DIR from PROJJECT_DIR
DATA_DIR="$PROJECT_DIR/assistant/data"

# Step 1: Get list of existing backups
log "Listing existing brain backups from container..."
EXISTING=$(docker exec "$CONTAINER_NAME" find "$DATA_CONTAINER_PATH" -name "brain-*.db" -type f 2>/dev/null | sort)

# Step 2: Create a new backup using timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_NAME="brain-$TIMESTAMP.db"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

# Copy the database from container to host
log "Creating backup: $BACKUP_NAME"
docker cp "${CONTAINER_NAME}:${DATA_CONTAINER_PATH}/assistant.db" "$BACKUP_PATH" 2>/dev/null && \
    log "Backup created: $BACKUP_PATH ($(stat -c%s "$BACKUP_PATH" 2>/dev/null || echo "unknown") bytes)" || \
    error "Failed to create backup from container"

# Step 3: Keep only the last KEEP_BACKUPS backups, delete older ones
log "Keeping last $KEEP_BACKUPS backups, deleting older ones..."
if [ -n "$EXISTING" ]; then
    OLD_BACKUPS=$(echo "$EXISTING" | head -n +$((KEEP_BACKUPS + 1)))
    if [ -n "$OLD_BACKUPS" ]; then
        echo "$OLD_BACKUPS" | while read -r old_backup; do
            log "Removing old backup: $(basename "$old_backup")"
            rm -f "$(dirname "$BACKUP_PATH")/$(basename "$old_backup")"
        done
    fi
fi

# Step 4: Clean up known test/false brain files from data directory
log "Cleaning up test/false brain files from data directory..."
FALSE_BRAINS=(
    "assistant.db.pre-identity-fix"
    "assistant.db.unencrypted"
    "assistant.db.unencrypted.old"
    "old_encrypted.db"
    "test-export.assistant-brain"
    "brain-export-encrypted.json"
)

for f in "${FALSE_BRAINS[@]}"; do
    if docker exec "$CONTAINER_NAME" test -f "$DATA_CONTAINER_PATH/$f" 2>/dev/null; then
        log "Removing false brain from container: $f"
        docker exec "$CONTAINER_NAME" rm -f "$DATA_CONTAINER_PATH/$f" 2>/dev/null
    fi
    # Also check host side
    if [ -f "$DATA_DIR/$f" ]; then
        log "Removing false brain from host: $f"
        rm -f "$DATA_DIR/$f"
    fi
done

# Also clean up pre-consolidation backups in the backups/ directory, keep latest 2
log "Cleaning up pre-consolidation backups (keeping latest 2)..."
PRE_CONSOLIDATION=$(docker exec "$CONTAINER_NAME" find "$DATA_CONTAINER_PATH" -name "assistant-pre-consolidation-*.db" -type f 2>/dev/null | sort -r | tail -n +3 || true)
if [ -n "$PRE_CONSOLIDATION" ]; then
    echo "$PRE_CONSOLIDATION" | while read -r old; do
        log "Removing old pre-consolidation backup: $(basename "$old")"
        docker exec "$CONTAINER_NAME" rm -f "$old" 2>/dev/null
    done
fi

# Step 5: List current state
log "=== Current backup state ==="
log "Backups in $BACKUP_DIR:"
ls -1 "$BACKUP_DIR"/brain-*.db 2>/dev/null | xargs -I{} basename {} || log "  (no brain backups)"

log "=== Backup complete ==="
log "Latest backup: $BACKUP_NAME"
log "Keep last $KEEP_BACKUPS backups automatically"
exit 0

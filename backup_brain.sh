#!/bin/bash
# Safe brain backup automation
# Backs up the SQLite database (SQLCipher encrypted) and cleans up old/test files

set -euo pipefail

PROJECT_DIR="/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai"
DATA_DIR="$PROJECT_DIR/assistant/data"
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

# Step 1: Create a backup using the Docker container's API or cp the DB
# The database is at assistant/data/assistant.db (SQLCipher encrypted)
DB_SOURCE="$DATA_DIR/assistant.db"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_NAME="brain-$TIMESTAMP.db"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

if [ ! -f "$DB_SOURCE" ]; then
    error "Database source not found: $DB_SOURCE"
    exit 1
fi

log "Copying database: $DB_SOURCE -> $BACKUP_PATH"
cp "$DB_SOURCE" "$BACKUP_PATH"
log "Backup created: $BACKUP_PATH ($(stat -c%s "$BACKUP_PATH") bytes)"

# Step 2: Keep only the last KEEP_BACKUPS backups, delete older ones
log "Keeping last $KEEP_BACKUPS backups, deleting older ones..."
BACKUPS=$(ls -1t "$BACKUP_DIR"/brain-*.db 2>/dev/null | head -n +$((KEEP_BACKUPS + 1)) || true)
if [ -n "$BACKUPS" ]; then
    echo "$BACKUPS" | while read -r old_backup; do
        log "Removing old backup: $(basename "$old_backup")"
        rm -f "$old_backup"
    done
fi

# Step 3: Clean up known test/false brain files
log "Cleaning up test/false brain files..."
FALSE_BRAINS=(
    "assistant.db.pre-identity-fix"
    "assistant.db.unencrypted"
    "assistant.db.unencrypted.old"
    "old_encrypted.db"
    "test-export.assistant-brain"
    "brain-export-encrypted.json"
)

for f in "${FALSE_BRAINS[@]}"; do
    if [ -f "$DATA_DIR/$f" ]; then
        log "Removing false brain: $f"
        rm -f "$DATA_DIR/$f"
    fi
done

# Also clean up pre-consolidation backups in the backups/ directory, keep latest 2
log "Cleaning up pre-consolidation backups (keeping latest 2)..."
PRE_CONSOLIDATION=$(ls -1t "$BACKUP_DIR"/assistant-pre-consolidation-*.db 2>/dev/null | tail -n +3 || true)
if [ -n "$PRE_CONSOLIDATION" ]; then
    echo "$PRE_CONSOLIDATION" | while read -r old; do
        log "Removing old pre-consolidation backup: $(basename "$old")"
        rm -f "$old"
    done
fi

# Step 5: List current state
log "=== Current backup state ==="
echo "Backups directory:"
ls -1 "$BACKUP_DIR"/brain-*.db 2>/dev/null | xargs -I{} basename {} || echo "  (no brain backups)"
echo ""
echo "Data directory (should not contain false brains):"
ls -1 "$DATA_DIR" | grep -E "(assistant\.db|pre-identity|unencrypted|old_encrypted|test-export)" || echo "  (no false brain files)"

log "=== Backup complete ==="
log "Active database: $(basename "$DB_SOURCE")"
log "Latest backup: $BACKUP_NAME"
exit 0

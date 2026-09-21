#!/bin/bash
# monitor.db (ai-monitor 컨테이너의 SQLite DB) 백업 스크립트
# - SQLite Online Backup API를 사용해 서비스 중단/락 걱정 없이 안전하게 백업
# - 백업 파일은 docker volume이 아니라 호스트 디스크에 gzip으로 저장 (volume 삭제/손상에도 보존)
# - 지정 보관일수(기본 30일)보다 오래된 백업은 자동 삭제

set -euo pipefail

CONTAINER="ai-monitor"
DB_PATH_IN_CONTAINER="/data/monitor.db"
BACKUP_DIR="/root/ai-monitor/backups"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
TMP_IN_CONTAINER="/tmp/monitor-backup-${TIMESTAMP}.db"
DEST_FILE="${BACKUP_DIR}/monitor-${TIMESTAMP}.db"
LOG_FILE="${BACKUP_DIR}/backup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    log "ERROR: ${CONTAINER} 컨테이너가 실행 중이 아님 - 백업 건너뜀"
    exit 1
fi

# SQLite Online Backup API로 컨테이너 내부에 안전하게 백업 생성
if ! docker exec "$CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('${DB_PATH_IN_CONTAINER}')
dst = sqlite3.connect('${TMP_IN_CONTAINER}')
with dst:
    src.backup(dst)
src.close()
dst.close()
"; then
    log "ERROR: 컨테이너 내부 백업 생성 실패"
    exit 1
fi

# 호스트로 복사 후 압축
if ! docker cp "${CONTAINER}:${TMP_IN_CONTAINER}" "$DEST_FILE"; then
    log "ERROR: docker cp 실패"
    docker exec "$CONTAINER" rm -f "$TMP_IN_CONTAINER" 2>/dev/null || true
    exit 1
fi
docker exec "$CONTAINER" rm -f "$TMP_IN_CONTAINER" 2>/dev/null || true

gzip "$DEST_FILE"
BACKUP_SIZE=$(du -h "${DEST_FILE}.gz" | cut -f1)
log "OK: ${DEST_FILE}.gz 생성 완료 (${BACKUP_SIZE})"

# 오래된 백업 정리
DELETED=$(find "$BACKUP_DIR" -name 'monitor-*.db.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "INFO: ${RETENTION_DAYS}일 초과 백업 ${DELETED}개 삭제"
fi

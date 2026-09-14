#!/usr/bin/env bash
#
# 定期バックアップ。backup.sh を呼び、日次 7 世代・週次 4 世代を残して古いものを消します。
#
#   ./deploy/backup-rotate.sh                        # 既定の /var/backups/task-manager へ
#   BACKUP_DIR=/data/backup ./deploy/backup-rotate.sh
#
# systemd タイマーから毎日呼ぶ想定です（deploy/task-manager-backup.timer）。
# 週次は日曜のバックアップを weekly/ にハードリンクするだけなので、容量は増えません。
set -euo pipefail

APP_DIR=${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/task-manager}
KEEP_DAILY=${KEEP_DAILY:-7}
KEEP_WEEKLY=${KEEP_WEEKLY:-4}
WEEKLY_DOW=${WEEKLY_DOW:-7}          # 1=月 ... 7=日

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

# --- 取得 -----------------------------------------------------------------
before=$(ls -1 "$BACKUP_DIR/daily" 2>/dev/null | wc -l)
APP_DIR="$APP_DIR" "$APP_DIR/deploy/backup.sh" "$BACKUP_DIR/daily"

latest=$(ls -1t "$BACKUP_DIR/daily"/task-manager-*.tar.gz 2>/dev/null | head -1 || true)
if [ -z "$latest" ] || [ "$(ls -1 "$BACKUP_DIR/daily" | wc -l)" -le "$before" ]; then
    echo "バックアップが作成されませんでした" >&2
    exit 1
fi

# --- 中身が壊れていないか確かめる ------------------------------------------
if ! tar tzf "$latest" >/dev/null 2>&1; then
    echo "作成したアーカイブを展開できません: $latest" >&2
    exit 1
fi
if ! tar tzf "$latest" | grep -q 'database\.sql$'; then
    echo "アーカイブに database.sql が入っていません: $latest" >&2
    exit 1
fi

# --- 週次に取っておく（ハードリンクなので容量は食わない） --------------------
if [ "$(date +%u)" = "$WEEKLY_DOW" ]; then
    ln -f "$latest" "$BACKUP_DIR/weekly/$(basename "$latest")" 2>/dev/null \
        || cp -a "$latest" "$BACKUP_DIR/weekly/"
    echo "==> 週次に保存しました: weekly/$(basename "$latest")"
fi

# --- 古い世代を消す --------------------------------------------------------
prune() {
    local dir=$1 keep=$2
    local removed=0
    # 新しい順に並べ、keep 件を超えたものを消す
    while IFS= read -r old; do
        rm -f "$old"
        removed=$((removed + 1))
    done < <(ls -1t "$dir"/task-manager-*.tar.gz 2>/dev/null | tail -n +$((keep + 1)))
    [ "$removed" -gt 0 ] && echo "==> $(basename "$dir"): 古い $removed 件を削除しました"
    return 0
}
prune "$BACKUP_DIR/daily" "$KEEP_DAILY"
prune "$BACKUP_DIR/weekly" "$KEEP_WEEKLY"

echo "==> 保有: 日次 $(ls -1 "$BACKUP_DIR/daily"/*.tar.gz 2>/dev/null | wc -l) 件 / 週次 $(ls -1 "$BACKUP_DIR/weekly"/*.tar.gz 2>/dev/null | wc -l) 件 / 合計 $(du -sh "$BACKUP_DIR" | cut -f1)"

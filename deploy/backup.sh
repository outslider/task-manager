#!/usr/bin/env bash
#
# データベースと添付ファイルをまとめて 1 つのアーカイブに保存します。
# 日次バックアップにも、別サーバーへの移行にも使えます。
#
#   ./deploy/backup.sh                     # ./ に task-manager-YYYYmmdd-HHMM.tar.gz を作成
#   ./deploy/backup.sh /backup             # 保存先を指定
#   APP_DIR=/opt/task-manager ./deploy/backup.sh /backup
#
# 復元:
#   tar xzf task-manager-*.tar.gz
#   mysql -u tmapp -p task_manager < task-manager-*/database.sql
#   cp -a task-manager-*/uploads/. /opt/task-manager/data/uploads/
set -euo pipefail

APP_DIR=${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUT_DIR=${1:-.}
CONFIG="$APP_DIR/config.ini"
[ -f "$CONFIG" ] || { echo "config.ini が見つかりません: $CONFIG" >&2; exit 1; }

value() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONFIG" | head -1; }
DB_HOST=$(value host); DB_PORT=$(value port); DB_NAME=$(value name)
DB_USER=$(value user); DB_PASSWORD=$(value password)
: "${DB_HOST:=127.0.0.1}" "${DB_PORT:=3306}" "${DB_NAME:=task_manager}" "${DB_USER:=tmapp}"

STAMP=$(date +%Y%m%d-%H%M)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
BUNDLE="$WORK/task-manager-$STAMP"
mkdir -p "$BUNDLE"

echo "==> データベース $DB_NAME をダンプします"
MYSQL_PWD="$DB_PASSWORD" mysqldump \
    --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" \
    --single-transaction --routines --default-character-set=utf8mb4 \
    "$DB_NAME" > "$BUNDLE/database.sql"

echo "==> 添付ファイルをコピーします"
mkdir -p "$BUNDLE/uploads"
if [ -d "$APP_DIR/data/uploads" ]; then
    cp -a "$APP_DIR/data/uploads/." "$BUNDLE/uploads/" 2>/dev/null || true
fi

cat > "$BUNDLE/MANIFEST.txt" <<META
取得日時 : $(date '+%Y-%m-%d %H:%M:%S')
取得元   : $(hostname)
アプリ   : $APP_DIR
データベース : $DB_NAME ($DB_HOST:$DB_PORT)
添付件数 : $(find "$BUNDLE/uploads" -type f | wc -l)
META

mkdir -p "$OUT_DIR"
ARCHIVE=$(cd "$OUT_DIR" && pwd)/task-manager-$STAMP.tar.gz
tar czf "$ARCHIVE" -C "$WORK" "task-manager-$STAMP"
echo "==> 作成しました: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

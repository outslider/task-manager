#!/usr/bin/env bash
#
# タスク管理システム インストーラ（新規導入・アップグレード兼用）
#
#   sudo ./deploy/install.sh                       # 既定値で導入
#   sudo ./deploy/install.sh --create-db           # DB とアプリ用ユーザーも作る
#   sudo ./deploy/install.sh --dir /srv/tm --port 9000
#
# 2 回目以降は同じコマンドでアップグレードになります（config.ini と data/ は保持）。
set -euo pipefail

APP_DIR=/opt/task-manager
APP_USER=taskmgr
SERVICE=task-manager
PORT=8080
BIND=127.0.0.1
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=task_manager
DB_USER=tmapp
DB_PASSWORD=""
CREATE_DB=0
WITH_SERVICE=1
PYTHON=""

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

オプション:
  --dir PATH            インストール先            (既定 /opt/task-manager)
  --user NAME           実行ユーザー              (既定 taskmgr)
  --service NAME        systemd サービス名        (既定 task-manager)
  --bind ADDR           待ち受けアドレス          (既定 127.0.0.1)
  --port N              待ち受けポート            (既定 8080)
  --db-host HOST        DB ホスト                 (既定 127.0.0.1)
  --db-port N           DB ポート                 (既定 3306)
  --db-name NAME        DB 名                     (既定 task_manager)
  --db-user NAME        DB ユーザー               (既定 tmapp)
  --db-password PASS    DB パスワード             (未指定かつ --create-db なら自動生成)
  --create-db           DB とユーザーを作成する（管理者権限で mysql に接続します）
  --no-service          systemd への登録を行わない
  --python PATH         使用する python3          (既定 python3)
  -h, --help            このヘルプ
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) APP_DIR=$2; shift 2 ;;
        --user) APP_USER=$2; shift 2 ;;
        --service) SERVICE=$2; shift 2 ;;
        --bind) BIND=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        --db-host) DB_HOST=$2; shift 2 ;;
        --db-port) DB_PORT=$2; shift 2 ;;
        --db-name) DB_NAME=$2; shift 2 ;;
        --db-user) DB_USER=$2; shift 2 ;;
        --db-password) DB_PASSWORD=$2; shift 2 ;;
        --create-db) CREATE_DB=1; shift ;;
        --no-service) WITH_SERVICE=0; shift ;;
        --python) PYTHON=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "不明なオプション: $1" >&2; usage; exit 1 ;;
    esac
done

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}
say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mエラー:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "root で実行してください（sudo $0 ...）"
command -v "$PYTHON" >/dev/null || die "$PYTHON が見つかりません"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "Python 3.8 以上が必要です（$($PYTHON -V)）"
[ -f "$SRC_DIR/server.py" ] || die "$SRC_DIR にアプリ本体が見つかりません"

UPGRADE=0
[ -f "$APP_DIR/server.py" ] && UPGRADE=1

# ---------------------------------------------------------------- ユーザー
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    say "実行ユーザー $APP_USER を作成します"
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER" 2>/dev/null \
        || useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$APP_USER"
fi

# ------------------------------------------------------------ ファイル配置
if [ "$UPGRADE" = 1 ]; then
    say "既存のインストールを更新します（$APP_DIR）"
    systemctl stop "$SERVICE" 2>/dev/null || true
else
    say "$APP_DIR にインストールします"
fi
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR" "$APP_DIR/data" "$APP_DIR/data/uploads"
for item in server.py requirements.txt README.md app static docs deploy config.ini.example; do
    [ -e "$SRC_DIR/$item" ] || continue
    rm -rf "${APP_DIR:?}/$item"
    cp -a "$SRC_DIR/$item" "$APP_DIR/"
done
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ------------------------------------------------------------------- venv
say "Python 仮想環境と依存パッケージを用意します"
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" "$PYTHON" -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip \
    || echo "  (pip の更新はスキップしました)"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" \
    || die "依存パッケージの導入に失敗しました。オフライン環境では PyMySQL の wheel を手動で配置してください"

# --------------------------------------------------------------------- DB
if [ "$CREATE_DB" = 1 ]; then
    [ -n "$DB_PASSWORD" ] || DB_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)
    say "データベース $DB_NAME とユーザー $DB_USER を作成します"
    mysql <<SQL || die "mysql への接続に失敗しました。手動で作成してから --create-db なしで再実行してください"
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
fi

# ---------------------------------------------------------------- config
if [ -f "$APP_DIR/config.ini" ]; then
    say "既存の config.ini をそのまま使います"
    # 表示用に、実際に使われる値を config.ini から読み直す
    conf_value() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$APP_DIR/config.ini" | head -1; }
    DB_HOST=$(conf_value host); DB_PORT=$(conf_value port)
    DB_NAME=$(conf_value name); DB_USER=$(conf_value user)
    BIND=$(sed -n '/^\[server\]/,$p' "$APP_DIR/config.ini" \
        | sed -n 's/^[[:space:]]*host[[:space:]]*=[[:space:]]*//p' | head -1)
    PORT=$(sed -n '/^\[server\]/,$p' "$APP_DIR/config.ini" \
        | sed -n 's/^[[:space:]]*port[[:space:]]*=[[:space:]]*//p' | head -1)
else
    [ -n "$DB_PASSWORD" ] || die "--db-password で DB パスワードを指定するか、--create-db を付けてください"
    say "config.ini を作成します"
    cat > "$APP_DIR/config.ini" <<CONF
; タスク管理システム 設定ファイル
[database]
host = $DB_HOST
port = $DB_PORT
name = $DB_NAME
user = $DB_USER
password = $DB_PASSWORD

[server]
host = $BIND
port = $PORT
max_upload_bytes = 26214400
; HTTPS で公開する場合は 1 にしてください
secure_cookie = 0
CONF
fi
chown "$APP_USER:$APP_USER" "$APP_DIR/config.ini"
chmod 600 "$APP_DIR/config.ini"

# ------------------------------------------------------------- スキーマ
say "データベーススキーマを作成／更新します"
INIT_OUT=$(sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/server.py" --init-db 2>&1) \
    || { echo "$INIT_OUT" >&2; die "データベースに接続できませんでした。config.ini を確認してください"; }
echo "$INIT_OUT" | sed 's/^/    /'

# ------------------------------------------------------------- systemd
if [ "$WITH_SERVICE" = 1 ]; then
    say "systemd サービス $SERVICE を登録します"
    cat > "/etc/systemd/system/$SERVICE.service" <<UNIT
[Unit]
Description=Task Manager
After=network-online.target mariadb.service mysqld.service
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/server.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP_DIR/data
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null
    systemctl restart "$SERVICE"
    sleep 2
    systemctl is-active --quiet "$SERVICE" \
        || die "起動に失敗しました: journalctl -u $SERVICE -n 50 を確認してください"
fi

echo
say "完了しました"
cat <<DONE

  インストール先 : $APP_DIR
  実行ユーザー   : $APP_USER
  待ち受け       : http://$BIND:$PORT/
  データベース   : $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME
DONE
if [ "$WITH_SERVICE" = 1 ]; then
cat <<DONE
  サービス操作   : systemctl {status|restart|stop} $SERVICE
  ログ           : journalctl -u $SERVICE -f
DONE
fi
if [ "$CREATE_DB" = 1 ]; then
    echo "  DB パスワード  : $DB_PASSWORD   （config.ini にも記録されています）"
fi
ADMIN_LINE=$(echo "$INIT_OUT" | grep -E 'パスワード *:' | head -1 | sed 's/^ *//')
if [ -n "$ADMIN_LINE" ]; then
cat <<DONE

  ------------------------------------------------------------------
   初期管理者アカウントを作成しました（この表示は一度きりです）
     メール    : $(echo "$INIT_OUT" | grep -E 'メール *:' | head -1 | sed 's/.*: *//')
     $ADMIN_LINE
   ログイン後に必ずパスワードを変更してください。
  ------------------------------------------------------------------
DONE
fi

#!/usr/bin/env bash
# Gmail OAuth 自動設定：啟用 API → 建 OAuth consent screen → 建 Desktop client → 下載 credentials.json
set -euo pipefail

PROJECT=$(gcloud config get-value project 2>/dev/null)
SECRETS_DIR="$(dirname "$0")/../data/secrets"
CREDS_FILE="$SECRETS_DIR/credentials.json"

echo "=== Gmail OAuth 設定 ==="
echo "GCP Project: $PROJECT"
echo ""

# 1. 啟用 Gmail API
echo "[1/4] 啟用 Gmail API..."
gcloud services enable gmail.googleapis.com --project="$PROJECT" 2>/dev/null && echo "  ✅ Gmail API 已啟用" || echo "  ℹ️  已啟用（或需手動確認）"

# 2. 設定 OAuth consent screen（測試模式）
echo "[2/4] 設定 OAuth consent screen..."
# 檢查是否已有 consent screen
if gcloud alpha iap oauth-brands list --project="$PROJECT" 2>/dev/null | grep -q "name:"; then
    echo "  ℹ️  OAuth consent screen 已存在"
else
    echo "  ⚠️  需要手動設定 OAuth consent screen（gcloud 不完全支援）"
    echo "  → 開啟瀏覽器..."
    open "https://console.cloud.google.com/apis/credentials/consent?project=$PROJECT"
    echo ""
    echo "  請在瀏覽器中："
    echo "    1. 選 External → Create"
    echo "    2. App name: jp-pm-jobs"
    echo "    3. User support email: 你的 email"
    echo "    4. Developer contact: 你的 email"
    echo "    5. Save → Scopes 頁不用改 → Save → Test users 加你自己的 email → Save"
    echo ""
    read -p "  完成後按 Enter 繼續..."
fi

# 3. 建 OAuth client（Desktop app）
echo "[3/4] 建 OAuth Desktop client..."
CLIENT_NAME="jp-pm-jobs-desktop"

# 嘗試用 gcloud 建 OAuth client
RESULT=$(gcloud alpha iap oauth-clients create \
    "projects/$PROJECT/brands/-" \
    --display_name="$CLIENT_NAME" 2>&1) || true

# gcloud alpha 不一定能建 Desktop type，改用 REST API
if echo "$RESULT" | grep -qi "error\|not found\|invalid"; then
    echo "  gcloud alpha 不支援 Desktop client，改用 REST API..."

    ACCESS_TOKEN=$(gcloud auth print-access-token)

    # 先列出已有的 OAuth clients
    EXISTING=$(curl -s \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://oauth2.googleapis.com/v1/projects/$PROJECT/oauthclients" 2>/dev/null || echo "")

    # 建 Desktop OAuth client
    RESPONSE=$(curl -s -X POST \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "client_name": "'"$CLIENT_NAME"'",
            "application_type": "installed"
        }' \
        "https://oauth2.googleapis.com/v1/projects/$PROJECT/oauthClients" 2>/dev/null || echo "")

    if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('client_id',''))" 2>/dev/null | grep -q "."; then
        CLIENT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['client_id'])")
        CLIENT_SECRET=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['client_secret'])")
        echo "  ✅ OAuth client 已建立"
    else
        echo "  ⚠️  REST API 也無法建立，需手動下載 credentials.json"
        echo "  → 開啟瀏覽器..."
        open "https://console.cloud.google.com/apis/credentials?project=$PROJECT"
        echo ""
        echo "  請在瀏覽器中："
        echo "    1. 點 '+ CREATE CREDENTIALS' → OAuth client ID"
        echo "    2. Application type: Desktop app"
        echo "    3. Name: $CLIENT_NAME"
        echo "    4. Create → 點 'DOWNLOAD JSON'"
        echo "    5. 下載的檔案會在 ~/Downloads/"
        echo ""
        read -p "  下載完成後按 Enter 繼續..."

        # 從 Downloads 找最新的 client_secret*.json
        DOWNLOADED=$(ls -t ~/Downloads/client_secret*.json 2>/dev/null | head -1)
        if [ -n "$DOWNLOADED" ]; then
            mkdir -p "$SECRETS_DIR"
            cp "$DOWNLOADED" "$CREDS_FILE"
            echo "  ✅ 已複製 $DOWNLOADED → $CREDS_FILE"
        else
            echo "  ❌ 找不到下載的 credentials.json，請手動放到 $CREDS_FILE"
            exit 1
        fi
        echo ""
        echo "[4/4] 現在跑 OAuth 授權..."
        cd "$(dirname "$0")/.."
        python3 -m inbox.auth
        exit 0
    fi
fi

# 4. 寫 credentials.json
if [ -n "${CLIENT_ID:-}" ] && [ -n "${CLIENT_SECRET:-}" ]; then
    mkdir -p "$SECRETS_DIR"
    python3 -c "
import json
creds = {
    'installed': {
        'client_id': '$CLIENT_ID',
        'client_secret': '$CLIENT_SECRET',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
        'redirect_uris': ['http://localhost']
    }
}
with open('$CREDS_FILE', 'w') as f:
    json.dump(creds, f, indent=2)
print(f'  ✅ credentials.json 已寫入 $CREDS_FILE')
"
fi

echo ""
echo "[4/4] 現在跑 OAuth 授權..."
cd "$(dirname "$0")/.."
python3 -m inbox.auth

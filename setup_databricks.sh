#!/usr/bin/env bash
# setup_databricks.sh
# Run this once before first deploy to create the secret scope and all required secrets.
# Prerequisites: Databricks CLI v2 installed + authenticated (databricks auth login)
#
# Usage:
#   chmod +x setup_databricks.sh
#   ./setup_databricks.sh

set -e

SCOPE="attribution"

echo "=== Creating secret scope: $SCOPE (if not exists) ==="
databricks secrets create-scope "$SCOPE" 2>/dev/null || echo "(scope already exists)"

echo ""
echo "=== Storing secrets ==="
echo "Paste each value when prompted. Press Enter to skip (secret stays unchanged)."
echo ""

put_secret() {
  local key="$1"
  local prompt="$2"
  read -rsp "  $prompt [$key]: " value
  echo ""
  if [ -n "$value" ]; then
    printf '%s' "$value" | databricks secrets put-secret "$SCOPE" "$key" --string-value "$value"
    echo "  ✓ $key saved"
  else
    echo "  – $key skipped"
  fi
}

put_secret "META_ACCESS_TOKEN"              "Meta long-lived access token"
put_secret "HUBSPOT_ACCESS_TOKEN"           "HubSpot private app token"
put_secret "STRIPE_SECRET_KEY"              "Stripe secret key (sk_live_...)"
put_secret "ANTHROPIC_API_KEY"              "Anthropic API key (sk-ant-...)"
put_secret "GOOGLE_ADS_DEVELOPER_TOKEN"     "Google Ads developer token"
put_secret "GOOGLE_ADS_CLIENT_ID"           "Google Ads OAuth client ID"
put_secret "GOOGLE_ADS_CLIENT_SECRET"       "Google Ads OAuth client secret"
put_secret "GOOGLE_ADS_REFRESH_TOKEN"       "Google Ads OAuth refresh token"
put_secret "LINKEDIN_ACCESS_TOKEN"          "LinkedIn Marketing API token"
put_secret "SENDGRID_API_KEY"               "SendGrid API key (for email delivery)"
put_secret "GMAIL_SENDER"                   "Gmail sender address (if using Gmail fallback)"
put_secret "GMAIL_APP_PASSWORD"             "Gmail app password (if using Gmail fallback)"
put_secret "TELEGRAM_BOT_TOKEN"             "Telegram bot token (ARIE)"
put_secret "TELEGRAM_CHAT_ID"               "Telegram chat ID (ARIE)"
put_secret "OPENAI_API_KEY"                 "OpenAI API key (ARIE voice, optional)"

echo ""
echo "=== Creating Unity Catalog resources ==="
echo "Creating main.attribution_ops schema and client_registry Volume..."

databricks unity-catalog schemas create --catalog-name main --name attribution_ops 2>/dev/null || echo "(schema already exists)"
databricks unity-catalog volumes create \
  --catalog-name main \
  --schema-name attribution_ops \
  --name client_registry \
  --volume-type MANAGED 2>/dev/null || echo "(volume already exists)"

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Edit attribution_agent/attribution_agent/config/client_config.py"
echo "     and set your real Meta ad account ID and HubSpot pipeline ID."
echo "  2. Run: databricks bundle deploy"
echo "  3. Run: databricks bundle run attribution_pipeline --dry-run"

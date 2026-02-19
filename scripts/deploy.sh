#!/usr/bin/env bash
# deploy.sh — Set up the A11y Remediation App on an Ubuntu ARM server.
#
# Usage:
#   1. SSH into your Oracle Cloud instance
#   2. Clone the repo: git clone https://github.com/jenniferkleiman/WCAGproject.git ~/a11y-remediate
#   3. Run: bash ~/a11y-remediate/scripts/deploy.sh
#
# Prerequisites:
#   - Ubuntu 24.04 ARM (Oracle Cloud Always Free VM.Standard.A1.Flex)
#   - SSH access as the ubuntu user
#   - .env.production file placed at ~/a11y-remediate/.env.production with:
#       GEMINI_API_KEY=...
#       ANTHROPIC_API_KEY=...
#       JWT_SECRET=<64-char hex, generate with: python3 -c "import secrets; print(secrets.token_hex(32))">

set -euo pipefail

APP_DIR="$HOME/a11y-remediate"
VENV="$APP_DIR/.venv"

echo "=== A11y Remediation App — Server Setup ==="

# ── 1. System packages ──────────────────────────────────────────
echo ""
echo "[1/6] Installing system packages..."
sudo apt update
sudo apt install -y \
    python3 python3-venv python3-pip \
    openjdk-17-jre-headless \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgdk-pixbuf2.0-0 libffi-dev \
    git caddy

# ── 2. Python venv + dependencies ───────────────────────────────
echo ""
echo "[2/6] Setting up Python virtual environment..."
cd "$APP_DIR"

if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -e .

# ── 3. Build Java JARs (if gradle is available) ────────────────
echo ""
echo "[3/6] Building Java components..."

# Check if JARs already exist (pre-built and committed)
ITEXT_JAR="$APP_DIR/java/itext-tagger/build/libs/itext-tagger-all.jar"
HTML2PDF_JAR="$APP_DIR/java/html-to-pdf/build/libs/html-to-pdf-all.jar"

if [ -f "$ITEXT_JAR" ] && [ -f "$HTML2PDF_JAR" ]; then
    echo "  Java JARs already present, skipping build."
else
    if command -v gradle &> /dev/null; then
        cd "$APP_DIR/java/itext-tagger" && gradle fatJar
        cd "$APP_DIR/java/html-to-pdf" && gradle fatJar
        cd "$APP_DIR"
    else
        echo "  WARNING: gradle not found and JARs not present."
        echo "  Install gradle (sudo snap install gradle --classic) and rebuild:"
        echo "    cd java/itext-tagger && gradle fatJar"
        echo "    cd java/html-to-pdf && gradle fatJar"
    fi
fi

# ── 4. Verify .env.production ───────────────────────────────────
echo ""
echo "[4/6] Checking production config..."

ENV_FILE="$APP_DIR/.env.production"
if [ ! -f "$ENV_FILE" ]; then
    echo "  ERROR: $ENV_FILE not found!"
    echo "  Create it with:"
    echo "    GEMINI_API_KEY=..."
    echo "    ANTHROPIC_API_KEY=..."
    echo "    JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    exit 1
fi

# Check required keys are present
for key in GEMINI_API_KEY ANTHROPIC_API_KEY JWT_SECRET; do
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        echo "  WARNING: $key not found in $ENV_FILE"
    fi
done
echo "  Config looks good."

# ── 5. Install systemd service ──────────────────────────────────
echo ""
echo "[5/6] Setting up systemd service..."
sudo cp "$APP_DIR/scripts/a11y-remediate.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable a11y-remediate
sudo systemctl restart a11y-remediate

echo "  Service started. Check status: sudo systemctl status a11y-remediate"
echo "  View logs: journalctl -u a11y-remediate -f"

# ── 6. Configure Caddy ─────────────────────────────────────────
echo ""
echo "[6/6] Setting up Caddy reverse proxy..."

CADDY_FILE="/etc/caddy/Caddyfile"
if grep -q "YOUR_DOMAIN" "$APP_DIR/scripts/Caddyfile"; then
    echo "  NOTE: Edit scripts/Caddyfile and replace YOUR_DOMAIN with your actual domain."
    echo "  Then run:"
    echo "    sudo cp $APP_DIR/scripts/Caddyfile $CADDY_FILE"
    echo "    sudo systemctl reload caddy"
else
    sudo cp "$APP_DIR/scripts/Caddyfile" "$CADDY_FILE"
    sudo systemctl reload caddy
    echo "  Caddy configured and reloaded."
fi

# ── Done ────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Point your domain's A record to this server's public IP"
echo "  2. Edit scripts/Caddyfile — replace YOUR_DOMAIN with your domain"
echo "  3. sudo cp scripts/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy"
echo "  4. Verify: curl https://YOUR_DOMAIN/api/health"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status a11y-remediate    # App status"
echo "  journalctl -u a11y-remediate -f          # App logs"
echo "  sudo systemctl status caddy              # Caddy status"
echo "  sudo systemctl restart a11y-remediate    # Restart app"

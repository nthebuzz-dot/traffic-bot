#!/bin/bash
# Ubuntu / Debian installation script for web-traffic-bot dependencies
# Tested on Ubuntu 20.04, 22.04, 24.04
#
# IMPORTANT: Snap Chromium CANNOT be used with Selenium.
# This script removes snap Chromium and installs the apt version.

set -e

echo "=== Updating apt cache ==="
sudo apt update

echo ""
echo "=== Installing Python 3, pip, venv, and utilities ==="
sudo apt install -y python3 python3-pip python3-venv wget unzip curl

echo ""
echo "=== Removing snap Chromium (incompatible with Selenium) ==="
if snap list chromium &>/dev/null 2>&1; then
    echo "Snap Chromium found — removing it..."
    sudo snap remove chromium
    echo "Snap Chromium removed."
else
    echo "Snap Chromium not installed — skipping."
fi

echo ""
echo "=== Installing apt Chromium and ChromeDriver ==="

# Detect Ubuntu version and install the correct package names
UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "0")
MAJOR_VERSION=$(echo "$UBUNTU_VERSION" | cut -d. -f1)

if [ "$MAJOR_VERSION" -ge 22 ] 2>/dev/null; then
    echo "Ubuntu 22.04+ detected — installing chromium + chromium-driver"
    sudo apt install -y chromium chromium-driver
elif apt-cache show chromium-browser &>/dev/null 2>&1; then
    echo "Ubuntu 20.04 detected — installing chromium-browser + chromium-chromedriver"
    sudo apt install -y chromium-browser chromium-chromedriver
else
    echo "WARNING: Could not detect Ubuntu version or find Chromium packages."
    echo "Try manually: sudo apt install -y chromium chromium-driver"
fi

echo ""
echo "=== Verifying installations ==="

# Print browser version
for bin in chromium chromium-browser; do
    if command -v "$bin" &>/dev/null; then
        echo "Browser : $($bin --version 2>/dev/null || echo 'version check failed')"
        break
    fi
done

# Print chromedriver version
for bin in chromedriver chromium-chromedriver; do
    if command -v "$bin" &>/dev/null; then
        echo "Driver  : $($bin --version 2>/dev/null || echo 'version check failed')"
        break
    fi
done

echo ""
echo "=== System setup complete! ==="
echo ""
echo "Next steps:"
echo "  python3 -m venv venv"
echo "  source venv/bin/activate"
echo "  pip install -e ."
echo "  web-traffic-bot --dashboard"

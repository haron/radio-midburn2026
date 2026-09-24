#!/usr/bin/env bash
# bash unofficial strict mode:
set -euo pipefail
IFS=$'\n\t'
[[ -n ${DEBUG:-""} ]] && set -x

for HOST; do
    ssh $HOST <<EOT
        apt install -y mpd ffmpeg vim
        systemctl disable --now mpd mpd.socket
        [[ -f /usr/bin/tailscale ]] \
            || { curl -fsSL https://tailscale.com/install.sh | sh; tailscale up; }
        [[ -f /usr/local/bin/uv ]] \
            || curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
        uv --directory /opt/radio sync
EOT
done

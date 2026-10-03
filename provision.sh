#!/usr/bin/env bash
# bash unofficial strict mode:
set -euo pipefail
IFS=$'\n\t'
[[ -n ${DEBUG:-""} ]] && set -x

for HOST; do
    ssh $HOST <<EOT
        apt install -y mpd ffmpeg vim dstat libraspberrypi-bin
        systemctl disable --now mpd mpd.socket
        timedatectl set-timezone Asia/Jerusalem
        timedatectl set-ntp true
        systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
        mkdir -p /etc/systemd/system.conf.d
        printf '[Manager]\nRuntimeWatchdogSec=15s\n' > /etc/systemd/system.conf.d/watchdog.conf
        echo kernel.panic=10 > /etc/sysctl.d/90-panic-reboot.conf
        sysctl -p /etc/sysctl.d/90-panic-reboot.conf
        grep -q '^temp_soft_limit=' /boot/firmware/config.txt || echo temp_soft_limit=70 >> /boot/firmware/config.txt
        systemctl daemon-reexec
        [[ -f /usr/bin/tailscale ]] \
            || { curl -fsSL https://tailscale.com/install.sh | sh; tailscale up; }
        [[ -f /usr/local/bin/uv ]] \
            || curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
        amixer sset PCM 100% unmute
        alsactl store
        uv --directory /opt/radio sync
        systemctl enable --now /opt/radio/radio.service
EOT
done

#!/usr/bin/env bash
# Raspberry Pi power and thermal health, now and in history. Ends with what's wrong right now,
# exits 1 if anything is, or if under-voltage or throttling happened since boot.
# The soft temp limit (temp_soft_limit=70 from provision.sh, ARM drops to 1200 MHz) is normal operation, so it's only a warning.
# Run on the Pi, or from the Mac: make healthcheck
set -euo pipefail

# vcgencmd (libraspberrypi-bin): Armbian's mainline kernel has no get_throttled in sysfs.
# Firmware flags: bits 0-3 are "now", bits 16-19 the same events "since boot".
flags=$(( $(vcgencmd get_throttled | cut -d= -f2) ))
names=(under-voltage freq-capped throttled soft-temp-limit)
report() { # $1: bit offset
    local out=()
    for i in "${!names[@]}"; do (( flags >> ($1 + i) & 1 )) && out+=("${names[$i]}"); done
    echo "${out[*]:-ok}"
}
echo "time        $(date '+%F %T %Z')"
printf 'throttled   0x%x\n' $flags
echo "now         $(report 0)"
echo "since boot  $(report 16)"

echo "temp        $(vcgencmd measure_temp | cut -d= -f2)"
echo "arm clock   $(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 )) MHz (max $(vcgencmd get_config arm_freq | cut -d= -f2))"
echo "core volts  $(vcgencmd measure_volts core | cut -d= -f2)"
echo "uptime     $(uptime)"

pattern='undervoltage|voltage normali|throttl|critical temp|lockup|hung_task|panic|oops'
echo; echo "== boots (only the current one if the journal isn't persistent)"
journalctl --list-boots --no-pager | tail -n5
for boot in -2 -1 0; do
    journalctl -k -b $boot --no-pager -q 2>/dev/null | grep -iE "$pattern" | sed "s/^/[boot $boot] /" | tail -n10 || true
done

problems=() warn=''
for i in 0 1 2; do (( flags >> i & 1 )) && problems+=("${names[$i]}"); done
(( flags & 0x8 )) && warn=' (warning: soft-temp-limit)'
[[ $(timedatectl show -p NTPSynchronized --value) == yes ]] || problems+=("clock not synced")
systemctl -q is-active radio || problems+=("radio.service $(systemctl is-active radio)")
echo; echo "== now: $(IFS=,; echo "${problems[*]:-OK}")$warn"

(( (flags >> 16 & 0x7) == 0 && ${#problems[@]} == 0 ))

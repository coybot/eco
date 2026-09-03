#!/bin/bash
# One line of machine state, appended every 30 s.
#
# Why this exists: thor restarts repeatedly and leaves no cause behind. pstore
# is empty (ramoops is loaded, so a kernel panic would have been captured), and
# the journal for the previous boot is unreadable because the clock starts at
# 1969 with no working RTC. An abrupt power loss writes nothing at all by
# definition — so the only way to learn anything is to have already written the
# state continuously. After the next restart, the LAST line here is a snapshot
# from at most 30 s before it died: temperatures, throttling, load, memory.
#
# Deliberately append-only, flushed per write, and outside /tmp so it survives
# a reboot. If it shows temps climbing or a throttle flag set, that is thermal.
# If the last line looks completely healthy, that points at power rather than
# anything the OS did.
LOG=/var/log/thor-heartbeat.log
{
  printf '%s uptime=%ss load=%s' \
    "$(date -Is)" \
    "$(cut -d' ' -f1 /proc/uptime)" \
    "$(cut -d' ' -f1-3 /proc/loadavg | tr ' ' ',')"
  for z in /sys/class/thermal/thermal_zone*/; do
    t=$(cat "$z/temp" 2>/dev/null) || continue
    n=$(cat "$z/type" 2>/dev/null)
    printf ' %s=%sC' "$n" "$((t/1000))"
  done
  # Tegra throttling / power-capping counters, when the platform exposes them.
  for f in /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq; do
    [ -r "$f" ] && printf ' cpu0khz=%s' "$(cat "$f")"
  done
  printf ' memfree=%skB' "$(awk '/MemAvailable/{print $2}' /proc/meminfo)"
  printf ' sim=%s' "$(systemctl --user -M <user>@ is-active presidio-sim 2>/dev/null || echo n/a)"
  printf '\n'
} >> "$LOG" 2>/dev/null
# Keep it bounded: ~20k lines is about a week at 30 s.
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
  tail -n 10000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

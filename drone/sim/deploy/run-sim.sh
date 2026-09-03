#!/bin/bash
# Launch the Godot sim fullscreen on this box's own monitor.
#
# Exists because the display number is NOT stable across reboots and hardcoding
# it silently bricks the demo. The unit used to set DISPLAY=:1 and wait for
# /tmp/.X11-unix/X1, which was correct at the time: someone had logged in on top
# of the GDM greeter, so the greeter held :0 and the real session got :1. Once
# GDM autologin was turned on there is no separate greeter session, the user
# session comes up on :0, and the unit sat in start-pre retrying 274 times over
# four hours waiting for an X1 that was never going to appear. The screen just
# stayed empty, which looks exactly like a crash and is not one.
#
# So: discover the display instead of assuming it. Try every socket X.org has
# actually created and keep the first one we can authenticate to.
set -u

XAUTH_CANDIDATES=(
  /run/user/1000/gdm/Xauthority   # GDM-managed session (autologin or greeter)
  "$HOME/.Xauthority"             # plain startx / non-GDM
)

find_display() {
  local sock dpy xauth
  for sock in /tmp/.X11-unix/X*; do
    [ -S "$sock" ] || continue
    dpy=":${sock##*/X}"
    for xauth in "${XAUTH_CANDIDATES[@]}"; do
      [ -r "$xauth" ] || continue
      # xdpyinfo is the real test: a socket can exist while the server behind it
      # refuses us (wrong cookie, or a different user's session on this seat).
      if DISPLAY="$dpy" XAUTHORITY="$xauth" xdpyinfo >/dev/null 2>&1; then
        echo "$dpy $xauth"
        return 0
      fi
    done
  done
  return 1
}

# Short probe, not a long wait: if nobody is logged in this cannot succeed no
# matter how long we sit here, and Restart=always in the unit is the better place
# to do the waiting. Exceeding TimeoutStartSec here just looks like a unit bug.
found=""
for _ in $(seq 1 10); do
  if found="$(find_display)"; then break; fi
  sleep 2
done

if [ -z "$found" ]; then
  echo "no X display we can authenticate to on this seat." >&2
  echo "sockets present: $(ls /tmp/.X11-unix/ 2>/dev/null | tr '\n' ' ')" >&2
  echo "is anyone logged in, and is autologin still enabled?" >&2
  exit 1
fi

export DISPLAY="${found% *}"
export XAUTHORITY="${found#* }"
echo "sim: using DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"

# Screen blanking is fatal here, not merely ugly: when the compositor stops
# presenting frames Godot throttles its main loop and the IPC server stops
# answering the drone. That killed a good 7-lap flight once already. These reset
# on every boot, unlike the gsettings, so redo them on every start.
xset s off || true
xset s noblank || true
xset -dpms || true
xset dpms force on || true

exec /home/<user>/godot/godot \
  --path /home/<user>/eco/drone/sim/godot \
  --resolution 3840x2160 --fullscreen \
  -- --fleet= --env=manhattan --chase=fig8 --ipc-port=9978 --seed=0

# Lab deployment units

The Manhattan figure-8 demo runs on two boxes, and each half survives a reboot
on its own. The unit files here are the ones actually deployed and running, not
sketches — copy them verbatim.

Host names, IPs and account names below are this lab's; substitute your own.

## Sim host (a display-attached box, `thor` here)

`coybot-sim.service` is a **user** unit, because it renders to a real monitor
and so needs the seat's X session, DISPLAY and Xauthority — none of which exist
before someone is logged in. A system unit would have to invent a virtual
display, and then nothing appears on the monitor at all.

It runs `run-sim.sh`, which **discovers the display** rather than being told it.
Do not put `DISPLAY=` back in the unit. The number is not stable: with a manual
login on top of the GDM greeter the greeter holds `:0` and the real session gets
`:1`, but under autologin there is no greeter and the session lands on `:0`.
Hardcoding `:1` cost four hours of blank screen and 274 silent retries the first
time autologin was enabled — the unit was starting fine, it was waiting for a
socket that was never going to appear. The script tries every socket X.org has
actually created and keeps the first one `xdpyinfo` can authenticate to.

    scp run-sim.sh <host>:eco/drone/sim/deploy/
    ssh <host> chmod +x eco/drone/sim/deploy/run-sim.sh
    scp coybot-sim.service <host>:.config/systemd/user/
    ssh <host> systemctl --user daemon-reload
    ssh <host> systemctl --user enable --now coybot-sim
    ssh <host> sudo loginctl enable-linger <user>   # user manager starts at boot

`enable-linger` is the part that is easy to miss: without it the systemd --user
instance does not exist until someone logs in, so the unit is not merely
inactive, it is unreachable.

Two things this unit disables, both of which have bitten this demo:

- **Screen blanking.** When the compositor stops presenting frames Godot
  throttles its main loop and the IPC server stops answering the drone. That
  killed a good 7-lap flight. `xset`/DPMS reset every boot, so `run-sim.sh`
  redoes them on each start; the GNOME-side settings persist and are worth
  setting too:
  `gsettings set org.gnome.desktop.session idle-delay 0` and
  `gsettings set org.gnome.desktop.screensaver lock-enabled false`.
- **Waiting on `graphical-session.target`.** Do not. On this box GNOME does not
  drive the systemd --user targets, so that target reads inactive even with a
  live desktop, and a unit hung off it is enabled, never starts, and leaves no
  journal entries at all. The unit probes for the X socket instead.

If the box boots to a GDM login screen and nobody logs in, there is no X session
and the sim cannot run; `Restart=always` sits retrying and it comes up by itself
the moment someone logs in. GDM autologin closes that gap at the cost of booting
straight to an unlocked desktop — a decision for the machine's owner.

## Drone host (a Jetson Orin Nano, `thorin` here)

`coybot-avoidance.service` is a plain system unit: the obstacle-avoidance
guidance is headless, talks to the sim over TCP, and has no display needs.

    scp coybot-avoidance.service <host>:/tmp/
    ssh <host> sudo cp /tmp/coybot-avoidance.service /etc/systemd/system/
    ssh <host> sudo systemctl enable --now coybot-avoidance

It reconnects and re-spawns the aircraft by itself, so restarting the sim host
does not need anything done here. `--host` in the unit points at the sim host.

**Disable it when you are not demoing.** With `Restart=always` and no mission it
will spawn an aircraft and fly it in a straight line out over the water; that
has happened twice. `systemctl disable --now coybot-avoidance`.

## Reboot forensics (`thor-heartbeat.*`)

The sim host restarts occasionally with no cause recorded: pstore is empty (so
not a kernel panic — ramoops is loaded and would have caught it), and the
previous boot's journal is unreadable because the clock starts at 1969 with no
working RTC. An abrupt power loss writes nothing at all, by definition.

So the state is written continuously instead, and after a restart the last line
is a snapshot from at most 30 s before it died.

    scp thor-heartbeat.sh <host>:/tmp/ && scp thor-heartbeat.{service,timer} <host>:/tmp/
    ssh <host> 'sudo install -m755 /tmp/thor-heartbeat.sh /usr/local/bin/ \
      && sudo cp /tmp/thor-heartbeat.{service,timer} /etc/systemd/system/ \
      && sudo systemctl enable --now thor-heartbeat.timer'

Read it with `tail /var/log/thor-heartbeat.log`. Temperatures climbing or a
throttle flag set means thermal. A last line that looks completely healthy
points at power instead of anything the OS did.

**What it found, over 5 days and 27 restarts on this box: power, not software.**
Every last-line-before-reset was healthy — 34-41 C (the sim itself runs the box
to 50 C), load under 0.8, 124 of 128 GB free, no throttle flag. `last -x` shows
no clean `shutdown` record since the box was provisioned, so nothing in software
was asking to reboot, and pstore is empty, so it was not a panic.

The one software cause with this exact signature is the systemd hardware
watchdog: `/etc/systemd/system.conf.d/watchdog.conf` sets
`RuntimeWatchdogSec=120`, so if PID 1 cannot pet `/dev/watchdog0` for 120 s the
SoC hard-resets leaving no log and no pstore entry. This heartbeat rules that
out by construction, because it *is* a systemd timer: across 8,284 samples the
largest gap was 35.0 s — the timer's own AccuracySec jitter — and samples landed
on schedule right up to the last one before every single reset. systemd was
never stalled anywhere near 120 s, so the watchdog cannot have fired.

That leaves the supply. A UPS both fixes it and proves it.

Also worth doing on a box with no working RTC, so the next journal is readable:
`sudo mkdir -p /etc/systemd/journald.conf.d` with `Storage=persistent`.

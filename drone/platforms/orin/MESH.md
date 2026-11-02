# Team link without an access point (802.11s mesh / IBSS)

`drone/common/team_link.py` shares position, task claims and sightings between
vehicles. On a shared LAN it uses IPv4 UDP broadcast, which needs an access
point. With no AP and no cloud, run it over a direct radio link between the
vehicles instead:

```
 vehicle A                     vehicle B
 team_link ── ipv6_multicast ── team_link        ff02::7e4d:5760, UDP 5760, hop limit 1
 mesh0/wlan1 ~~ 802.11s mesh (SAE) or IBSS (WPA2-PSK) ~~ mesh0/wlan1
```

No DHCP or address plan is needed: the kernel gives the interface an `fe80::`
link-local address as soon as it is up, and the link joins a link-local
multicast group on that interface only.

There are two layers of protection, and they do different jobs:

- **Link layer** (SAE/WPA2 passphrase, `mesh_setup.sh`) keeps outsiders off
  the radio link and encrypts the traffic.
- **team_link HMAC** (team key file) authenticates every frame and rejects
  replays, so a forged `TASK_CLAIM` is dropped even on an open or compromised
  link. It does not encrypt anything.

## 1. Radio

Use a **second Wi-Fi radio** for the mesh, for example a USB adapter, and
leave the onboard one to `coybot-network-manager` (hotspot and setup). That
service flips the first Wi-Fi interface it finds between client and AP mode,
so it will fight a mesh on that same interface. Pin it to the onboard radio
with `interface:` in `network_manager.yaml`. `mesh_setup.sh` will not run
while the service is active unless you pass `--force`.

Check what the radio can do. Don't assume. JetPack kernels and vendor
drivers often lack mesh support:

```bash
iw phy "$(cat /sys/class/net/wlan1/phy80211/name)" info | sed -n '/Supported interface modes/,/^\t[A-Z]/p'
```

You want `* mesh point`. If you only see `* IBSS`, the script falls back to
IBSS. Secured IBSS (IBSS-RSN) also depends on the driver. If
`wpa_supplicant` exits at startup, check `/run/coybot-mesh/<iface>.log`.

## 2. Bring the link up

Every vehicle in the team needs the same mesh ID, passphrase, channel and HT
mode:

```bash
export MESH_ID=coybot-team MESH_PASSPHRASE='…8-63 chars…' MESH_CHANNEL=6 MESH_REGDOM=US
sudo -E ./mesh_setup.sh wlan1 --dry-run   # review the commands first
sudo -E ./mesh_setup.sh wlan1             # idempotent: re-running with the same settings is a no-op
sudo -E ./mesh_setup.sh wlan1 --status    # mode, link-local address, neighbours + signal
sudo -E ./mesh_setup.sh wlan1 --down      # back to managed mode, handed back to NetworkManager
```

- The channel is fixed. On 5 GHz, use a non-DFS channel (36-48 or 149-165 in
  the US). DFS channels need radar detection, which mesh/IBSS does not do.
- The script never prints the passphrase. It writes the passphrase only to
  `/run/coybot-mesh/<iface>.conf` (root, 0600, tmpfs), so it is gone after a
  reboot. Re-run the script at boot, for example from a oneshot systemd unit
  that has the env vars in an `EnvironmentFile=` with mode 0600.
- `MESH_ALLOW_OPEN=1` without a passphrase gives an unencrypted link. Use it
  only on the bench.
- `MESH_IPV4=192.0.2.11/24` (a different address on each vehicle) also lets the
  older `udp_broadcast` transport run over the mesh.

## 3. Team key

Generate the key once and copy the same file to every vehicle over a trusted
channel. Never commit it.

```bash
python3 -c "import secrets; print(1, secrets.token_hex(32))" > ~/drone-api/certs/team.key
chmod 600 ~/drone-api/certs/team.key
```

The file holds one `<key_id> <hex secret>` per line. The first line signs and
every line verifies. To rotate the key:

1. Append the new key on every vehicle. It is now accepted.
2. Move it to the first line. It now signs.
3. Delete the old line.

## 4. config.yaml

```yaml
team_link:
  transport: ipv6_multicast     # or udp_broadcast (needs an AP or MESH_IPV4)
  interface: wlan1
  port: 5760
  key_file: certs/team.key      # relative to the install dir
  epoch_file: team_link.epoch   # keeps session ids increasing across reboots
  max_clock_skew_s: 30          # null = no timestamp window (clockless bench)
  rssi: iw                      # per-peer signal from `iw station dump`; omit for None
  vclass: quad
  # insecure_allow_unsigned: true   # bench only: no key, accepts unsigned/legacy frames
```

```python
link = TeamLink.from_config(drone_id, cfg["team_link"], base_dir=INSTALL_DIR)
link.start()
```

If the interface is not up yet, `start()` does not fail. The link keeps
retrying every 5 s, so starting it before `mesh_setup.sh` is fine.

## 5. Verify

`link.counters()` shows what was dropped and why. `link.link_stats()` shows
per-peer age, loss and RSSI. The usual failures look like this:

| Symptom | Counter | Cause |
|---|---|---|
| teammate never appears | `drop_bad_mac` / `drop_unknown_key` | different key file or key id |
| teammate never appears | `drop_stale_ts` (+ a "check time sync" warning) | vehicle clocks more than `max_clock_skew_s` apart |
| teammate vanishes after its reboot | `drop_stale_epoch` | its clock went backwards and it has no `epoch_file` |
| nothing at all | `rx_frames` stays 0 | link down: run `mesh_setup.sh --status`, check the channel and mesh ID |

## Limits: what this does not protect against

- **Jamming.** The link uses one fixed channel with no frequency hopping. The
  crypto cannot stop a jammer. A rising `loss_recent` and falling RSSI are how
  the allocator finds out.
- **A stolen key file.** Anyone with the key can impersonate any vehicle. There
  are no per-vehicle keys, so a lost aircraft means rotating the key for the
  whole team.
- **Traffic analysis.** Frames go out in the clear at the team_link layer.
  Hiding contents needs the link-layer passphrase.
- **Replays to a receiver that just booted.** That receiver has no replay state
  yet. The timestamp window bounds how old a replayed frame can be, so run
  vehicles with synced clocks (GPS or chrony). If `max_clock_skew_s` is null,
  that bound is gone.

"""The camera-split files agree with each other, and none names a /dev/videoN.

On the quadcopter the loopback numbers lived in four places (the module line,
the ffmpeg unit, the drone's CAMERA_DEVICE, the watchdog) and all four said
video10/video11. A RealSense enumerated onto those numbers one boot, and every
photo failed with "could not open V4L2 device /dev/video11". The numbers are
now pinned in one file and everything else uses the /dev/eco-* names that udev
derives from it. These tests keep it that way: change a name in one place and
they fail until you change it in all of them.

Run: cd eco && python3 -m pytest drone/common/tests/test_camera_split_config.py -v
"""

import re
from pathlib import Path

ORIN = Path(__file__).resolve().parents[2] / "platforms" / "orin"
SPLIT = ORIN / "camera-split"


def _text(name):
    return (SPLIT / name).read_text()


def _live_lines(name):
    return [l for l in _text(name).splitlines() if l.strip() and not l.lstrip().startswith("#")]


def _module_options():
    line = next(l for l in _live_lines("eco-v4l2loopback.conf") if l.startswith("options v4l2loopback"))
    opts = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', line))
    return {k: v.strip('"').split(",") for k, v in opts.items()}


def test_the_module_pins_one_number_and_one_name_per_node():
    opts = _module_options()
    assert int(opts["devices"][0]) == len(opts["video_nr"]) == len(opts["card_label"]) \
        == len(opts["exclusive_caps"])
    assert all(c == "1" for c in opts["exclusive_caps"])


def test_the_loopbacks_are_numbered_clear_of_every_camera():
    # The RealSense cameras take video0-11 and the Jetson's own nodes sit below.
    assert min(int(n) for n in _module_options()["video_nr"]) >= 20


def test_every_label_has_a_udev_name_and_no_name_is_unexplained():
    labels = set(_module_options()["card_label"])
    rules = _text("60-eco-camera.rules")
    assert set(re.findall(r'ATTR\{name\}=="([^"]+)"', rules)) == labels
    assert set(re.findall(r'SYMLINK\+="([^"]+)"', rules)) == labels


def test_cam_split_writes_exactly_the_named_nodes_from_the_stable_camera_name():
    unit = _text("cam-split.service")
    start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    assert "-i /dev/d435i-color" in start
    outputs = set(re.findall(r"-f v4l2 (/dev/\S+)", start))
    assert outputs == {f"/dev/{n}" for n in _module_options()["card_label"]}


def test_cam_split_waits_for_every_name_it_uses():
    wait = next(l for l in _text("cam-split.service").splitlines()
                if l.startswith("ExecStartPre=/bin/bash"))
    for name in _module_options()["card_label"] + ["d435i-color"]:
        assert f"[ -e /dev/{name} ]" in wait


def test_the_camera_name_comes_from_the_d435i_usb_identity():
    rule = _text("99-d435i-color.rules")
    assert 'idVendor}=="8086"' in rule and 'idProduct}=="0b3a"' in rule
    assert 'SYMLINK+="d435i-color"' in rule


def test_the_watchdog_has_a_node_to_itself():
    device = re.search(r'^DEVICE = "([^"]+)"', _text("cam-split-watchdog.py"), re.M).group(1)
    assert device in {f"/dev/{n}" for n in _module_options()["card_label"]}
    # The drone reads the colour node; the watchdog sharing it dropped frames.
    assert device != _drone_camera_device()
    assert device != "/dev/eco-stream"


def test_the_drone_reads_a_named_loopback_that_cam_split_writes():
    assert _drone_camera_device() in {f"/dev/{n}" for n in _module_options()["card_label"]}


def _drone_camera_device():
    install = (ORIN / "install.sh").read_text()
    return re.search(r'Environment=CAMERA_DEVICE=(/dev/\S+?)"', install).group(1)


def test_nothing_names_a_video_number():
    for name in ("cam-split.service", "cam-split-watchdog.service", "cam-split-watchdog.timer",
                 "cam-split-watchdog.py", "60-eco-camera.rules", "99-d435i-color.rules",
                 "eco-v4l2loopback.modules"):
        assert not any(re.search(r"/dev/video\d", l) for l in _live_lines(name)), name
    install = (ORIN / "install.sh").read_text()
    assert not re.search(r"CAMERA_DEVICE=/dev/video", install)


def test_the_watchdog_runs_under_the_venv_that_has_opencv():
    unit = _text("cam-split-watchdog.service")
    assert "ExecStart=__INSTALL_DIR__/venv/bin/python /usr/local/bin/cam-split-watchdog.py" in unit


def test_the_module_loads_at_boot():
    assert _live_lines("eco-v4l2loopback.modules") == ["v4l2loopback"]

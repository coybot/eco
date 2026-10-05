"""An aircraft must know on the ground whether it can run its VLM.

The quadcopter had both GGUFs downloaded and no llama_cpp in its venv. The
daemon reported vlm=True because the weights file existed, the cloud planned a
five-phase perception mission on the strength of it, and the aircraft failed at
phase 3, airborne, with "VLM not available (Qwen3-VL not loaded)" — a message
about the model when the model was fine.

The projector test pins the second bug waiting behind the first: the mmproj on
that aircraft declares `qwen2.5vl_merger`, and the loader drove it through the
LLaVA-1.6 handler regardless.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vlm  # noqa: E402


def _gguf(path: Path, kv: dict) -> Path:
    """A header-only GGUF carrying the given metadata and no tensors."""
    out = bytearray(b"GGUF")
    out += struct.pack("<I", 3)          # version
    out += struct.pack("<Q", 0)          # tensor count
    out += struct.pack("<Q", len(kv))
    for key, value in kv.items():
        kb = key.encode()
        out += struct.pack("<Q", len(kb)) + kb
        if isinstance(value, bool):
            out += struct.pack("<I", 7) + struct.pack("<B", value)
        elif isinstance(value, int):
            out += struct.pack("<I", 4) + struct.pack("<I", value)
        elif isinstance(value, list):     # array of u32
            out += struct.pack("<I", 9) + struct.pack("<I", 4)
            out += struct.pack("<Q", len(value))
            for v in value:
                out += struct.pack("<I", v)
        else:
            vb = str(value).encode()
            out += struct.pack("<I", 8) + struct.pack("<Q", len(vb)) + vb
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def weights(tmp_path):
    model = _gguf(tmp_path / "vlm.gguf", {"general.architecture": "qwen2vl"})
    mmproj = _gguf(tmp_path / "vlm_mmproj.gguf", {
        "general.architecture": "clip",
        "clip.has_vision_encoder": True,
        "clip.vision.block_count": 32,
        "clip.vision.image_mean": [1, 2, 3],
        "clip.projector_type": "qwen2.5vl_merger",
    })
    return model, mmproj


@pytest.fixture
def no_remote(monkeypatch):
    monkeypatch.delenv("VLM_BASE_URL", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)


def _hide_llama_cpp(monkeypatch):
    # None in sys.modules makes `import llama_cpp` raise ImportError, whether
    # or not it is installed on the machine running the tests.
    monkeypatch.setitem(sys.modules, "llama_cpp", None)


def test_weights_without_runtime_is_not_a_vlm(weights, no_remote, monkeypatch):
    _hide_llama_cpp(monkeypatch)
    ok, reason = vlm.check_vlm_runtime(*weights)
    assert not ok
    assert "llama-cpp-python" in reason


def test_missing_vision_encoder_is_not_a_vlm(weights, no_remote, tmp_path):
    model, _ = weights
    ok, reason = vlm.check_vlm_runtime(model, tmp_path / "absent.gguf")
    assert not ok
    assert "vision encoder" in reason


def test_missing_weights_is_not_a_vlm(no_remote, tmp_path):
    ok, reason = vlm.check_vlm_runtime(tmp_path / "a.gguf", tmp_path / "b.gguf")
    assert not ok
    assert "weights missing" in reason


def test_endpoint_serving_the_model_needs_no_local_weights(monkeypatch, tmp_path):
    _hide_llama_cpp(monkeypatch)
    monkeypatch.setenv("VLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("VLM_MODEL", "qwen3.5:2b-q4_K_M")
    monkeypatch.setattr(vlm, "_served_models", lambda url: ({"qwen3.5:2b-q4_K_M"}, ""))
    ok, _ = vlm.check_vlm_runtime(tmp_path / "a.gguf", tmp_path / "b.gguf")
    assert ok


def test_endpoint_that_is_down_is_not_a_vlm(monkeypatch, tmp_path):
    monkeypatch.setenv("VLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("VLM_MODEL", "qwen3.5:2b-q4_K_M")
    monkeypatch.setattr(vlm, "_served_models", lambda url: (None, "Connection refused"))
    ok, reason = vlm.check_vlm_runtime(tmp_path / "a.gguf", tmp_path / "b.gguf")
    assert not ok
    assert "unreachable" in reason


def test_endpoint_without_the_model_pulled_is_not_a_vlm(monkeypatch, tmp_path):
    monkeypatch.setenv("VLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("VLM_MODEL", "qwen3.5:2b-q4_K_M")
    monkeypatch.setattr(vlm, "_served_models", lambda url: ({"qwen3.5:0.8b"}, ""))
    ok, reason = vlm.check_vlm_runtime(tmp_path / "a.gguf", tmp_path / "b.gguf")
    assert not ok
    assert "does not serve" in reason


def test_real_refused_connection_is_reported_not_raised(monkeypatch):
    # Nothing listens on port 9 locally; exercise the real urllib path once.
    served, err = vlm._served_models("http://127.0.0.1:9/v1", attempts=1)
    assert served is None and err


def test_remote_endpoint_without_model_name_is_not_a_vlm(monkeypatch, tmp_path):
    monkeypatch.setenv("VLM_BASE_URL", "http://example.invalid/v1")
    monkeypatch.delenv("VLM_MODEL", raising=False)
    ok, reason = vlm.check_vlm_runtime(tmp_path / "a.gguf", tmp_path / "b.gguf")
    assert not ok
    assert "VLM_MODEL" in reason


def test_projector_type_is_read_past_other_value_types(weights):
    # The key sits after a bool, an int and an array; reaching it means every
    # preceding value was skipped by its real width.
    _, mmproj = weights
    meta = vlm._read_gguf_metadata(mmproj, {"clip.projector_type"})
    assert meta == {"clip.projector_type": "qwen2.5vl_merger"}


def test_qwen25_projector_gets_the_qwen25_handler():
    assert vlm._PROJECTOR_HANDLERS["qwen2.5vl_merger"] == "Qwen25VLChatHandler"


def test_non_gguf_is_rejected(tmp_path):
    bad = tmp_path / "x.gguf"
    bad.write_bytes(b"NOPE" + b"\0" * 32)
    with pytest.raises(ValueError):
        vlm._read_gguf_metadata(bad, {"clip.projector_type"})


class _FakeLlamaCpp:
    pass


def test_weights_that_do_not_fit_are_not_a_vlm(weights, no_remote, monkeypatch):
    # The quadcopter's case once llama_cpp was installed: loads, then OOM.
    monkeypatch.setitem(sys.modules, "llama_cpp", _FakeLlamaCpp())
    monkeypatch.setattr(vlm, "_mem_available_mb", lambda: 100)
    ok, reason = vlm.check_vlm_runtime(*weights)
    assert not ok
    assert "not enough memory" in reason


def test_weights_that_fit_are_a_vlm(weights, no_remote, monkeypatch):
    monkeypatch.setitem(sys.modules, "llama_cpp", _FakeLlamaCpp())
    monkeypatch.setattr(vlm, "_mem_available_mb", lambda: 64_000)
    ok, _ = vlm.check_vlm_runtime(*weights)
    assert ok


def test_unknown_memory_does_not_block(weights, no_remote, monkeypatch):
    monkeypatch.setitem(sys.modules, "llama_cpp", _FakeLlamaCpp())
    monkeypatch.setattr(vlm, "_mem_available_mb", lambda: None)
    ok, _ = vlm.check_vlm_runtime(*weights)
    assert ok


def test_qwen3_projector_gets_the_generic_mtmd_handler():
    # What setup_models.py installs on every variant today.
    assert vlm._PROJECTOR_HANDLERS["qwen3vl_merger"] == "MTMDChatHandler"

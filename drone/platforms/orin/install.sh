#!/bin/bash
# install.sh - Set up the drone daemon on NVIDIA Orin (Nano, NX, AGX)
#
# Usage:
#   ./install.sh                                    # Local install
#   ./install.sh --start                            # Local install + start services
#   ./install.sh --remote user@host                 # Remote install via SSH
#   ./install.sh --remote user@host --password pass # Remote install with password
#   ./install.sh --remote user@host --start         # Remote install + start

set -e

# =========================================
# Argument Parsing
# =========================================

REMOTE_HOST=""
REMOTE_PASSWORD=""
START_AFTER_INSTALL=false
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --remote)
            REMOTE_HOST="$2"
            shift 2
            ;;
        --password)
            REMOTE_PASSWORD="$2"
            shift 2
            ;;
        --start)
            START_AFTER_INSTALL=true
            shift
            ;;
        --help|-h)
            SHOW_HELP=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            SHOW_HELP=true
            shift
            ;;
    esac
done

if [ "$SHOW_HELP" = true ]; then
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --remote USER@HOST   Install on remote Orin via SSH"
    echo "  --password PASS      SSH password (for --remote)"
    echo "  --start              Start services after installation"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                                      # Local install"
    echo "  $0 --start                              # Local install + start"
    echo "  $0 --remote jetson@192.168.1.50         # Remote install (key auth)"
    echo "  $0 --remote jetson@192.168.1.50 --password mypass --start"
    exit 0
fi

# =========================================
# Remote Installation Handler
# =========================================

if [ -n "$REMOTE_HOST" ]; then
    echo "========================================="
    echo "Remote Installation to: $REMOTE_HOST"
    echo "========================================="
    echo ""
    
    # Check for sshpass if password is provided
    if [ -n "$REMOTE_PASSWORD" ]; then
        if ! command -v sshpass &> /dev/null; then
            echo "Installing sshpass for password authentication..."
            if command -v apt-get &> /dev/null; then
                sudo apt-get install -y sshpass
            elif command -v brew &> /dev/null; then
                brew install hudochenkov/sshpass/sshpass
            else
                echo "Error: sshpass not found. Install it or use SSH key authentication."
                exit 1
            fi
        fi
        SSH_PREFIX=(sshpass -p "$REMOTE_PASSWORD")
        SSH_OPTS=(-o StrictHostKeyChecking=no)
    else
        SSH_PREFIX=()
        SSH_OPTS=()
    fi
    
    # Get script directory for copying files
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    DRONE_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
    
    echo "Copying drone software to remote host..."
    
    # Create remote directory
    "${SSH_PREFIX[@]}" ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" "mkdir -p ~/drone-install"
    
    # Copy the entire drone directory (including wheels/ for pre-built packages)
    "${SSH_PREFIX[@]}" scp "${SSH_OPTS[@]}" -r "$DRONE_DIR" "$REMOTE_HOST:~/drone-install/"
    
    echo "Running installation on remote host..."
    
    # Build remote command
    REMOTE_CMD="cd ~/drone-install/drone/platforms/orin && bash install.sh"
    if [ "$START_AFTER_INSTALL" = true ]; then
        REMOTE_CMD="$REMOTE_CMD --start"
    fi
    
    # Execute on remote
    "${SSH_PREFIX[@]}" ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" "$REMOTE_CMD"
    
    echo ""
    echo "========================================="
    echo "✅ Remote installation complete!"
    echo "========================================="
    exit 0
fi

# =========================================
# Local Installation (runs on the Orin)
# =========================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DRONE_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
COMMON_DIR="$DRONE_DIR/common"
MODELS_DIR="$DRONE_DIR/models"
INSTALL_DIR="$HOME/drone-api"

# Detect Orin variant (Nano, NX, AGX 32GB, AGX 64GB)
detect_orin_variant() {
    if [ -f /proc/device-tree/model ]; then
        MODEL=$(cat /proc/device-tree/model | tr '[:upper:]' '[:lower:]')
        if [[ "$MODEL" == *"orin nano"* ]]; then
            echo "nano"
            return
        elif [[ "$MODEL" == *"orin nx"* ]]; then
            echo "nx"
            return
        elif [[ "$MODEL" == *"agx orin"* ]]; then
            # AGX Orin - check memory for 32GB vs 64GB variant
            MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
            MEM_GB=$((MEM_KB / 1024 / 1024))
            if [ "$MEM_GB" -ge 50 ]; then
                echo "agx64"
            else
                echo "agx32"
            fi
            return
        fi
    fi
    
    # Fall back to memory check
    MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    MEM_GB=$((MEM_KB / 1024 / 1024))
    if [ "$MEM_GB" -lt 12 ]; then
        echo "nano"
    elif [ "$MEM_GB" -lt 20 ]; then
        echo "nx"
    elif [ "$MEM_GB" -lt 50 ]; then
        echo "agx32"
    else
        echo "agx64"
    fi
}

ORIN_VARIANT=$(detect_orin_variant)
IS_AGX=false
if [[ "$ORIN_VARIANT" == agx* ]]; then
    IS_AGX=true
fi

echo "========================================="
echo "Installing Drone API Daemon"
echo "========================================="
echo "Orin Variant: $ORIN_VARIANT"
echo "Source: $DRONE_DIR"
echo "Install to: $INSTALL_DIR"
echo "Start after install: $START_AFTER_INSTALL"
echo ""

# Create install directory
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/models"
mkdir -p "$INSTALL_DIR/certs"

# Copy common files
echo "Copying common files..."
# Copy every module, not a hand-maintained list. The list drifted: nine modules
# that daemon.py and reasoning_loop.py import at module scope were never
# installed, so on a real drone the mission path died at dispatch with
# ModuleNotFoundError and the app just showed the mission stuck in progress.
# All of drone/common is 772K; there is nothing to be saved by picking.
cp "$COMMON_DIR"/*.py "$INSTALL_DIR/"

# Track A local prompt pipeline (SSH / run_prompt.py — same tree as cloud daemon)
echo "Copying Track A / local prompt modules..."
for _f in run_prompt.py grounding.py spatial_memory.py reactive_planner.py target_selector.py where.py; do
    cp "$COMMON_DIR/$_f" "$INSTALL_DIR/" 2>/dev/null || echo "  (optional) $_f not found"
done

# Copy certs directory (claim certs for fleet provisioning)
if [ -d "$COMMON_DIR/certs" ]; then
    cp -r "$COMMON_DIR/certs/"* "$INSTALL_DIR/certs/" 2>/dev/null || true
fi

# Copy mission autonomy modules
echo "Copying mission autonomy modules..."

# Copy VLM and Nav2 bridge (mission autonomy on Nano and AGX)
echo "Copying VLM and Nav2 modules (mission autonomy)..."

# Copy models setup script
cp "$MODELS_DIR/setup_models.py" "$INSTALL_DIR/models/" 2>/dev/null || true

# Copy or create config
if [ -f "$COMMON_DIR/config.yaml" ]; then
    cp "$COMMON_DIR/config.yaml" "$INSTALL_DIR/"
elif [ -f "$COMMON_DIR/config.yaml.example" ]; then
    cp "$COMMON_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
    echo "⚠️  Created config.yaml from template - please edit it!"
fi

# Create config directory
sudo mkdir -p /etc/astral
sudo mkdir -p /etc/astral/certs
if [ -f "$COMMON_DIR/network_manager.yaml.example" ]; then
    sudo cp "$COMMON_DIR/network_manager.yaml.example" /etc/astral/network_manager.yaml
fi

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install pymavlink pyserial awsiotsdk pyyaml

# Install AI dependencies
echo "Installing AI dependencies..."
# onnxruntime-gpu is not on PyPI for Jetson aarch64; try NVIDIA's pre-built wheel first
if ! pip install onnxruntime-gpu 2>/dev/null; then
    echo "  onnxruntime-gpu not on PyPI for aarch64, trying NVIDIA Jetson wheel..."
    pip install onnxruntime-gpu --extra-index-url https://elinux.org/jetson_zoo 2>/dev/null \
        || pip install onnxruntime 2>/dev/null \
        || echo "  ⚠️  onnxruntime not installed - perception may fall back to CPU or be unavailable"
fi
pip install opencv-python-headless numpy ultralytics huggingface_hub

# Grounding DINO (run_prompt.py) — torch wheel is platform-specific; install may skip on failure
echo "Installing optional HF / Track A dependencies..."
pip install transformers accelerate safetensors 2>/dev/null \
    || echo "  ⚠️  transformers stack not installed — add torch+transformers per Jetson docs for run_prompt.py"

# Install llama-cpp-python with CUDA support
echo "Installing llama-cpp-python with CUDA support..."
# Ensure nvcc is in PATH (Jetson installs CUDA toolkit to /usr/local/cuda but doesn't always add to PATH)
export PATH="/usr/local/cuda/bin:$PATH"
export CUDACXX="/usr/local/cuda/bin/nvcc"
export CMAKE_CUDA_COMPILER="/usr/local/cuda/bin/nvcc"

# Check for pre-built wheel first (saves ~45 min of compilation)
WHEEL_DIR="$DRONE_DIR/wheels"
LLAMA_WHEEL=$(find "$WHEEL_DIR" -name "llama_cpp_python-*-linux_aarch64.whl" 2>/dev/null | head -1)

if [ -n "$LLAMA_WHEEL" ]; then
    echo "  Found pre-built wheel: $(basename $LLAMA_WHEEL)"
    pip install "$LLAMA_WHEEL" --force-reinstall
else
    echo "  No pre-built wheel found in $WHEEL_DIR, building from source (~30-45 min)..."
    CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir || {
        echo "  ⚠️  CUDA build failed, trying CPU-only llama-cpp-python..."
        pip install llama-cpp-python --force-reinstall --no-cache-dir || echo "  ⚠️  llama-cpp-python install failed - VLM will be unavailable"
    }
    # Save the wheel for future installs
    echo "  Saving wheel for future installs..."
    mkdir -p "$WHEEL_DIR"
    CMAKE_ARGS="-DGGML_CUDA=on" pip wheel llama-cpp-python==$(pip show llama-cpp-python 2>/dev/null | grep Version | cut -d' ' -f2) \
        --wheel-dir "$WHEEL_DIR" --no-deps 2>/dev/null || true
fi

# For AGX, install ROS 2 and Nav2 dependencies
if [ "$IS_AGX" = true ]; then
    echo ""
    echo "========================================="
    echo "Installing ROS 2 and Nav2 for AGX"
    echo "========================================="
    
    # Check if ROS 2 is installed
    if [ ! -d "/opt/ros/humble" ]; then
        echo "ROS 2 Humble not found. Please install it first:"
        echo "  https://nvidia-isaac-ros.github.io/getting_started/isaac_ros_buildfarm_cdn.html"
        echo ""
        echo "After installing ROS 2, re-run this script."
        echo ""
    else
        echo "ROS 2 Humble found at /opt/ros/humble"
        source /opt/ros/humble/setup.bash
        
        # Install Nav2 packages
        sudo apt-get install -y \
            ros-humble-navigation2 \
            ros-humble-nav2-bringup \
            ros-humble-tf2-ros \
            ros-humble-tf2-geometry-msgs
        
        echo "Nav2 installed successfully"
    fi
fi

# Install networking tools
echo "Installing networking tools..."
sudo apt-get update -y
sudo apt-get install -y network-manager hostapd dnsmasq iw openssl wget

# =========================================
# Fleet Provisioning (get device certificate)
# =========================================

echo ""
echo "========================================="
echo "Fleet Provisioning"
echo "========================================="

cd "$INSTALL_DIR"

if [ -f "$INSTALL_DIR/certs/device.pem" ]; then
    echo "✓ Device certificate already exists"
else
    if [ -f "$INSTALL_DIR/fleet_provisioning.py" ] && [ -f "$INSTALL_DIR/certs/claim-cert.pem" ]; then
        echo "Running fleet provisioning to obtain device certificate..."
        if python3 fleet_provisioning.py; then
            echo "✓ Fleet provisioning successful!"
        else
            echo "⚠️  Fleet provisioning failed."
            echo "   This may be due to network issues or missing claim certificates."
            echo "   You can retry later with: python3 $INSTALL_DIR/fleet_provisioning.py"
        fi
    else
        echo "⚠️  Fleet provisioning not available (missing claim certificates)"
        echo "   Device certificate will need to be provisioned manually or via iOS app"
    fi
fi

# =========================================
# Download AI Models
# =========================================

echo ""
echo "========================================="
echo "Setting up AI Models for Orin $ORIN_VARIANT"
echo "========================================="
echo ""

cd "$INSTALL_DIR/models"

# Download models (this may take a while on first install)
if [ -f "setup_models.py" ]; then
    echo "Downloading AI models (this may take 10-60 minutes on first install)..."
    
    case "$ORIN_VARIANT" in
        nano)
            echo "Orin Nano: Downloading YOLOv8 + small VLM for mission autonomy..."
            python setup_models.py --yolo-only
            python setup_models.py --qwen3-vl-2b
            ;;
        nx)
            echo "Orin NX: Downloading YOLOv8 + Qwen3-VL-8B (mission autonomy)..."
            echo "⚠️  Qwen3-VL-8B download is ~6GB - this may take 30+ minutes"
            python setup_models.py --yolo-only
            python setup_models.py --qwen3-vl-8b
            ;;
        agx32)
            echo "Orin AGX 32GB: Downloading YOLOv8x + Qwen3-VL-8B (mission autonomy)..."
            echo "⚠️  Qwen3-VL-8B download is ~6GB - this may take 30+ minutes"
            python setup_models.py --yolox
            python setup_models.py --qwen3-vl-8b
            ;;
        agx64)
            echo "Orin AGX 64GB: Downloading YOLOv8x + Qwen3-VL-32B (mission autonomy)..."
            echo "⚠️  Qwen3-VL-32B download is ~24GB - this may take 60+ minutes"
            python setup_models.py --yolox
            python setup_models.py --qwen3-vl-32b
            ;;
    esac
    
    # Create symlinks for the detected variant
    echo "Creating model symlinks for $ORIN_VARIANT..."
    python setup_models.py --setup-device --variant "$ORIN_VARIANT"
    
    echo "AI models ready!"
else
    echo "⚠️  setup_models.py not found - skipping model download"
    echo "   Run setup_models.py manually after installation"
fi

cd "$INSTALL_DIR"

# =========================================
# Create systemd Services
# =========================================

echo "Creating systemd services..."
SERVICE_FILE="/etc/systemd/system/drone-api.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Drone API Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/daemon.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Create network manager service (if template exists)
if [ -f "$DRONE_DIR/platforms/orin/astral-network-manager.service" ]; then
    # Substitute, do not cat: the template's __INSTALL_DIR__ has to become
    # this user's install dir. A heredoc around $(cat ...) does not re-expand
    # the file's own contents, which is how the old literal path survived.
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
        "$DRONE_DIR/platforms/orin/astral-network-manager.service" \
        | sudo tee "/etc/systemd/system/astral-network-manager.service" > /dev/null
fi

# Create local control API service (if template exists)
if [ -f "$DRONE_DIR/platforms/orin/astral-local-control.service" ]; then
    # Substitute, do not cat: the template's __INSTALL_DIR__ has to become
    # this user's install dir. A heredoc around $(cat ...) does not re-expand
    # the file's own contents, which is how the old literal path survived.
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
        "$DRONE_DIR/platforms/orin/astral-local-control.service" \
        | sudo tee "/etc/systemd/system/astral-local-control.service" > /dev/null
fi

# Reload systemd
sudo systemctl daemon-reload

# =========================================
# Start Services (if --start flag provided)
# =========================================

if [ "$START_AFTER_INSTALL" = true ]; then
    echo ""
    echo "========================================="
    echo "Starting Services"
    echo "========================================="
    
    echo "Enabling and starting drone-api..."
    sudo systemctl enable drone-api
    sudo systemctl start drone-api
    
    # Start optional services if they exist
    if [ -f "/etc/systemd/system/astral-network-manager.service" ]; then
        sudo systemctl enable astral-network-manager
        sudo systemctl start astral-network-manager
    fi
    
    if [ -f "/etc/systemd/system/astral-local-control.service" ]; then
        sudo systemctl enable astral-local-control
        sudo systemctl start astral-local-control
    fi
    
    echo ""
    echo "Checking service status..."
    sleep 2
    sudo systemctl status drone-api --no-pager || true
fi

# =========================================
# Done!
# =========================================

echo ""
echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""

# The setup passphrase is generated on-device and is the only way into a drone
# with no shell access, so print it here: this is the operator's one chance to
# write it on the airframe before the hotspot needs it.
HOTSPOT_SSID="$(sudo python3 "$INSTALL_DIR/wifi_manager.py" 2>/dev/null | sed -n 's/^Hotspot name: //p')"
HOTSPOT_PSK="$(sudo python3 "$INSTALL_DIR/wifi_manager.py" --passphrase 2>/dev/null)"
if [ -n "$HOTSPOT_SSID" ] && [ -n "$HOTSPOT_PSK" ]; then
    echo "WiFi setup network - write these on the airframe:"
    echo "  Network:    $HOTSPOT_SSID"
    echo "  Passphrase: $HOTSPOT_PSK"
    echo ""
    echo "The iOS app asks for both. The passphrase also authorizes setup, and is"
    echo "regenerated after each successful WiFi configuration."
    echo ""
fi

if [ "$START_AFTER_INSTALL" = true ]; then
    echo "Services are running!"
    echo ""
    echo "View logs:     journalctl -u drone-api -f"
    echo "Stop service:  sudo systemctl stop drone-api"
    echo "Restart:       sudo systemctl restart drone-api"
else
    echo "Next steps:"
    echo "1. Edit config: nano $INSTALL_DIR/config.yaml"
    echo "2. Start:       sudo systemctl start drone-api"
    echo "3. Enable boot: sudo systemctl enable drone-api"
    echo ""
    echo "Or run with --start flag: $0 --start"
fi
echo ""

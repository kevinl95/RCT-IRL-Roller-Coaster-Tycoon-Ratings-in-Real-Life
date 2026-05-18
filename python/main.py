"""
RCT IRL — Python side (Qualcomm MPU)

Receives IMU data from the STM32 via Bridge,
runs signal processing to extract 5 reliable ride features,
runs three Edge Impulse models for E/I/N prediction,
and serves the RCT-themed WebUI.
"""

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
import time
import math
import os

# ============================================================
# Edge Impulse model loading
# ============================================================
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")

try:
    from edge_impulse_linux.runner import ImpulseRunner

    def load_model(path):
        runner = ImpulseRunner(path)
        runner.init()
        return runner

    def run_inference(runner, features):
        """Run inference. Returns the predicted regression value."""
        result = runner.classify(features)
        return float(result['result']['classification']['value'])

    print("Edge Impulse Linux SDK loaded")

except ImportError:
    print("Edge Impulse Linux SDK not found — running in demo mode")

    def load_model(path):
        return None

    def run_inference(runner, features):
        import random
        return round(3.0 + random.uniform(0, 4), 2)


# Load all three models
models = {}
for name in ["excitement", "intensity", "nausea"]:
    eim_path = os.path.join(MODEL_DIR, f"rct_{name}.eim")
    if os.path.exists(eim_path):
        try:
            os.chmod(eim_path, 0o755)
            models[name] = load_model(eim_path)
            print(f"Loaded model: {name} from {eim_path}")
        except Exception as e:
            print(f"Failed to load {name} model: {e}")
            models[name] = None
    else:
        print(f"Model not found: {eim_path}")
        models[name] = None


# ============================================================
# IMU data collection
# ============================================================
imu_samples = []  # List of (timestamp, ax, ay, az, gx, gy, gz)
recording = False
imu_connected = False


def on_imu_data(data_str):
    """Called by Bridge when sketch sends IMU data."""
    global imu_samples
    if not recording:
        return
    try:
        parts = data_str.split(",")
        if len(parts) == 7:
            ts = int(parts[0])
            ax, ay, az = float(parts[1]), float(parts[2]), float(parts[3])
            gx, gy, gz = float(parts[4]), float(parts[5]), float(parts[6])
            imu_samples.append((ts, ax, ay, az, gx, gy, gz))
    except (ValueError, IndexError) as e:
        print(f"Bad IMU data: {data_str} — {e}")


def on_imu_status(status):
    """Called by Bridge when sketch reports IMU status."""
    global imu_connected
    imu_connected = bool(status)


# Register Bridge callbacks
Bridge.provide("imu_data", on_imu_data)
Bridge.provide("imu_status", on_imu_status)


# ============================================================
# Training data ranges for input clamping
# ============================================================
# Real coasters produce more extreme values than the RCT2 game data.
# Without clamping, the model extrapolates wildly (scores above 20).
# We clamp each input to the range seen in training so the model
# stays on the 0-10 scale.
FEATURE_RANGES = {
    "max_pos_gs":     (2.14, 6.26),
    "max_neg_gs":     (-56.0, 1.89),
    "max_lateral_gs": (0.0, 3.32),
    "total_air_time": (0.0, 13.68),
    "ride_time":      (12.0, 186.0),
}


def clamp(value, feature_name):
    """Clamp a value to the training data range for that feature."""
    lo, hi = FEATURE_RANGES[feature_name]
    return max(lo, min(hi, value))


# ============================================================
# Signal processing — extract 5 reliable features from raw IMU
# ============================================================
def extract_features(samples):
    """
    Takes a list of (ts, ax, ay, az, gx, gy, gz) tuples and extracts
    the 5 features our Edge Impulse models expect.

    We deliberately use only features the IMU can measure reliably:
      - max_pos_gs:     peak positive vertical G (direct reading)
      - max_neg_gs:     peak negative vertical G (direct reading)
      - max_lateral_gs: peak lateral G (direct reading)
      - total_air_time: seconds of near-weightlessness (threshold on direct reading)
      - ride_time:      recording duration (a clock)

    Uses gravity calibration from the first samples (user standing still)
    to determine "down" regardless of IMU orientation in the fanny pack.
    """
    if len(samples) < 20:
        print("Not enough samples for analysis")
        return None

    # =========================================================
    # Step 1: Gravity calibration from first samples (at rest)
    # =========================================================
    cal_count = min(20, len(samples) // 4)
    grav_x = sum(s[1] for s in samples[:cal_count]) / cal_count
    grav_y = sum(s[2] for s in samples[:cal_count]) / cal_count
    grav_z = sum(s[3] for s in samples[:cal_count]) / cal_count
    grav_mag = math.sqrt(grav_x**2 + grav_y**2 + grav_z**2)

    if grav_mag > 0.5:
        grav_x /= grav_mag
        grav_y /= grav_mag
        grav_z /= grav_mag
    else:
        grav_x, grav_y, grav_z = 0.0, 0.0, 1.0

    print(f"Calibrated gravity vector: ({grav_x:.3f}, {grav_y:.3f}, {grav_z:.3f})")

    # =========================================================
    # Step 2: Project all samples into vertical/lateral
    # =========================================================
    vertical_gs = []
    lateral_gs = []
    timestamps = []

    for s in samples:
        ts, ax, ay, az = s[0], s[1], s[2], s[3]
        timestamps.append(ts)

        # Vertical G = dot product with gravity direction
        vert = ax * grav_x + ay * grav_y + az * grav_z
        vertical_gs.append(vert)

        # Lateral G = perpendicular component magnitude
        perp_x = ax - vert * grav_x
        perp_y = ay - vert * grav_y
        perp_z = az - vert * grav_z
        lat = math.sqrt(perp_x**2 + perp_y**2 + perp_z**2)
        lateral_gs.append(lat)

    # =========================================================
    # Step 3: Time
    # =========================================================
    duration_ms = timestamps[-1] - timestamps[0]
    ride_time = duration_ms / 1000.0
    dt = ride_time / len(samples)

    # =========================================================
    # Step 4: Minimum ride threshold
    # =========================================================
    max_vert = max(vertical_gs)
    min_vert = min(vertical_gs)
    max_lat = max(lateral_gs)

    if max_vert < 1.5 and max_lat < 0.5 and ride_time < 30:
        print(f"No significant motion detected "
              f"(max vert G: {max_vert:.2f}, max lat G: {max_lat:.2f}, "
              f"time: {ride_time:.0f}s)")
        return None

    # =========================================================
    # Step 5: Extract the 5 reliable features
    # =========================================================

    # G-forces: direct accelerometer peaks
    max_pos_gs = max_vert
    max_neg_gs = min_vert
    max_lateral_gs = max_lat

    # Airtime: seconds where vertical G < 0.5 (near-weightlessness)
    AIRTIME_THRESHOLD = 0.5
    airtime_samples = sum(1 for v in vertical_gs if v < AIRTIME_THRESHOLD)
    total_air_time = airtime_samples * dt

    features = {
        "max_pos_gs": round(max_pos_gs, 2),
        "max_neg_gs": round(max_neg_gs, 2),
        "max_lateral_gs": round(max_lateral_gs, 2),
        "total_air_time": round(total_air_time, 2),
        "ride_time": round(ride_time),
    }

    print(f"Extracted features: {features}")
    return features


# ============================================================
# Run ML inference
# ============================================================
def predict_ratings(features):
    """
    Run the three Edge Impulse models to predict E/I/N scores.
    Clamps inputs to training data range before inference.
    """
    # Build feature vector in the order the models expect,
    # clamped to training data range
    feature_vector = [
        clamp(float(features["max_pos_gs"]), "max_pos_gs"),
        clamp(float(features["max_neg_gs"]), "max_neg_gs"),
        clamp(float(features["max_lateral_gs"]), "max_lateral_gs"),
        clamp(float(features["total_air_time"]), "total_air_time"),
        clamp(float(features["ride_time"]), "ride_time"),
    ]

    print(f"Clamped feature vector: {feature_vector}")

    ratings = {}
    for name in ["excitement", "intensity", "nausea"]:
        if models.get(name) is not None:
            try:
                score = run_inference(models[name], feature_vector)
                ratings[name] = round(max(0, min(10, score)), 2)
            except Exception as e:
                print(f"Inference error for {name}: {e}")
                ratings[name] = 5.0
        else:
            ratings[name] = run_inference(None, feature_vector)

    return ratings


# ============================================================
# WebUI setup
# ============================================================
ui = WebUI()


def api_status():
    return {"imu_connected": imu_connected, "recording": recording}


def api_start():
    global recording, imu_samples
    imu_samples = []
    recording = True
    Bridge.call("set_recording", True)
    print("Recording started")
    return {"status": "recording"}


def api_stop():
    global recording
    recording = False
    Bridge.call("set_recording", False)
    print(f"Recording stopped. {len(imu_samples)} samples collected.")

    features = extract_features(imu_samples)
    if features is None:
        return {"error": "Not enough ride data. Try recording for longer or with more motion."}

    ratings = predict_ratings(features)

    # Combine features + ratings for the WebUI
    result = {**features, **ratings}
    print(f"Results: E={ratings['excitement']}, I={ratings['intensity']}, N={ratings['nausea']}")
    return result


ui.expose_api("GET", "/api/status", api_status)
ui.expose_api("POST", "/api/start", api_start)
ui.expose_api("POST", "/api/stop", api_stop)


# ============================================================
# Main loop
# ============================================================
def loop():
    Bridge.call("get_status", 0)
    time.sleep(2)


print("RCT IRL Python side ready")
print(f"Models loaded: {[k for k, v in models.items() if v is not None]}")
print("WebUI available on port 7000")

App.run(user_loop=loop)
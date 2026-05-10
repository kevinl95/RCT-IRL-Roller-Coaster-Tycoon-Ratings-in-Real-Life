"""
RCT IRL — Python side (Qualcomm MPU)

Receives IMU data from the STM32 via Bridge,
runs signal processing to extract ride features,
runs three Edge Impulse models for E/I/N prediction,
and serves the RCT-themed WebUI.
"""

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
import time
import math
import json
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
    USE_EI_SDK = True

except ImportError:
    print("Edge Impulse Linux SDK not found — running in demo mode")
    USE_EI_SDK = False

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
# Signal processing — extract 11 features from raw IMU stream
# ============================================================
def extract_features(samples):
    """
    Takes a list of (ts, ax, ay, az, gx, gy, gz) tuples
    and extracts the 11 features our Edge Impulse models expect.

    Uses gravity calibration from the first samples (user standing still)
    to determine which direction is "down" regardless of IMU orientation.
    """
    if len(samples) < 20:
        print("Not enough samples for analysis")
        return None

    # =========================================================
    # Step 1: Gravity calibration from first samples (at rest)
    # =========================================================
    # The user hits Start while standing still in the queue,
    # so the first ~20 samples tell us which way gravity points.
    cal_count = min(20, len(samples) // 4)
    grav_x = sum(s[1] for s in samples[:cal_count]) / cal_count
    grav_y = sum(s[2] for s in samples[:cal_count]) / cal_count
    grav_z = sum(s[3] for s in samples[:cal_count]) / cal_count
    grav_mag = math.sqrt(grav_x**2 + grav_y**2 + grav_z**2)

    # Normalize gravity vector
    if grav_mag > 0.5:
        grav_x /= grav_mag
        grav_y /= grav_mag
        grav_z /= grav_mag
    else:
        # Fallback: assume Z is down
        grav_x, grav_y, grav_z = 0.0, 0.0, 1.0

    print(f"Calibrated gravity vector: ({grav_x:.3f}, {grav_y:.3f}, {grav_z:.3f})")
    print(f"Gravity magnitude at rest: {grav_mag:.3f} G")

    # =========================================================
    # Step 2: Project all samples into body frame
    # =========================================================
    # Vertical = component along gravity axis
    # Lateral  = magnitude of component perpendicular to gravity
    vertical_gs = []
    lateral_gs = []
    gyro_magnitudes = []
    timestamps = []

    for s in samples:
        ts, ax, ay, az, gx, gy, gz = s
        timestamps.append(ts)

        # Vertical G = dot product of accel with gravity direction
        # At rest this should be ~1.0 G (gravity itself)
        vert = ax * grav_x + ay * grav_y + az * grav_z
        vertical_gs.append(vert)

        # Lateral G = magnitude of the perpendicular component
        perp_x = ax - vert * grav_x
        perp_y = ay - vert * grav_y
        perp_z = az - vert * grav_z
        lat = math.sqrt(perp_x**2 + perp_y**2 + perp_z**2)
        lateral_gs.append(lat)

        # Gyro magnitude for inversion detection
        gyro_mag = math.sqrt(gx**2 + gy**2 + gz**2)
        gyro_magnitudes.append(gyro_mag)

    # =========================================================
    # Step 3: Time calculations
    # =========================================================
    duration_ms = timestamps[-1] - timestamps[0]
    ride_time = duration_ms / 1000.0  # seconds
    dt = ride_time / len(samples)  # seconds per sample

    # =========================================================
    # Step 4: Minimum ride threshold
    # =========================================================
    max_vert = max(vertical_gs)
    min_vert = min(vertical_gs)
    max_lat = max(lateral_gs)

    # At rest, vertical G is ~1.0. If the max never exceeds 1.5
    # and the ride is short, nothing interesting happened.
    if max_vert < 1.5 and max_lat < 0.5 and ride_time < 30:
        print(f"No significant motion detected "
              f"(max vert G: {max_vert:.2f}, max lat G: {max_lat:.2f}, "
              f"time: {ride_time:.0f}s)")
        return None

    # =========================================================
    # Step 5: Extract features
    # =========================================================

    # --- G-force features (directly measured) ---
    max_pos_gs = max_vert          # Max positive vertical G (pushed into seat)
    max_neg_gs = min_vert          # Max negative vertical G (airtime / weightless)
    max_lateral_gs = max_lat       # Max lateral G (turns)

    # --- Airtime detection ---
    # Airtime = periods where vertical G < 0.5 (near weightlessness)
    # At rest vertical G is ~1.0, so < 0.5 means real airtime
    AIRTIME_THRESHOLD = 0.5
    airtime_samples = sum(1 for v in vertical_gs if v < AIRTIME_THRESHOLD)
    total_air_time = airtime_samples * dt

    # --- Drop detection ---
    # A "drop" is a sustained period where vertical G drops below threshold
    DROP_THRESHOLD = 0.7
    in_drop = False
    drops = 0
    drop_durations = []
    current_drop_start = 0

    for i, v in enumerate(vertical_gs):
        if v < DROP_THRESHOLD and not in_drop:
            in_drop = True
            current_drop_start = i
            drops += 1
        elif v >= DROP_THRESHOLD and in_drop:
            in_drop = False
            drop_samples = i - current_drop_start
            drop_durations.append(drop_samples * dt)

    # --- Highest drop height ---
    # Estimate from longest free-fall duration: h = 0.5 * g * t^2
    # Convert to feet (1m = 3.281ft)
    if drop_durations:
        longest_drop = max(drop_durations)
        highest_drop_height = 0.5 * 9.81 * (longest_drop ** 2) * 3.281
    else:
        highest_drop_height = 0

    # --- Inversion detection ---
    # Use gyro magnitude — sustained high rotation rate indicates
    # going through a loop or corkscrew.
    INVERSION_RATE_THRESHOLD = 90  # degrees/sec
    cumulative_rotation = 0
    inversions = 0
    for gm in gyro_magnitudes:
        if gm > INVERSION_RATE_THRESHOLD:
            cumulative_rotation += gm * dt
            if cumulative_rotation >= 340:
                inversions += 1
                cumulative_rotation = 0
        else:
            cumulative_rotation *= 0.95

    # --- Speed estimation ---
    # Integrate vertical acceleration minus gravity baseline
    velocity = 0.0
    speeds = []
    for v in vertical_gs:
        accel_ms2 = (v - 1.0) * 9.81
        velocity += accel_ms2 * dt
        velocity = max(velocity, 0)
        speeds.append(velocity)

    max_speed_ms = max(speeds) if speeds else 0
    avg_speed_ms = sum(speeds) / len(speeds) if speeds else 0

    # Convert m/s to mph
    max_speed = max_speed_ms * 2.237
    avg_speed = avg_speed_ms * 2.237

    # --- Ride length ---
    total_distance_m = sum(s * dt for s in speeds)
    ride_length = total_distance_m * 3.281

    # Ensure non-negative
    drops = max(drops, 0)
    inversions = max(inversions, 0)
    highest_drop_height = max(highest_drop_height, 0)

    features = {
        "max_speed": round(max_speed),
        "avg_speed": round(avg_speed),
        "ride_time": round(ride_time),
        "ride_length": round(ride_length),
        "max_pos_gs": round(max_pos_gs, 2),
        "max_neg_gs": round(max_neg_gs, 2),
        "max_lateral_gs": round(max_lateral_gs, 2),
        "total_air_time": round(total_air_time, 2),
        "drops": int(drops),
        "highest_drop_height": round(highest_drop_height),
        "inversions": int(inversions),
    }

    print(f"Extracted features: {features}")
    return features


# ============================================================
# Run ML inference
# ============================================================
def predict_ratings(features):
    """Run the three Edge Impulse models to predict E/I/N scores."""
    feature_vector = [
        float(features["max_speed"]),
        float(features["avg_speed"]),
        float(features["ride_time"]),
        float(features["ride_length"]),
        float(features["max_pos_gs"]),
        float(features["max_neg_gs"]),
        float(features["max_lateral_gs"]),
        float(features["total_air_time"]),
        float(features["drops"]),
        float(features["highest_drop_height"]),
        float(features["inversions"]),
    ]

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
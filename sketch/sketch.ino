/*
 * RCT IRL - Roller Coaster Telemetry Sketch
 * 
 * Reads 6-axis IMU data (accel + gyro) from SparkFun ICM-20948 over Qwiic,
 * and sends it to the Python side via the UNO Q Bridge.
 *
 * The Python side handles signal processing and ML inference.
 *
 * Hardware: Arduino UNO Q + SparkFun 9DoF IMU (ICM-20948) via Qwiic
 * Libraries: Arduino_RouterBridge, SparkFun ICM-20948
 */

#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include "ICM_20948.h"

ICM_20948_I2C myIMU;

// Sampling config
const unsigned long SAMPLE_INTERVAL_MS = 5;  // 200 Hz
unsigned long lastSampleTime = 0;

// Recording state — controlled from Python side
bool recording = false;

// Buffers for accumulating samples before sending
// We batch 10 samples per Bridge call to reduce RPC overhead
const int BATCH_SIZE = 10;
int batchIndex = 0;

float accelX[BATCH_SIZE], accelY[BATCH_SIZE], accelZ[BATCH_SIZE];
float gyroX[BATCH_SIZE], gyroY[BATCH_SIZE], gyroZ[BATCH_SIZE];
unsigned long timestamps[BATCH_SIZE];

// Bridge callback: Python calls this to start/stop recording
void setRecording(bool state) {
  recording = state;
  batchIndex = 0;  // Reset buffer on state change

  if (state) {
    Serial.println("Recording STARTED");
  } else {
    Serial.println("Recording STOPPED");
  }
}

// Bridge callback: Python calls this to check if IMU is connected
void getStatus(int unused) {
  // Reply with 1 if IMU is good, 0 if not
  Bridge.call("imu_status", myIMU.isConnected() ? 1 : 0);
}

void sendBatch() {
  // Send each sample in the batch to Python
  // Format: timestamp, ax, ay, az, gx, gy, gz
  for (int i = 0; i < batchIndex; i++) {
    // Pack as a comma-separated string for simplicity
    String data = String(timestamps[i]) + "," +
                  String(accelX[i], 4) + "," +
                  String(accelY[i], 4) + "," +
                  String(accelZ[i], 4) + "," +
                  String(gyroX[i], 4) + "," +
                  String(gyroY[i], 4) + "," +
                  String(gyroZ[i], 4);
    
    Bridge.call("imu_data", data);
  }
  batchIndex = 0;
}

void setup() {
  Serial.begin(115200);
  
  // Initialize Bridge
  Bridge.begin();
  Bridge.provide("set_recording", setRecording);
  Bridge.provide("get_status", getStatus);
  
  // Initialize I2C for Qwiic
  Wire1.begin();
  Wire1.setClock(400000);  // 400kHz for faster I2C
  
  // Initialize IMU
  bool imuReady = false;
  for (int attempt = 0; attempt < 5; attempt++) {
    if (myIMU.begin(Wire1, 1) == ICM_20948_Stat_Ok) {  // AD0 = 1 -> address 0x69
      imuReady = true;
      break;
    }
    delay(500);
  }
  
  if (!imuReady) {
    Serial.println("ICM-20948 not found! Check Qwiic connection.");
    // Try default address
    if (myIMU.begin(Wire1, 0) == ICM_20948_Stat_Ok) {  // AD0 = 0 -> address 0x68
      imuReady = true;
      Serial.println("Found ICM-20948 at address 0x68");
    }
  }
  
  if (imuReady) {
    Serial.println("ICM-20948 initialized");
    
    // Configure accelerometer: +/- 8G range (coasters can pull 5-6G)
    ICM_20948_fss_t myFSS;
    myFSS.a = gpm8;    // +/- 8G
    myFSS.g = dps1000;  // +/- 1000 degrees per second (fast rotations)
    myIMU.setFullScale(ICM_20948_Internal_Acc | ICM_20948_Internal_Gyr, myFSS);
    
    // Set sample rate divider for ~200Hz
    // Output Data Rate = 1125 / (1 + divider)
    // For 200Hz: divider = ~4.6, use 4 for ~225Hz
    ICM_20948_smplrt_t mySmplrt;
    mySmplrt.a = 4;
    mySmplrt.g = 4;
    myIMU.setSampleRate(ICM_20948_Internal_Acc | ICM_20948_Internal_Gyr, mySmplrt);
  }
  
  Serial.println("RCT IRL Sketch ready. Waiting for Python to start recording...");
}

void loop() {
  unsigned long now = millis();
  
  if (!recording) {
    delay(100);  // Idle — don't burn cycles
    return;
  }
  
  // Sample at configured rate
  if (now - lastSampleTime >= SAMPLE_INTERVAL_MS) {
    lastSampleTime = now;
    
    if (myIMU.dataReady()) {
      myIMU.getAGMT();  // Read all sensor data
      
      // Store in batch buffer (accel in G, gyro in degrees/sec)
      timestamps[batchIndex] = now;
      accelX[batchIndex] = myIMU.accX() / 1000.0;  // mg to G
      accelY[batchIndex] = myIMU.accY() / 1000.0;
      accelZ[batchIndex] = myIMU.accZ() / 1000.0;
      gyroX[batchIndex]  = myIMU.gyrX();
      gyroY[batchIndex]  = myIMU.gyrY();
      gyroZ[batchIndex]  = myIMU.gyrZ();
      
      batchIndex++;
      
      // Send when batch is full
      if (batchIndex >= BATCH_SIZE) {
        sendBatch();
      }
    }
  }
}
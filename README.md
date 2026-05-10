# RollerCoasterTestMeter

Rate real roller coasters just like the classic game! Strap an Arduino UNO Q with an IMU into a fanny pack, ride a coaster, and get Excitement, Intensity, and Nausea scores displayed in a pixel-perfect RCT2-themed interface on your phone.

Built with Arduino UNO Q, SparkFun ICM-20948 IMU, Edge Impulse ML, and Arduino App Lab.

## How It Works

1. **Record** — Hit Start on your phone before the ride. The IMU samples acceleration and rotation at 200Hz.
2. **Process** — Hit Stop after the ride. The UNO Q's Qualcomm MPU extracts G-forces, airtime, and other ride metrics from the raw sensor data.
3. **Predict** — Three Edge Impulse regression models (trained on actual RCT2 game data) predict Excitement, Intensity, and Nausea scores.
4. **Share** — View your scores in the RCT2-styled interface and share a screenshot to socials.

## Hardware

- Arduino UNO Q
- SparkFun 9DoF IMU Breakout - ICM-20948 (Qwiic)
- Qwiic cable
- USB-C power bank (any 5V/2A+ bank works)
- Fanny pack (your enclosure)

## Project Structure

Create a new app in App Lab called "RollerCoasterTestMeter", then arrange files like this:

```
rollercoastertestmeter/
├── app.yaml                    ← App manifest (copy provided file)
├── python/
│   ├── main.py                 ← Signal processing + inference (copy provided file)
│   └── requirements.txt        ← Python deps (copy provided file)
├── sketch/
│   └── sketch.ino              ← IMU reading + Bridge (copy provided file)
├── assets/
│   └── index.html              ← RCT-themed WebUI (copy provided file)
└── models/
    ├── rct_excitement.eim       ← From Edge Impulse (excitement project)
    ├── rct_intensity.eim        ← From Edge Impulse (intensity project)
    └── rct_nausea.eim           ← From Edge Impulse (nausea project)
```

## Setup Guide

### Step 1: Create the App in App Lab

1. Open Arduino App Lab and connect to your UNO Q
2. Go to **My Apps** → **Create new app+**
3. Name it "RollerCoasterTestMeter"

### Step 2: Add Bricks and Libraries

1. Add the **WebUI - HTML** brick from the bricks menu
2. In **Sketch Libraries**, install:
   - **SparkFun 9DoF IMU Breakout - ICM 20948 - Arduino Library**
   - **Arduino_RouterBridge** (should be pre-installed)

### Step 3: Copy the Code

Paste the contents of the following files into their App Lab equivalents:

- `sketch/sketch.ino` → App Lab sketch editor
- `python/main.py` → App Lab Python editor
- `assets/index.html` → Create an `assets` folder, add `index.html`
- `python/requirements.txt` → Create in the Python folder

### Step 4: Train the Edge Impulse Models

You need three Edge Impulse projects, one each for excitement, intensity, and nausea.

For each project:

1. Create a new project at [studio.edgeimpulse.com](https://studio.edgeimpulse.com)
2. Set target device to **Arduino UNO Q (Qualcomm QRB2210)**
3. Go to **CSV Wizard** and upload the corresponding CSV (`rct_excitement.csv`, etc.)
4. Configure the wizard: label column = `label`, each row = one sample
5. Upload the CSV through the regular uploader
6. **Create Impulse**: Input features → **Flatten** processing block → **Regression** learning block
7. **Flatten settings**: uncheck everything except **Average**, set normalize to **StandardScaler**
8. Generate features, then go to **Regression**
9. Set architecture to **36 → 16 neurons**, **300 training cycles**, learning rate **0.005** (use **0.001** for nausea)
10. Train and verify MAE is around 1.0
11. Go to **Deployment** → select **Linux (AARCH64 with Qualcomm QNN)** → **Build**
12. Download the `.zip`, extract the `.eim` file

**Important for intensity**: Remove the outlier sample with label ~44.94 before training.

### Step 5: Deploy the Models to the UNO Q

The `.eim` files are compiled binaries — do NOT open them in a text editor.

You can find your Uno Q's IP address from the Settings menu in App Lab.

From your computer, copy them via SCP:

```bash
scp rct_excitement.eim arduino@<UNO_Q_IP>:~/ArduinoApps/rollercoastertestmeter/models/
scp rct_intensity.eim arduino@<UNO_Q_IP>:~/ArduinoApps/rollercoastertestmeter/models/
scp rct_nausea.eim arduino@<UNO_Q_IP>:~/ArduinoApps/rollercoastertestmeter/models/
```

Then make them executable:

```bash
ssh arduino@<UNO_Q_IP> "chmod +x ~/ArduinoApps/rollercoastertestmeter/models/*.eim"
```

Default SSH credentials: `arduino` / the password you set during initial App Lab setup.

### Step 6: Connect Hardware

Plug the SparkFun ICM-20948 into the UNO Q's Qwiic port with a Qwiic cable. That's it — no breadboard, no wiring.

### Step 7: Run

Click the green **Run** button in App Lab. Open a browser to `http://<UNO_Q_IP>:7000`.

## Park Day Deployment

This section covers how to take the project to an amusement park and use it untethered from a laptop.

### Set Up the UNO Q as a WiFi Hotspot

The simplest approach is to have the UNO Q create its own WiFi network. Your phone connects to it directly — no internet required, no router, no cables. Everything runs locally on the UNO Q.

On the UNO Q (via SSH or App Lab terminal):

```bash
# Create the hotspot
sudo nmcli dev wifi hotspot ifname wlan0 ssid "RCT-IRL" password "coaster123"
```

On your phone, connect to the **RCT-IRL** WiFi network with password `coaster123`, then browse to `http://10.42.0.1:7000`.

**VPN note:** If you run an always-on VPN on your phone, you'll need to disable it while connected to the UNO Q's hotspot. The VPN tries to route local traffic through its tunnel, which prevents you from reaching the UNO Q. Toggle it off before opening the WebUI, and back on when you're done.

### Make the Hotspot Start on Boot

Set the hotspot connection to auto-connect so it comes up every time the UNO Q powers on:

```bash
# The hotspot connection is saved as "Hotspot" — verify with:
nmcli con show

# Set it to auto-connect on boot
sudo nmcli con mod "Hotspot" connection.autoconnect yes
```

### Add to Home Screen (PWA)

In your phone's browser, navigate to `http://10.42.0.1:7000`, then:

- **iOS**: Share → Add to Home Screen
- **Android**: Menu (⋮) → Add to Home Screen

This launches full-screen without browser chrome — looks and feels like a native app.

### Auto-Start the App on Boot

So you don't need a monitor or App Lab to start the app each time:

```bash
# Create a systemd service to auto-start the app
sudo tee /etc/systemd/system/rct-irl.service << 'EOF'
[Unit]
Description=Roller Coaster Test Meter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=arduino
WorkingDirectory=/home/arduino/ArduinoApps/rollercoastertestmeter
ExecStart=/usr/bin/arduino-app-cli app start /home/arduino/ArduinoApps/rollercoastertestmeter
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable the service
sudo systemctl daemon-reload
sudo systemctl enable rct-irl.service
```

### At the Park

1. Pack the UNO Q, IMU, and battery in the fanny pack
2. Secure the IMU so it doesn't rattle around
3. Plug in the battery — UNO Q boots, starts WiFi hotspot, launches app
4. Wait ~60 seconds for boot + app startup
5. On your phone: connect to **RCT-IRL** WiFi, disable VPN if needed
6. Open the bookmarked page (or `http://10.42.0.1:7000`)
7. When you're in the queue and about to board: hit **Start**
8. **Stand still for 2-3 seconds** — the app calibrates gravity direction from the first few samples
9. Ride the coaster!
10. After the ride: hit **Stop**
11. View your E/I/N scores, tap the coaster name to label it
12. Hit **Share** to screenshot and post to socials
13. To share on socials, reconnect to your regular WiFi or mobile data first


## Troubleshooting

**IMU not detected / keeps showing "disconnected"**
- Check the Qwiic cable is fully clicked in on both ends
- The sketch tries both I2C addresses (0x68 and 0x69) — the SparkFun breakout defaults to 0x69
- Try `Wire1` instead of `Wire` in the sketch if using the Qwiic port specifically

**"Not enough ride data" after stopping**
- The threshold requires either >1.5G vertical force, >0.5G lateral force, or >30 seconds of recording
- Make sure you're actually shaking/moving the IMU, or record for longer

**Models show "Exec format error"**
- The `.eim` files are binaries — don't open or copy them as text
- Use `scp` or `adb push` to transfer them
- Make sure they're built for **Linux (AARCH64 with Qualcomm QNN)**, not generic AARCH64
- Run `chmod +x models/*.eim` after copying

**WebUI shows demo data instead of real results**
- Check the browser console for fetch errors
- Make sure the `if (window.AppBridge)` guards have been removed from `index.html`
- Verify the Python side is running (check App Lab console for "WebUI available on port 7000")

**Scores seem unreasonable**
- The models were trained on RCT2 game data with ~147 samples — they're approximate
- Speed and ride length are estimated via acceleration integration (inherently noisy)
- G-force readings and airtime are the most accurate features

**Can't reach the WebUI from your phone**
- Make sure your phone is connected to the **RCT-IRL** WiFi network, not your regular WiFi
- Disable any always-on VPN — it will block local network traffic
- Verify the app is running: the UNO Q's LED matrix or App Lab console should show activity
- Try `http://10.42.0.1:7000` — this is the default hotspot gateway address

**Can't SSH into the UNO Q**
- Use the password you set during initial App Lab setup
- If you forgot your password, use `adb shell` over USB (no password needed) and run `passwd` to reset
- When the UNO Q is in hotspot mode, SSH to `10.42.0.1`

## Credits

- **RCT Dataset**: [nolanbconaway/RollerCoaster-Tycoon-Data](https://github.com/nolanbconaway/RollerCoaster-Tycoon-Data) on GitHub / [Kaggle](https://www.kaggle.com/datasets/nolanbconaway/rollercoaster-tycoon-rides)
- **Edge Impulse**: Regression models trained and deployed via [edgeimpulse.com](https://edgeimpulse.com)
- **Inspiration**: RollerCoaster Tycoon by Chris Sawyer

## License

MIT
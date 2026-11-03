# 📦 AI-Driven Packaging Line Defect Sorter

An intelligent quality control system that uses computer vision to detect torn/damaged packages on a conveyor belt and automatically triggers an Arduino-controlled rejection arm.

## ✨ Features

- **Real-time camera feed** with center detection line
- **Automatic torn package detection** using contour analysis
- **Arduino/Adreno integration** — sends serial commands to activate rejection arm
- **Professional dashboard** with live stats, event log, and admin panel
- **Dual mode** — runs locally with camera OR deployed on Vercel as interactive demo
- **Adjustable settings** — sensitivity, threshold, detection line position, reject delay

## 🚀 Quick Start (Local)

```bash
# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the server
uvicorn main:app --host 127.0.0.1 --port 8000

# 4. Open browser
# http://127.0.0.1:8000
```

## 🔌 Arduino Setup

### Wiring Diagram
```
Arduino Pin 9  ──── Servo Signal (orange/yellow wire)
External 5V    ──── Servo Power  (red wire)
External GND   ──┬─ Servo Ground (brown/black wire)
                 └─ Arduino GND  (must be connected!)

Optional: 470-1000µF capacitor across servo power lines
```

### Upload Arduino Code
1. Open `sorter.ino` in Arduino IDE
2. Select **Tools > Board > Arduino Uno**
3. Select **Tools > Port > COM3** (your port)
4. Click **Upload**
5. **Close Serial Monitor** before starting the Python server

### Connect in Dashboard
1. Start the server: `uvicorn main:app --host 127.0.0.1 --port 8000`
2. Open `http://127.0.0.1:8000`
3. In **System Status** card, type your COM port (e.g., `COM3`)
4. Click **Connect**
5. Green = Connected ✔

Or set it via environment variable:
```bash
set SERIAL_PORT=COM3
uvicorn main:app --host 127.0.0.1 --port 8000
```

## ☁️ Vercel Deployment

### Step 1: Install Vercel CLI
```bash
npm i -g vercel
```

### Step 2: Login
```bash
vercel login
```

### Step 3: Deploy
```bash
cd C:\Users\wdaks\Downloads\files
vercel --prod
```

> **Note**: On Vercel, the camera and Arduino features are disabled.
> The dashboard runs in **simulation mode** with animated conveyor belt.
> All stats, event logging, and admin features still work.

## 🎯 How Detection Works

1. Camera captures frames at ~30 FPS
2. A **green detection line** is drawn at the center of the feed
3. A ±40px band around the line is the **detection zone**
4. **Motion detection** (frame differencing) identifies moving objects
5. **Contour analysis** checks if the object shape is irregular (torn)
6. If torn → logs event, dequeues package, sends `1` to Arduino
7. Arduino pushes servo arm UP to block the package
8. After 500ms, arm returns DOWN

## 📱 Admin Panel

- URL: `http://127.0.0.1:8000/admin`
- Default credentials: `admin` / `admin`
- Change via environment variables:
  ```bash
  set ADMIN_USER=myuser
  set ADMIN_PASS=mypass
  ```

## 📁 Project Structure

```
files/
├── main.py              # FastAPI backend (all logic)
├── sorter.ino           # Arduino sketch
├── requirements.txt     # Python dependencies
├── vercel.json          # Vercel deployment config
├── .gitignore
├── api/
│   └── index.py         # Vercel entry point
├── templates/
│   ├── index.html       # Main dashboard
│   ├── login.html       # Admin login page
│   └── admin_logs.html  # Admin event log
└── static/              # Static assets
```

## 🔧 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_PORT` | (none) | Arduino COM port (e.g., `COM3`) |
| `ADMIN_USER` | `admin` | Admin panel username |
| `ADMIN_PASS` | `admin` | Admin panel password |
| `VERCEL` | (auto) | Auto-set by Vercel platform |


# ------------------------------------------------------------
# main.py – Packaging Line Defect Sorter (Professional Edition)
# Dual-mode: Local (camera+serial) / Vercel (simulation)
# High-speed low-latency vision & persistent serial integration
# ------------------------------------------------------------
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np

# Detect Vercel deployment BEFORE importing heavy local-only libs
IS_VERCEL = os.getenv("VERCEL") is not None

if not IS_VERCEL:
    import cv2
    try:
        import serial as pyserial
    except ImportError:
        pyserial = None
else:
    cv2 = None
    pyserial = None

import uvicorn
from fastapi import (
    FastAPI,
    Request,
    Depends,
    HTTPException,
    status as fastapi_status,
    Form,
    Response,
    Cookie,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin")

if IS_VERCEL:
    DB_PATH = Path("/tmp/events.db")
else:
    DB_PATH = Path(__file__).with_name("events.db")

# ----------------------------------------------------------------------
# FastAPI app & templating
# ----------------------------------------------------------------------
app = FastAPI(title="Packaging Line Defect Sorter")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory="templates")

# ----------------------------------------------------------------------
# Status class – tracks live system state
# ----------------------------------------------------------------------
class Status:
    def __init__(self):
        self.camera: bool = False
        self.arduino: bool = False
        self.detector: str = "contour-vision"
        self.fps: int = 30
        self.serial_port: Optional[str] = None
        self.detection_active: bool = True
        self.last_detection_time: float = 0
        self.detection_cooldown: float = 1.2  # minimum seconds between sequential triggers

app_status = Status()
settings = {
    "conf_threshold": 0.50,
    "reject_delay_s": 0.0,
    "detection_sensitivity": 55,   # 10-100
    "line_position": 50,           # percentage from left (50 = center vertical)
}
last_pack: Optional[dict] = None

# ----------------------------------------------------------------------
# In-memory package queue
# ----------------------------------------------------------------------
package_queue: deque[str] = deque([f"PKG-{i:04d}" for i in range(1, 51)])


def dequeue_package() -> Optional[str]:
    try:
        return package_queue.popleft()
    except IndexError:
        return None


def enqueue_package(pkg_id: str) -> None:
    package_queue.append(pkg_id)


# ----------------------------------------------------------------------
# Session handling for admin UI
# ----------------------------------------------------------------------
admin_sessions: dict[str, str] = {}


def create_admin_session(username: str) -> str:
    token = str(uuid.uuid4())
    admin_sessions[token] = username
    return token


def get_admin_user_from_cookie(token: Optional[str] = None) -> str:
    if token and token in admin_sessions:
        return admin_sessions[token]
    raise HTTPException(
        status_code=fastapi_status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized admin session",
    )


# ----------------------------------------------------------------------
# SQLite helpers – event store
# ----------------------------------------------------------------------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                action TEXT NOT NULL,
                ms INTEGER NOT NULL,
                reviewed INTEGER
            )
            """
        )
        conn.commit()


def log_event(label: str, confidence: float, action: str, ms: int,
              reviewed: Optional[int] = None) -> None:
    ts = datetime.datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO events (ts, label, confidence, action, ms, reviewed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, label, confidence, action, ms, reviewed),
        )
        conn.commit()


def get_all_events() -> List[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def set_review(event_id: int, ok: bool) -> None:
    reviewed = 1 if ok else 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE events SET reviewed = ? WHERE id = ?",
                      (reviewed, event_id))
        conn.commit()


# ----------------------------------------------------------------------
# High-Performance Persistent Arduino Serial Controller
# ----------------------------------------------------------------------
class ArduinoController:
    """
    Maintains a persistent open serial connection to the Arduino.
    Keeps port open to avoid resetting the Arduino on each command write.
    """
    def __init__(self):
        self.ser = None
        self.lock = threading.RLock()
        self.port = None
        self.is_connected = False

    def connect(self, port_name: str) -> bool:
        if not pyserial:
            print("[SERIAL] pyserial library is not available.")
            return False

        clean_port = port_name.strip()
        with self.lock:
            self.disconnect()
            try:
                self.ser = pyserial.Serial(
                    port=clean_port,
                    baudrate=9600,
                    timeout=0.1,
                    write_timeout=0.5,
                    dsrdtr=False,
                    rtscts=False
                )
                try:
                    self.ser.dtr = False
                    self.ser.rts = False
                except Exception:
                    pass

                self.port = clean_port
                self.is_connected = True
                app_status.serial_port = self.port
                app_status.arduino = True
                print(f"[SERIAL] Successfully opened connection to {self.port}")

                threading.Thread(target=self._initial_handshake, daemon=True).start()
                return True
            except Exception as e:
                print(f"[SERIAL] Connection failure on {clean_port}: {e}")
                self.ser = None
                self.is_connected = False
                app_status.arduino = False
                return False

    def _initial_handshake(self):
        time.sleep(1.5)
        self.send("0")

    def disconnect(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.is_connected = False
        app_status.arduino = False

    def send(self, command: str) -> bool:
        """Send 1-byte command ('1'=REJECT, '0'=PASS, 'T'=TEST) in < 1ms."""
        if not self.ser or not self.is_connected:
            print(f"[SERIAL-MOCK] Command '{command}' logged (hardware offline)")
            return False

        try:
            with self.lock:
                self.ser.write(command.encode("ascii"))
                self.ser.flush()
            print(f"[SERIAL] Sent '{command}' -> Arduino (OK)")
            return True
        except Exception as e:
            print(f"[SERIAL] Write error: {e}")
            self.is_connected = False
            app_status.arduino = False
            return False

    def test_sweep(self) -> bool:
        return self.send("T")


arduino_controller = ArduinoController()


def send_serial_command(command: str):
    arduino_controller.send(command)


# ----------------------------------------------------------------------
# Camera Manager (Clean direct capture, zero-hang)
# ----------------------------------------------------------------------
class CameraManager:
    """
    Manages webcam capture. Opens camera cleanly, keeps handle open,
    and returns frames reliably without multi-second freezes.
    """
    def __init__(self):
        self.cap = None
        self.lock = threading.RLock()
        self.cam_index = 0
        self.last_open_attempt = 0
        self.last_frame_ts = time.time()

    def open(self, index: int = 0) -> bool:
        if cv2 is None:
            return False

        with self.lock:
            self._release()
            self.last_open_attempt = time.time()
            print(f"[CAMERA] Opening camera index {index}...")
            try:
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    self.cap = cap
                    self.cam_index = index
                    try:
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    app_status.camera = True
                    print(f"[CAMERA] Camera index {index} is ready")
                    return True
                cap.release()
            except Exception as e:
                print(f"[CAMERA] Error on index {index}: {e}")

            # Try index 1 as fallback if index 0 failed
            if index == 0:
                try:
                    cap = cv2.VideoCapture(1)
                    if cap.isOpened():
                        self.cap = cap
                        self.cam_index = 1
                        try:
                            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except Exception:
                            pass
                        app_status.camera = True
                        print("[CAMERA] Camera index 1 is ready (fallback)")
                        return True
                    cap.release()
                except Exception:
                    pass

            app_status.camera = False
            print("[CAMERA] No camera could be opened")
            return False

    def get_frame(self) -> Optional[np.ndarray]:
        if cv2 is None:
            return None

        with self.lock:
            if self.cap is None or not self.cap.isOpened():
                now = time.time()
                if now - self.last_open_attempt < 1.5:
                    return None
                if not self.open(self.cam_index):
                    return None

            try:
                ret, frame = self.cap.read()
            except Exception:
                ret, frame = False, None

            if not ret or frame is None:
                self._release()
                app_status.camera = False
                return None

            app_status.camera = True

            now = time.time()
            dt = now - self.last_frame_ts
            self.last_frame_ts = now
            if dt > 0.001:
                calc_fps = int(1.0 / dt)
                app_status.fps = max(10, min(60, int(0.9 * app_status.fps + 0.1 * calc_fps)))

            return frame

    def _release(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        app_status.camera = False


camera_manager = CameraManager()


# ----------------------------------------------------------------------
# Intelligent Computer Vision Engine (Item & Defect Recognition)
# ----------------------------------------------------------------------
class VisionInspector:
    """
    Analyzes objects crossing the vertical centerline.
    - Isolates moving packages using adaptive background modeling.
    - Measures contour solidity, edge roughness, and convexity defects.
    - Classifies intact packages vs. torn/destroyed defects in real-time.
    """
    def __init__(self):
        self.bg_frame = None
        self.frame_counter = 0

    def inspect(
        self, frame: np.ndarray, line_x: int, sensitivity: int, conf_floor: float
    ) -> Tuple[bool, bool, float, dict]:
        if cv2 is None:
            return False, False, 0.0, {}

        try:
            h, w = frame.shape[:2]
            band = 48  # Width of vertical corridor around centerline
            x_min = max(0, line_x - band)
            x_max = min(w, line_x + band)

            roi = frame[:, x_min:x_max]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray_blur = cv2.GaussianBlur(gray, (7, 7), 0)

            # Adaptive background model in corridor
            self.frame_counter += 1
            if self.bg_frame is None or self.frame_counter % 20 == 0:
                if self.bg_frame is None:
                    self.bg_frame = gray_blur.copy().astype("float")
                else:
                    cv2.accumulateWeighted(gray_blur, self.bg_frame, 0.08)

            bg_uint8 = cv2.convertScaleAbs(self.bg_frame)
            delta = cv2.absdiff(bg_uint8, gray_blur)

            # Dynamic thresholding based on user sensitivity
            thresh_val = max(10, 48 - int(sensitivity * 0.32))
            _, thresh = cv2.threshold(delta, thresh_val, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return False, False, 0.0, {}

            min_area = max(500, int(2000 * (1.1 - sensitivity / 100.0)))
            valid_contours = [c for c in contours if cv2.contourArea(c) >= min_area]
            if not valid_contours:
                return False, False, 0.0, {}

            best_contour = max(valid_contours, key=cv2.contourArea)
            area = cv2.contourArea(best_contour)
            bx, by, bw, bh = cv2.boundingRect(best_contour)

            frame_bbox = {
                "x": x_min + bx,
                "y": by,
                "w": bw,
                "h": bh
            }

            hull = cv2.convexHull(best_contour)
            hull_area = cv2.contourArea(hull)
            solidity = float(area) / hull_area if hull_area > 0 else 1.0

            perimeter = cv2.arcLength(best_contour, True)
            compactness = (perimeter * perimeter) / area if area > 0 else 0.0

            deep_defects = 0
            try:
                hull_indices = cv2.convexHull(best_contour, returnPoints=False)
                if len(hull_indices) > 3 and len(best_contour) > 3:
                    defects = cv2.convexityDefects(best_contour, hull_indices)
                    if defects is not None:
                        for i in range(defects.shape[0]):
                            d = defects[i, 0][3] / 256.0
                            if d > 12:
                                deep_defects += 1
            except Exception:
                pass

            solidity_thresh = 0.80 - (conf_floor - 0.50) * 0.25
            is_torn = (solidity < solidity_thresh) or (compactness > 28.0) or (deep_defects >= 2)
            confidence = max(0.60, min(0.98, 1.05 - solidity + (deep_defects * 0.05)))

            return True, is_torn, round(confidence, 2), frame_bbox
        except Exception as e:
            print(f"[VISION-ERROR] {e}")
            return False, False, 0.0, {}


vision_inspector = VisionInspector()

detection_result = {
    "detected": False,
    "label": "normal",
    "confidence": 0.0,
    "contour_area": 0,
}


# ----------------------------------------------------------------------
# Frame Processing & HUD Rendering
# ----------------------------------------------------------------------
def get_frame_with_detection() -> Tuple[bytes, dict]:
    """Capture latest frame, run machine vision, draw HUD, encode JPEG."""
    global detection_result

    frame = camera_manager.get_frame()
    if frame is None:
        return _placeholder_frame(), detection_result

    h, w = frame.shape[:2]
    line_pct = settings.get("line_position", 50)
    line_x = int(w * line_pct / 100)

    now = time.time()
    has_item = False
    is_torn = False
    conf = 0.0
    bbox = {}

    if app_status.detection_active:
        sens = settings.get("detection_sensitivity", 55)
        conf_floor = settings.get("conf_threshold", 0.50)
        has_item, is_torn, conf, bbox = vision_inspector.inspect(frame, line_x, sens, conf_floor)

        if has_item:
            if is_torn:
                detection_result = {
                    "detected": True,
                    "label": "torn",
                    "confidence": conf,
                    "contour_area": bbox.get("w", 0) * bbox.get("h", 0)
                }

                if (now - app_status.last_detection_time) > app_status.detection_cooldown:
                    app_status.last_detection_time = now
                    threading.Thread(target=_dispatch_reject, args=(conf,), daemon=True).start()

            else:
                detection_result = {
                    "detected": False,
                    "label": "normal",
                    "confidence": conf,
                    "contour_area": bbox.get("w", 0) * bbox.get("h", 0)
                }
                if (now - app_status.last_detection_time) > app_status.detection_cooldown:
                    app_status.last_detection_time = now
                    threading.Thread(target=_dispatch_pass, args=(conf,), daemon=True).start()

    # --- Draw Overlays on Frame ---
    band = 48
    # 1. Semi-transparent inspection corridor
    corridor = frame.copy()
    cv2.rectangle(corridor, (max(0, line_x - band), 0), (min(w, line_x + band), h), (16, 185, 129), -1)
    cv2.addWeighted(corridor, 0.08, frame, 0.92, 0, frame)

    # 2. Vertical Corridor boundary dashed lines
    cv2.line(frame, (max(0, line_x - band), 0), (max(0, line_x - band), h), (16, 140, 100), 1)
    cv2.line(frame, (min(w, line_x + band), 0), (min(w, line_x + band), h), (16, 140, 100), 1)

    # 3. Center Laser Tripwire (Vibrant Emerald Line)
    cv2.line(frame, (line_x, 0), (line_x, h), (0, 230, 118), 2)

    # 4. Tripwire HUD Text
    cv2.putText(frame, f"TRIPWIRE [X: {line_x}px]", (min(line_x + 8, w - 170), 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 118), 1, cv2.LINE_AA)

    # 5. Real-Time Recognized Item Bounding Box & Target Label
    if has_item and bbox:
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        if is_torn:
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
            cv2.putText(frame, f"[!] TORN DEFECT [{int(conf*100)}%]", (bx, max(20, by - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
        else:
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (255, 200, 0), 2)
            cv2.putText(frame, "[OK] INTACT ITEM [PASS]", (bx, max(20, by - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1, cv2.LINE_AA)

        cx, cy = bx + bw // 2, by + bh // 2
        cv2.drawMarker(frame, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 12, 1)

    # 6. Flash Alert Screen Border when Defect Purge is Active
    if (now - app_status.last_detection_time) < 0.9:
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 4)
        cv2.putText(frame, "! DEFECT PURGE TRIGGERED !", (w // 2 - 160, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2, cv2.LINE_AA)

    # 7. Telemetry HUD indicators
    fps_text = f"FPS: {app_status.fps}"
    cv2.putText(frame, fps_text, (w - 95, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (16, 185, 129), 1, cv2.LINE_AA)

    ard_status_text = "ARDUINO: LINKED" if app_status.arduino else "ARDUINO: OFF"
    ard_color = (16, 185, 129) if app_status.arduino else (100, 100, 100)
    cv2.putText(frame, ard_status_text, (w - 150, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.45, ard_color, 1, cv2.LINE_AA)

    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return jpeg.tobytes(), detection_result


def _dispatch_reject(confidence: float):
    global last_pack
    t0 = time.time()

    delay = settings.get("reject_delay_s", 0.0)
    if delay > 0:
        time.sleep(delay)

    ms = int((time.time() - t0) * 1000)
    log_event(label="torn", confidence=confidence, action="reject", ms=ms)

    pkg_id = dequeue_package()
    arduino_controller.send("1")

    last_pack = {
        "label": "torn",
        "conf": confidence,
        "action": "reject",
        "ms": ms,
        "time": datetime.datetime.utcnow().strftime("%H:%M:%S"),
        "package_id": pkg_id,
    }
    print(f"[QC-DEFECT] Torn package purged: ID={pkg_id}, conf={confidence:.2f}")


def _dispatch_pass(confidence: float):
    global last_pack
    log_event(label="intact", confidence=confidence, action="accept", ms=0)
    arduino_controller.send("0")
    last_pack = {
        "label": "intact",
        "conf": confidence,
        "action": "accept",
        "ms": 0,
        "time": datetime.datetime.utcnow().strftime("%H:%M:%S"),
        "package_id": None,
    }


def _placeholder_frame() -> bytes:
    """High-visibility placeholder frame with grid, emerald line, and live status."""
    h, w = 480, 640
    frame = np.full((h, w, 3), 24, dtype=np.uint8)

    # Subtle grid lines
    if cv2:
        for x in range(0, w, 40):
            cv2.line(frame, (x, 0), (x, h), (35, 38, 48), 1)
        for y in range(0, h, 40):
            cv2.line(frame, (0, y), (w, y), (35, 38, 48), 1)

        line_pct = settings.get("line_position", 50)
        line_x = int(w * line_pct / 100)

        # Bright emerald vertical laser line
        cv2.line(frame, (line_x, 0), (line_x, h), (0, 230, 118), 2)
        cv2.putText(frame, f"TRIPWIRE [X: {line_x}px]", (min(line_x + 8, w - 170), 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 118), 1, cv2.LINE_AA)

        # Central HUD Viewfinder Reticle
        cv2.rectangle(frame, (w // 2 - 130, h // 2 - 65), (w // 2 + 130, h // 2 + 65), (50, 60, 80), 1)
        cv2.putText(frame, "WEBCAM SENSOR STANDBY", (w // 2 - 110, h // 2 - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (245, 158, 11), 1, cv2.LINE_AA)
        cv2.putText(frame, "Attempting connection to Camera...", (w // 2 - 115, h // 2 + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1, cv2.LINE_AA)
        cv2.putText(frame, "Click 'Reconnect Cam' or use Simulation", (w // 2 - 120, h // 2 + 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100, 116, 139), 1, cv2.LINE_AA)

        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpeg.tobytes()
    else:
        return _minimal_jpeg()


def _minimal_jpeg() -> bytes:
    return (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
        b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
        b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
        b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
        b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00'
        b'\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
        b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdb\xae\x8a(\x03\xff\xd9'
    )


# ----------------------------------------------------------------------
# Routes – UI & High-Frame-Rate Video Feed
# ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    index_path = Path(__file__).parent / "templates" / "index.html"
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{IS_VERCEL}}", "true" if IS_VERCEL else "false")
    return HTMLResponse(content=html, status_code=200)


@app.get("/video_feed")
def video_feed():
    """Smooth MJPEG stream with zero frame backlog."""
    if IS_VERCEL:
        return Response(content=_minimal_jpeg(), media_type="image/jpeg")

    def gen():
        while True:
            frame_bytes, _ = get_frame_with_detection()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
            time.sleep(0.02)  # ~30 FPS

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace;boundary=frame"
    )


# ----------------------------------------------------------------------
# API endpoints
# ----------------------------------------------------------------------
@app.get("/api/stats")
def api_stats():
    events = get_all_events()
    reviewed = [e for e in events if e["reviewed"] is not None]
    correct = sum(1 for e in reviewed if e["reviewed"] == 1)
    accuracy = (correct / len(reviewed) * 100) if reviewed else None
    inference_times = [e["ms"] for e in events if e["ms"] > 0]
    avg_ms = (sum(inference_times) / len(inference_times)) if inference_times else None

    return {
        "inspected": sum(1 for e in events if e["action"] == "accept"),
        "rejected": sum(1 for e in events if e["action"] == "reject"),
        "accuracy": accuracy,
        "avg_inference_ms": avg_ms,
        "status": {
            "camera": app_status.camera,
            "arduino": app_status.arduino,
            "detector": app_status.detector,
            "fps": app_status.fps,
            "serial_port": app_status.serial_port,
            "detection_active": app_status.detection_active,
            "cam_index": camera_manager.cam_index,
        },
        "settings": settings,
        "last": last_pack,
        "queue_size": len(package_queue),
        "events": events[:20],
        "is_vercel": IS_VERCEL,
        "detection": detection_result,
    }


@app.post("/api/settings")
def update_settings(
    conf_threshold: float = Form(None),
    reject_delay_s: float = Form(None),
    detection_sensitivity: int = Form(None),
    line_position: int = Form(None),
    serial_port: str = Form(None),
):
    if conf_threshold is not None:
        settings["conf_threshold"] = conf_threshold
    if reject_delay_s is not None:
        settings["reject_delay_s"] = reject_delay_s
    if detection_sensitivity is not None:
        settings["detection_sensitivity"] = max(10, min(100, detection_sensitivity))
    if line_position is not None:
        settings["line_position"] = max(10, min(90, line_position))
    if serial_port is not None:
        app_status.serial_port = serial_port.strip() if serial_port.strip() else None
        if app_status.serial_port:
            arduino_controller.connect(app_status.serial_port)
    return {"ok": True, "settings": settings}


@app.post("/api/reset")
def reset_stats():
    global last_pack
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM events")
        conn.commit()
    last_pack = None
    return {"ok": True}


@app.post("/api/feedback")
def feedback(id: int = Form(...), correct: bool = Form(...)):
    set_review(id, correct)
    return {"ok": True}


@app.post("/api/torn")
def torn_event():
    global last_pack
    event = {"label": "torn", "confidence": 1.0, "action": "reject", "ms": 0}
    log_event(**event)
    pkg_id = dequeue_package()

    arduino_controller.send("1")

    last_pack = {
        "label": "torn",
        "conf": 1.0,
        "action": "reject",
        "ms": 0,
        "time": datetime.datetime.utcnow().strftime("%H:%M:%S"),
        "package_id": pkg_id,
    }
    return {"ok": True, "dequeued_package": pkg_id}


@app.post("/api/toggle_detection")
def toggle_detection():
    app_status.detection_active = not app_status.detection_active
    return {"ok": True, "detection_active": app_status.detection_active}


@app.post("/api/connect_arduino")
def connect_arduino(port: str = Form(...)):
    connected = arduino_controller.connect(port)
    return {"ok": True, "connected": connected, "port": arduino_controller.port}


@app.post("/api/test_arduino")
def test_arduino():
    sent = arduino_controller.test_sweep()
    return {"ok": True, "sent": sent}


@app.post("/api/reconnect_camera")
def reconnect_camera(index: int = Form(0)):
    success = camera_manager.open(index)
    return {"ok": True, "camera": success, "index": camera_manager.cam_index}


# ----------------------------------------------------------------------
# Admin UI (cookie-based session)
# ----------------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_home(token: Optional[str] = Cookie(None)):
    try:
        get_admin_user_from_cookie(token)
        return RedirectResponse(url="/admin/logs")
    except HTTPException:
        return RedirectResponse(url="/admin/login")


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page():
    login_path = Path(__file__).parent / "templates" / "login.html"
    with open(login_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html, status_code=200)


@app.post("/admin/login")
def admin_login(username: str = Form(...), password: str = Form(...)):
    if username != ADMIN_USER or password != ADMIN_PASS:
        raise HTTPException(
            status_code=fastapi_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    token = create_admin_session(username)
    response = RedirectResponse(url="/admin/logs", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, max_age=3600)
    return response


@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, token: Optional[str] = Cookie(None)):
    get_admin_user_from_cookie(token)
    events = get_all_events()
    rows = []
    for e in events:
        reviewed = (
            '<span class="badge bg-secondary">pending</span>'
            if e["reviewed"] is None
            else '<span class="badge bg-success">✔</span>'
            if e["reviewed"] == 1
            else '<span class="badge bg-danger">✘</span>'
        )
        rows.append(
            f"<tr><td>{e['id']}</td><td>{e['ts']}</td><td>{e['label']}</td>"
            f"<td>{e['confidence']*100:.2f}%</td><td>{e['action']}</td>"
            f"<td>{e['ms']}</td><td>{reviewed}</td></tr>"
        )
    rows_html = "\n".join(rows)
    log_path = Path(__file__).parent / "templates" / "admin_logs.html"
    with open(log_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{rows}}", rows_html)
    return HTMLResponse(content=html, status_code=200)


# ----------------------------------------------------------------------
# Startup & Shutdown Lifecycle
# ----------------------------------------------------------------------
@app.on_event("startup")
def startup():
    init_db()
    if not IS_VERCEL:
        camera_manager.open(0)
        default_port = os.getenv("SERIAL_PORT")
        if default_port:
            arduino_controller.connect(default_port)

    print(f"[STARTUP] Mode={'VERCEL' if IS_VERCEL else 'LOCAL'} | "
          f"Camera={app_status.camera} | Arduino={app_status.arduino}")


@app.on_event("shutdown")
def shutdown():
    camera_manager._release()
    arduino_controller.disconnect()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

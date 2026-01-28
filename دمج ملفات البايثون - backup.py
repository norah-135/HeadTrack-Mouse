
import sys
import os
import json
import time
import logging
import webbrowser
import ctypes
import pygetwindow as gw
import pyautogui
import serial
import threading
import wave
import speech_recognition as sr
import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from collections import deque
import io

# PyQt6 Imports
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from screeninfo import get_monitors

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ═══════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('pnuai_integrated.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION LOADER
# ═══════════════════════════════════════════════════════════════

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"❌ ملف الإعدادات غير موجود: {config_path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"❌ خطأ في قراءة الإعدادات: {e}")
        raise

CONFIG = load_config()
CREDENTIALS = CONFIG['credentials']
PATHS = CONFIG['paths']
URLS = CONFIG['urls']
UI_CONFIG = CONFIG['ui']
SELENIUM_CONFIG = CONFIG['selenium']

# ═══════════════════════════════════════════════════════════════
#  SAFETY & LOCK ZONES
# ═══════════════════════════════════════════════════════════════

SAFE_MODE = True
LOCK_ZONE_SIZE = 0.05
CURSOR_HEIGHT = 20

lock_mode = None
saved_position = None
command_queue = []
command_lock = threading.Lock()
show_calibration_msgs = False

logger.info("🛡️  SAFE MODE: ENABLED (Press SPACE to toggle)")
logger.info("📝 Controls:")
logger.info("   Keyboard: U=Top | D=Bottom | R=Release | SPACE=Safe Mode | Q=Quit")
logger.info("   Voice: 'up' | 'down' | 'release'")
logger.info("   Terminal: 0=Disable Voice | 1=Enable Voice")

# ═══════════════════════════════════════════════════════════════
# 🖱️ CLICK DETECTION
# ═══════════════════════════════════════════════════════════════

CLICK_HOLD_DURATION = 0.3
CLICK_COOLDOWN = 0.5

last_click_time = {'left': 0, 'right': 0}
click_start_time = {'left': None, 'right': None}
click_triggered = {'left': False, 'right': False}

# ═══════════════════════════════════════════════════════════════
#  ARDUINO CONNECTION (Y-axis)
# ═══════════════════════════════════════════════════════════════

arduino_port = 'COM3'
arduino = None

try:
    arduino = serial.Serial(arduino_port, 115200, timeout=0.01)
    logger.info(f"✅ Arduino Connected: {arduino_port}")
    time.sleep(2)
except Exception as e:
    logger.warning(f"⚠️ Arduino connection failed: {e}")
    arduino = None

arduino_y_position = None
arduino_lock = threading.Lock()

def arduino_reader():
    """قراءة قيم Y من Arduino"""
    global arduino_y_position
    
    if not arduino:
        return
    
    while True:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode('utf-8', errors='ignore').strip()
                
                if line.startswith("Y:"):
                    try:
                        y_val = int(line.split(":")[1])
                        with arduino_lock:
                            arduino_y_position = y_val
                    except:
                        pass
        except:
            pass
        
        time.sleep(0.005)

if arduino:
    reader_thread = threading.Thread(target=arduino_reader, daemon=True)
    reader_thread.start()
    logger.info("✅ Arduino reader thread started")

# ═══════════════════════════════════════════════════════════════
#  ESP32 VOICE CONTROL
# ═══════════════════════════════════════════════════════════════

ESP32_PORT = 'COM4'
ESP32_BAUD = 921600
RECORD_TIME = 1.7
VOICE_SAMPLE_RATE = 16000
EXPECTED_SIZE = int(VOICE_SAMPLE_RATE * RECORD_TIME * 2)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_FILE_PATH = os.path.join(BASE_DIR, "voice_record.wav")

esp32 = None
recognizer = None
voice_processing_enabled = True

try:
    esp32 = serial.Serial(ESP32_PORT, ESP32_BAUD, timeout=0.1)
    time.sleep(2)
    esp32.reset_input_buffer()
    recognizer = sr.Recognizer()
    logger.info(f"✅ ESP32 Voice Connected: {ESP32_PORT}")
except Exception as e:
    logger.warning(f"⚠️ ESP32 Voice Not Available: {e}")
    esp32 = None

voice_command_queue = []
voice_lock = threading.Lock()

def handle_voice_audio():
    """معالجة الصوت من ESP32"""
    global voice_processing_enabled
    
    if not esp32 or not recognizer:
        return
    
    try:
        esp32.reset_input_buffer()
        raw_audio = b""
        start_time = time.time()
        
        while len(raw_audio) < EXPECTED_SIZE:
            if esp32.in_waiting > 0:
                chunk_size = min(esp32.in_waiting, EXPECTED_SIZE - len(raw_audio))
                raw_audio += esp32.read(chunk_size)
            if time.time() - start_time > 5.0:
                break

        if len(raw_audio) < EXPECTED_SIZE // 2:
            if voice_processing_enabled:
                logger.warning("❌ Audio too short")
            return

        if not voice_processing_enabled:
            logger.info("🔴 Voice received but processing is DISABLED")
            return

        logger.info(f"🎤 Recording...")
        
        with wave.open(VOICE_FILE_PATH, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(VOICE_SAMPLE_RATE)
            wf.writeframes(raw_audio)

        audio_data = sr.AudioData(raw_audio, VOICE_SAMPLE_RATE, 2)
        text = recognizer.recognize_google(audio_data, language="en-US").lower()
        logger.info(f"👂 Heard: '{text}'")
        
        up_matches = ["up", "above", "higher", "top"]
        down_matches = ["down", "lower"]
        release_matches = ["release", "stop", "back"]

        command = None
        
        if any(word in text for word in up_matches):
            command = 'U'
            logger.info("🔵 [VOICE] Command: UP")
        elif any(word in text for word in down_matches):
            command = 'D'
            logger.info("🟠 [VOICE] Command: DOWN")
        elif any(word in text for word in release_matches):
            command = 'R'
            logger.info("🔓 [VOICE] Command: RELEASE")
        
        if command:
            with voice_lock:
                voice_command_queue.append(command)
    
    except sr.UnknownValueError:
        if voice_processing_enabled:
            logger.warning("❌ Voice not clear")
    except sr.RequestError:
        if voice_processing_enabled:
            logger.warning("❌ Internet issue")
    except Exception as e:
        if voice_processing_enabled:
            logger.error(f"❌ Error: {e}")

def voice_command_listener():
    """الاستماع للأوامر الصوتية من ESP32"""
    if not esp32:
        logger.warning("❌ ESP32 not available")
        return
    
    logger.info("✅ Voice command listener started - waiting for ESP32 signals...")
    bytes_received_total = 0
    signal_count = 0
    
    while True:
        try:
            if esp32.in_waiting > 0:
                bytes_available = esp32.in_waiting
                bytes_received_total += bytes_available
                signal_count += 1
                
                logger.info(f"📊 [Signal #{signal_count}] {bytes_available} bytes waiting (total: {bytes_received_total})")
                
                line = esp32.readline().decode('utf-8', errors='ignore').strip()
                
                if not line or len(line) < 1:
                    logger.debug(f"   (empty line)")
                    continue
                
                logger.info(f"   📨 Received: '{line}'")
                
                if "RECORDING_START" in line or "[RECORDING_START]" in line or "RECORDING" in line:
                    logger.info(f"   🎤 RECORD SIGNAL DETECTED! Processing audio...")
                    if voice_processing_enabled:
                        handle_voice_audio()
                    else:
                        logger.warning(f"   🔴 Voice processing is DISABLED - ignoring")
        
        except UnicodeDecodeError as e:
            logger.debug(f"   Unicode error: {e}")
        except Exception as e:
            logger.error(f"   ❌ Voice listener error: {e}")
        
        time.sleep(0.01)

if esp32:
    voice_thread = threading.Thread(target=voice_command_listener, daemon=True)
    voice_thread.start()

# ═══════════════════════════════════════════════════════════════
# ⌨️ TERMINAL INPUT
# ═══════════════════════════════════════════════════════════════

def terminal_input_reader():
    """قراءة الأوامر من Terminal"""
    global command_queue
    
    print("\n💡 Terminal Input Ready!")
    print("   Type: U | D | R | Q | 0 | 1")
    print("   Then press ENTER\n")
    
    while True:
        try:
            cmd = input().strip().upper()
            if cmd in ['U', 'D', 'R', 'Q', 'SPACE', '0', '1']:
                with command_lock:
                    command_queue.append(cmd)
        except EOFError:
            break
        except KeyboardInterrupt:
            break

terminal_thread = threading.Thread(target=terminal_input_reader, daemon=True)
terminal_thread.start()
logger.info("✅ Terminal input thread started")

# ═══════════════════════════════════════════════════════════════
#  MEDIAPIPE SETUP
# ═══════════════════════════════════════════════════════════════

model_path = r"C:\Users\ASUS\Desktop\face_landmarker.task"
if not os.path.exists(model_path):
    logger.error(f" Model not found: {model_path}")
    sys.exit(1)

base_options = python.BaseOptions(model_asset_path=model_path)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    output_face_blendshapes=True,
    num_faces=1
)
detector = vision.FaceLandmarker.create_from_options(options)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 60)

# ═══════════════════════════════════════════════════════════════
#  SYSTEM SETTINGS
# ═══════════════════════════════════════════════════════════════

SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()

ROTATION_LIMIT = 0.6
SMOOTH_FACTOR_NORMAL = 0.08
SMOOTH_FACTOR_SLOW = 0.03
BUFFER_SIZE = 8
DEADZONE_X = 0.15
EXPO_POWER = 1.8

SMOOTH_FACTOR_Y_NORMAL = 0.20
SMOOTH_FACTOR_Y_EDGE = 0.06

EDGE_ZONE_TOP = 0.05
EDGE_ZONE_BOTTOM = 0.95

SPEED_SCALE_NORMAL = 1.0
SPEED_SCALE_SLOW = 0.25
SPEED_SCALE_EDGE_Y = 0.25

rotation_buffer = deque(maxlen=BUFFER_SIZE)

smooth_x = SCREEN_WIDTH // 2
smooth_y = SCREEN_HEIGHT // 2
velocity_x = 0

current_speed_mode = "NORMAL"
current_y_zone = "NORMAL"

frame_count = 0
start_time = time.time()
last_frame_time = time.time()

logger.info(f"📊 Screen: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
logger.info(f"✨ Edge Zones: Top {EDGE_ZONE_TOP*100:.0f}% | Bottom {(1-EDGE_ZONE_BOTTOM)*100:.0f}%")
logger.info(f"🔒 Lock Zones: Top 0-{int(SCREEN_HEIGHT*LOCK_ZONE_SIZE)}px")
logger.info("🚀 Starting Integrated System...")

# ═══════════════════════════════════════════════════════════════
#  LOCK ZONES FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_in_lock_zone(y_pos):
    percent = y_pos / SCREEN_HEIGHT
    return percent <= LOCK_ZONE_SIZE or percent >= (1 - LOCK_ZONE_SIZE)

def lock_to_top():
    global lock_mode, saved_position, smooth_y
    
    if not is_in_lock_zone(smooth_y):
        saved_position = (smooth_x, smooth_y)
        logger.info(f"💾 Saved: ({int(smooth_x)}, {int(smooth_y)})")
    
    lock_mode = 'up'
    max_y_allowed = int(SCREEN_HEIGHT * LOCK_ZONE_SIZE) - CURSOR_HEIGHT
    
    if smooth_y > max_y_allowed:
        smooth_y = max_y_allowed
        logger.info(f"🔵 LOCKED TOP - Y={int(smooth_y)}")
    else:
        logger.info(f"🔵 LOCKED TOP")

def lock_to_bottom():
    global lock_mode, saved_position, smooth_y
    
    if not is_in_lock_zone(smooth_y):
        saved_position = (smooth_x, smooth_y)
        logger.info(f"💾 Saved: ({int(smooth_x)}, {int(smooth_y)})")
    
    lock_mode = 'down'
    min_y_zone = int(SCREEN_HEIGHT * (1 - LOCK_ZONE_SIZE))
    
    if smooth_y < min_y_zone:
        smooth_y = min_y_zone
        logger.info(f"🟠 LOCKED BOTTOM - Y={int(smooth_y)}")
    else:
        logger.info(f"🟠 LOCKED BOTTOM")

def release_lock():
    global lock_mode, saved_position, smooth_y
    
    if lock_mode is None:
        logger.warning("❌ Already FREE")
        return
    
    lock_mode = None
    
    if saved_position is not None:
        smooth_y = saved_position[1]
        logger.info(f"🔓 RELEASED - Restored to Y={int(smooth_y)}")
        saved_position = None
    else:
        logger.info(f"🔓 RELEASED")

def enforce_lock():
    global smooth_y
    
    if lock_mode == 'up':
        max_y = int(SCREEN_HEIGHT * LOCK_ZONE_SIZE) - CURSOR_HEIGHT
        if smooth_y > max_y:
            smooth_y = max_y
    elif lock_mode == 'down':
        min_y = int(SCREEN_HEIGHT * (1 - LOCK_ZONE_SIZE))
        if smooth_y < min_y:
            smooth_y = min_y

# ═══════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def apply_expo_curve(value, power=1.8):
    sign = 1 if value >= 0 else -1
    return sign * (abs(value) ** power)

def smooth_with_buffer(new_value, buffer):
    buffer.append(new_value)
    return sum(buffer) / len(buffer)

def get_y_zone(y_position):
    percent = y_position / SCREEN_HEIGHT
    if percent < EDGE_ZONE_TOP:
        return "TOP_EDGE"
    elif percent > EDGE_ZONE_BOTTOM:
        return "BOTTOM_EDGE"
    else:
        return "NORMAL"

def detect_mouth_pull(mesh, rotation_value):
    left_mouth = mesh[61]
    right_mouth = mesh[291]
    mouth_center = mesh[14]
    
    mouth_width = abs(right_mouth.x - left_mouth.x)
    if mouth_width < 0.01:
        return None, 0
    
    left_ratio = (mouth_center.y - left_mouth.y) / mouth_width
    right_ratio = (mouth_center.y - right_mouth.y) / mouth_width
    asymmetry = left_ratio - right_ratio
    
    abs_rotation = abs(rotation_value)
    
    if abs_rotation < 0.3:
        if asymmetry < -0.080:
            return 'right', abs(asymmetry)
        elif asymmetry > 0.080:
            return 'left', abs(asymmetry)
    else:
        if rotation_value < -0.3:
            if asymmetry < -0.080:
                return 'left', abs(asymmetry)
        elif rotation_value > 0.3:
            if asymmetry > 0.080:
                return 'left', abs(asymmetry)
    
    return None, 0

def execute_click(click_type):
    if SAFE_MODE:
        return
    try:
        if click_type == 'left':
            pyautogui.click(button='left')
        elif click_type == 'right':
            pyautogui.click(button='right')
    except:
        pass

def process_clicks(click_type, click_strength, current_time):
    global last_click_time, click_start_time, click_triggered
    
    if click_type is None:
        click_start_time['left'] = None
        click_start_time['right'] = None
        click_triggered['left'] = False
        click_triggered['right'] = False
        return None
    
    if current_time - last_click_time[click_type] < CLICK_COOLDOWN:
        return None
    
    if click_start_time[click_type] is None:
        click_start_time[click_type] = current_time
        return None
    
    hold_duration = current_time - click_start_time[click_type]
    
    if hold_duration >= CLICK_HOLD_DURATION and not click_triggered[click_type]:
        execute_click(click_type)
        click_triggered[click_type] = True
        last_click_time[click_type] = current_time
        click_start_time[click_type] = None
        return click_type
    
    return None

# ═══════════════════════════════════════════════════════════════
#  PYQT6 CONTROL PANEL
# ═══════════════════════════════════════════════════════════════

class PNUAI_Assistant(QWidget):
    """لوحة التحكم الرئيسية"""
    
    def __init__(self):
        super().__init__()
        
        logger.info("🚀 تهيئة مساعد PNUAI...")
        
        self.username = CREDENTIALS['username']
        self.password = CREDENTIALS['password']
        self.driver_path = PATHS['webdriver']
        
        self.bb_login_url = URLS['blackboard_login']
        self.bb_courses_url = URLS['blackboard_courses']
        self.gemini_url = URLS['gemini']
        self.gmail_url = URLS['gmail']
        self.youtube_url = URLS['youtube']

        self.edge_driver = None
        
        self.tabs = {
            'blackboard': None,
            'gemini': None,
            'gmail': None,
            'youtube': None,
            'study_app': None
        }
        
        if not os.path.exists(self.driver_path):
            logger.warning(f"⚠️ WebDriver غير موجود: {self.driver_path}")

        self.initUI()
        logger.info("✅ تم تهيئة لوحة التحكم")

    def initUI(self):
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(UI_CONFIG['panel_full_opacity'])
        
        self.main_frame = QFrame(self)
        self.main_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #8b92a0, stop:1 #7a8294);
                border-radius: 20px;
                border: none;
            }
        """)
        
        main_shadow = QGraphicsDropShadowEffect()
        main_shadow.setBlurRadius(40)
        main_shadow.setColor(QColor(0, 0, 0, 40))
        main_shadow.setOffset(0, 10)
        self.main_frame.setGraphicsEffect(main_shadow)
        
        layout = QVBoxLayout(self.main_frame)
        layout.setContentsMargins(22, 25, 22, 20)
        layout.setSpacing(10)
        
        title = QLabel("Control Panel")
        title.setStyleSheet("""
            color: #f5f5f5; 
            font-size: 18px; 
            font-weight: 700; 
            font-family: 'Segoe UI Semibold';
        """)
        layout.addWidget(title)

        self.add_card_button(layout, "Blackboard", self.launch_blackboard)
        self.add_card_button(layout, "Gemini AI", self.launch_gemini_automated, is_ai=True)
        self.add_card_button(layout, "Gmail", lambda: self.launch_normal("Gmail", self.gmail_url))
        self.add_card_button(layout, "YouTube", lambda: self.launch_normal("YouTube", self.youtube_url))
        self.add_card_button(layout, "Study App", self.study_app_action)
        self.add_card_button(layout, "Add Shortcut", lambda: logger.info("إضافة اختصار جديد"), is_ghost=True)


        self.exit_btn = QPushButton("Exit")
        self.exit_btn.setFixedHeight(42)
        self.exit_btn.setStyleSheet("""
            QPushButton {
                background-color: #6a7280;
                color: #f5f5f5;
                border-radius: 10px;
                font-weight: 700;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover { background-color: #5a6370; }
            QPushButton:pressed { background-color: #4a5360; }
        """)
        self.exit_btn.clicked.connect(QApplication.instance().quit)
        layout.addWidget(self.exit_btn)

        final_layout = QVBoxLayout(self)
        final_layout.addWidget(self.main_frame)
        
        self.setGeometry(
            UI_CONFIG['panel_x_offset'],
            UI_CONFIG['panel_y_offset'],
            UI_CONFIG['panel_width'],
            UI_CONFIG['panel_height']
        )

    def add_card_button(self, layout, text, func, is_ghost=False, is_ai=False):
        btn = QPushButton(text)
        btn.setFixedHeight(UI_CONFIG['button_height'])
        
        if not is_ghost:
            bg_color = "#7a8294" if is_ai else "#8b92a0"
            text_color = "#f5f5f5"
            
            style = f"""
                QPushButton {{
                    background-color: {bg_color};
                    color: {text_color};
                    border-radius: 10px;
                    font-size: 12px;
                    font-weight: 600;
                    font-family: 'Segoe UI';
                    text-align: left;
                    padding-left: 16px;
                    border: none;
                }}
                QPushButton:hover {{ background-color: #6a7280; color: #ffffff; }}
                QPushButton:pressed {{ background-color: #5a6270; }}
            """
        else:
            style = """
                QPushButton {{
                    background-color: transparent;
                    color: #a0a9b8;
                    border: 1px solid #a0a9b8;
                    border-radius: 10px;
                    font-size: 12px;
                    font-weight: 600;
                    padding-left: 16px;
                }}
                QPushButton:hover {{
                    border: 1px solid #c5c9d0;
                    color: #c5c9d0;
                    background-color: rgba(255, 255, 255, 0.05);
                }}
            """
        
        btn.setStyleSheet(style)
        btn.clicked.connect(func)
        layout.addWidget(btn)

    def ensure_driver(self):
        """التأكد من وجود نافذة Edge واحدة فقط"""
        # تحقق من أن الـ driver لا يزال حياً
        if self.edge_driver:
            try:
                # محاولة بسيطة للتأكد من أن الـ session حي
                self.edge_driver.current_window_handle
                logger.info("♻️ استخدام driver موجود")
                return self.edge_driver
            except Exception as e:
                logger.warning(f"⚠️ driver القديم مات: {e}")
                self.edge_driver = None
                # أعد تعيين التبويبات
                for key in self.tabs:
                    self.tabs[key] = None
        
        logger.info("🆕 إنشاء نافذة Edge جديدة...")
        options = EdgeOptions()
        options.add_experimental_option("detach", SELENIUM_CONFIG['detach_browser'])
        options.add_argument("--use-fake-ui-for-media-stream" if SELENIUM_CONFIG['fake_media_stream'] else "")
        
        service = EdgeService(executable_path=self.driver_path)
        self.edge_driver = webdriver.Edge(service=service, options=options)
        
        time.sleep(1)
        self.force_move_window(ctypes.windll.user32.GetForegroundWindow())
        logger.info("✅ تم إنشاء نافذة Edge")
        
        return self.edge_driver
    
    def switch_or_create_tab(self, tab_name, url):
        """التبديل لتبويب موجود أو إنشاء تبويب جديد
        
        Args:
            tab_name: اسم التبويب ('blackboard', 'gemini', إلخ)
            url: الرابط المراد فتحه
        
        Returns:
            True إذا تم التبديل لتبويب موجود، False إذا تم إنشاء تبويب جديد
        """
        driver = self.ensure_driver()
        
        # محاولة التبديل لتبويب موجود
        if self.tabs[tab_name] is not None:
            try:
                # تحقق من أن التبويب لا يزال موجوداً
                driver.switch_to.window(self.tabs[tab_name])
                logger.info(f"♻️ الانتقال لتبويب {tab_name} الموجود")
                return True  # تبويب موجود
            except Exception as e:
                logger.warning(f" التبويب القديم لم يعد موجوداً: {e}")
                self.tabs[tab_name] = None
        
        # إنشاء تبويب جديد
        try:
            logger.info(f" إنشاء تبويب جديد لـ {tab_name}...")
            driver.execute_script("window.open('');")
            driver.switch_to.window(driver.window_handles[-1])
            
            # حفظ معرف التبويب الجديد
            self.tabs[tab_name] = driver.current_window_handle
            
            # فتح الرابط
            driver.get(url)
            time.sleep(1)
            
            return False  # تبويب جديد
        except Exception as e:
            logger.error(f" خطأ في إنشاء تبويب: {e}")
            raise
    
    def launch_gemini_automated(self):
        """منطق جيميناي: فتح في تبويب مخصص (إعادة استخدام النافذة)"""
        logger.info(" بدء تشغيل Gemini AI...")
        
        try:
            # التبديل لتبويب Gemini أو إنشاء جديد
            is_existing = self.switch_or_create_tab('gemini', self.gemini_url)
            
            if not is_existing:
                # تبويب جديد - تفعيل المايك
                self.activate_mic_in_existing_driver(self.edge_driver)
            
            logger.info(" تم فتح Gemini بنجاح")
            
        except Exception as e:
            logger.error(f" خطأ في Gemini: {e}")
    
    def activate_mic_in_existing_driver(self, driver):
        """تفعيل المايك في driver موجود"""
        logger.info(" تفعيل الميكروفون...")
        try:
            # انتظار تحميل الصفحة
            time.sleep(SELENIUM_CONFIG['page_load_delay'])
            
            wait = WebDriverWait(driver, SELENIUM_CONFIG['mic_activation_timeout'])
            
            # 🔍 طريقة 1: البحث عن كل الأزرار وفحصها
            logger.info("🔍 البحث عن جميع الأزرار...")
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                logger.info(f"   وجدت {len(all_buttons)} زر")
                
                for i, btn in enumerate(all_buttons):
                    try:
                        aria_label = btn.get_attribute("aria-label") or ""
                        title = btn.get_attribute("title") or ""
                        class_name = btn.get_attribute("class") or ""
                        
                        # البحث عن كلمات مفتاحية
                        if any(word in aria_label.lower() for word in ['voice', 'microphone', 'صوت', 'ميكروفون']) or \
                           any(word in title.lower() for word in ['voice', 'microphone', 'صوت']) or \
                           'mic' in class_name.lower():
                            
                            logger.info(f"    وجدت زر الميك #{i}: aria-label='{aria_label}', title='{title}'")
                            time.sleep(0.5)
                            
                            # محاولة النقر
                            try:
                                btn.click()
                            except:
                                driver.execute_script("arguments[0].click();", btn)
                            
                            logger.info(" تم تفعيل الميكروفون! ")
                            return True
                    except:
                        continue
            except Exception as e:
                logger.warning(f"   الطريقة 1 فشلت: {e}")
            
            # 🔍 طريقة 2: XPath مباشر
            logger.info("🔍 محاولة XPath...")
            try:
                xpath_queries = [
                    "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'voice')]",
                    "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'microphone')]",
                    "//button[contains(@aria-label, 'صوت')]",
                    "//button[.//svg]",
                ]
                
                for xpath in xpath_queries:
                    try:
                        mic_btn = driver.find_element(By.XPATH, xpath)
                        if mic_btn:
                            logger.info(f"   ✅ وجدت عبر XPath: {xpath[:50]}...")
                            time.sleep(0.5)
                            try:
                                mic_btn.click()
                            except:
                                driver.execute_script("arguments[0].click();", mic_btn)
                            logger.info("✅ تم تفعيل الميكروفون عبر XPath! 🎤")
                            return True
                    except:
                        continue
            except Exception as e:
                logger.warning(f"   الطريقة 2 فشلت: {e}")
            
           
            logger.info("🔍 محاولة منطقة الإدخال...")
            try:
                input_area = driver.find_element(By.CSS_SELECTOR, "div.input-area, div[role='textbox']")
                nearby_buttons = input_area.find_elements(By.XPATH, "./preceding-sibling::button | ./following-sibling::button | .//button")
                if nearby_buttons:
                    first_btn = nearby_buttons[0]
                    logger.info(f"   وجدت زر بجانب منطقة الإدخال")
                    time.sleep(0.5)
                    try:
                        first_btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", first_btn)
                    logger.info("✅ تم نقر الزر! 🎤")
                    return True
            except Exception as e:
                logger.warning(f"   الطريقة 3 فشلت: {e}")
            
            logger.error(" جميع الطرق فشلت - لم نجد زر الميكروفون")
            return False
                
        except Exception as e:
            logger.error(f" خطأ في تفعيل الميكروفون: {e}")
            return False
    
    def open_new_gemini(self):
        """(غير مستخدم الآن - استخدم launch_gemini_automated بدلاً منه)"""
        pass
    
    def force_move_window(self, hwnd):
        """تموضع النافذة - 70% على اليمين (Control Panel 30% على اليسار)"""
        try:
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)  # عرض الشاشة
            sh = user32.GetSystemMetrics(1)  # ارتفاع الشاشة
            
            # حساب عرض النافذة - 70% من الشاشة
            target_w = int(sw * 0.70)
            
            # موضع X: نبدأ من 30% (نخلي اليسار لوحة التحكم)
            start_x = int(sw * 0.30)
            
            # تعديل الارتفاع - نطرح taskbar height
            target_h = sh - 40
            start_y = 0
            
            # تطبيق التموضع
            user32.ShowWindow(hwnd, 9)  # استرجاع النافذة إذا كانت مخفية
            user32.SetWindowPos(hwnd, 0, start_x, start_y, target_w, target_h, 0x0040)
            
            logger.info(f"✅ تم تموضع النافذة: x={start_x}px (30%), width={target_w}px (70%), height={target_h}px")
        except Exception as e:
            logger.error(f"❌ خطأ في تموضع النافذة: {e}")
    
    def launch_blackboard(self):
        """تسجيل دخول تلقائي للبلاك بورد"""
        try:
            logger.info(" بدء تشغيل البلاك بورد...")
            
            # التبديل لتبويب Blackboard أو إنشاء جديد
            is_existing = self.switch_or_create_tab('blackboard', self.bb_login_url)
            
            if is_existing:
                # تبويب موجود - لا تسجل دخول مرة أخرى
                logger.info(" تبويب البلاك بورد موجود بالفعل")
                return
            
            # تبويب جديد - قم بتسجيل الدخول
            logger.info(" تسجيل دخول جديد...")
            wait = WebDriverWait(self.edge_driver, SELENIUM_CONFIG['wait_timeout'])
            wait.until(EC.presence_of_element_located((By.ID, "user_id"))).send_keys(self.username)
            self.edge_driver.find_element(By.ID, "password").send_keys(self.password)
            self.edge_driver.find_element(By.ID, "entry-login").click()
            
            time.sleep(4)
            self.edge_driver.get(self.bb_courses_url)
            logger.info("✅ تم تسجيل الدخول للبلاك بورد بنجاح")
            
        except Exception as e:
            logger.error(f"❌ خطأ في البلاك بورد: {e}")

    def launch_normal(self, title_key, url):
        """فتح رابط في المتصفح الافتراضي + تموضع النافذة"""
        logger.info(f"🌐 فتح {title_key}...")
        
        try:
            # افتح في المتصفح الافتراضي
            webbrowser.open(url)
            
            # بعد ثانيتين، ابحث عن النافذة وموضعها
            QTimer.singleShot(2500, lambda: self.find_and_snap(title_key))
            
            logger.info(f"✅ تم فتح {title_key}")
        except Exception as e:
            logger.error(f"❌ خطأ في فتح {title_key}: {e}")

    def find_and_snap(self, keyword):
        """البحث وتموضع النافذة - 70% على اليمين"""
        try:
            # تعريف كلمات البحث البديلة
            search_keywords = {
                "Study": ["study", "المذاكرة", "تطبيق"],
                "Gmail": ["gmail", "google", "inbox"],
                "YouTube": ["youtube", "video"],
                "Gemini": ["gemini", "google", "ai"]
            }
            
            keywords_to_search = search_keywords.get(keyword, [keyword.lower()])
            
            for win in gw.getAllWindows():
                if win.visible:
                    win_title_lower = win.title.lower()
                    # ابحث عن أي كلمة مفتاحية
                    if any(kw in win_title_lower for kw in keywords_to_search):
                        self.force_move_window(win._hWnd)
                        logger.info(f"✅ تم تموضع {keyword}: {win.title}")
                        return
            
            logger.warning(f"⚠️ لم نجد نافذة تطابق: {keyword}")
        except Exception as e:
            logger.warning(f"⚠️ خطأ في التموضع: {e}")

    def study_app_action(self):
        """فتح برنامج المذاكرة في المتصفح الافتراضي + تموضع النافذة"""
        
        # 🔧 طريقة 1: المسار النسبي (نفس مجلد الكود)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        study_app_path = os.path.join(script_dir, "smart_study_app.html")
        
        logger.info(f"🔍 البحث عن تطبيق المذاكرة في: {study_app_path}")
        
        if os.path.exists(study_app_path):
            try:
                # تحويل المسار لـ file:/// URL
                file_url = f"file:///{study_app_path.replace(os.sep, '/')}"
                
                # افتح في المتصفح الافتراضي
                webbrowser.open(file_url)
                
                # بعد ثانيتين، ابحث عن النافذة وموضعها
                QTimer.singleShot(2500, lambda: self.find_and_snap("Study"))
                
                logger.info("✅ تم فتح تطبيق المذاكرة في المتصفح")
                
            except Exception as e:
                logger.error(f"❌ خطأ في فتح تطبيق المذاكرة: {e}")
        else:
            logger.error(f"⚠️ لم نجد ملف تطبيق المذاكرة!")
            logger.info(f"   المكان المتوقع: {study_app_path}")
            logger.info(f"   مجلد الكود: {script_dir}")
            logger.info(f"\n💡 الحل:")
            logger.info(f"   1. ضع 'smart_study_app.html' في: {script_dir}")
            logger.info(f"   أو")
            logger.info(f"   2. عدّل المسار في config.json")
            
            # محاولة أخيرة - المجلد الحالي
            current_dir_path = os.path.join(os.getcwd(), "smart_study_app.html")
            if os.path.exists(current_dir_path):
                logger.info(f"\n✅ وجدت في المجلد الحالي: {current_dir_path}")
                file_url = f"file:///{current_dir_path.replace(os.sep, '/')}"
                webbrowser.open(file_url)
                QTimer.singleShot(2500, lambda: self.find_and_snap("Study"))
            else:
                logger.error(f"\n❌ ليست في المجلد الحالي: {current_dir_path}")


    def enterEvent(self, event):
        self.setWindowOpacity(UI_CONFIG['panel_full_opacity'])

    def leaveEvent(self, event):
        self.setWindowOpacity(UI_CONFIG['panel_opacity'])

# ═══════════════════════════════════════════════════════════════
#  MAIN LOOP - Face Control
# ═══════════════════════════════════════════════════════════════

def run_face_control():
    """حلقة التحكم بالوجه الرئيسية"""
    global SAFE_MODE, smooth_x, smooth_y, velocity_x, current_speed_mode, current_y_zone
    global lock_mode, saved_position, voice_processing_enabled
    
    current_speed_scale_x = SPEED_SCALE_NORMAL
    
    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            break
        
        current_time = time.time()
        frame_time = current_time - last_frame_time
        actual_fps = 1 / frame_time if frame_time > 0 else 0
        
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        
        rgb_frame = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        )
        detection_result = detector.detect(rgb_frame)
        
        if detection_result.face_landmarks:
            mesh = detection_result.face_landmarks[0]
            
            # Lip purse detection
            face_len = abs(mesh[4].y - mesh[152].y)
            lip_gap = abs(mesh[11].y - mesh[16].y)
            current_ratio = lip_gap / face_len
            
            if face_len < 0.20:
                active_threshold = 0.13
            elif face_len > 0.26:
                active_threshold = 0.09
            else:
                active_threshold = 0.11
            
            is_lips_pursed = (current_ratio < active_threshold)
            
            if is_lips_pursed:
                current_speed_mode = "SLOW"
                current_smooth_factor_x = SMOOTH_FACTOR_SLOW
                current_speed_scale_x = SPEED_SCALE_SLOW
            else:
                current_speed_mode = "NORMAL"
                current_smooth_factor_x = SMOOTH_FACTOR_NORMAL
                current_speed_scale_x = SPEED_SCALE_NORMAL
            
            # Calculate X-axis
            left_point = mesh[234]
            center_point = mesh[4]
            right_point = mesh[454]
            
            dist_to_left = abs(center_point.x - left_point.x)
            dist_to_right = abs(right_point.x - center_point.x)
            total_width = abs(right_point.x - left_point.x)
            
            if total_width > 0:
                ratio_to_right = dist_to_right / total_width
                raw_rotation = (0.5 - ratio_to_right) * 2
            else:
                raw_rotation = 0
            
            filtered_rotation = smooth_with_buffer(raw_rotation, rotation_buffer)
            
            if abs(filtered_rotation) < DEADZONE_X:
                filtered_rotation = 0
                velocity_x = 0
            
            filtered_rotation = np.clip(filtered_rotation, -ROTATION_LIMIT, ROTATION_LIMIT)
            filtered_rotation = filtered_rotation / ROTATION_LIMIT
            expo_rotation = apply_expo_curve(filtered_rotation, EXPO_POWER)
            
            target_x = (expo_rotation + 1) / 2 * SCREEN_WIDTH
            target_x = max(0, min(SCREEN_WIDTH - 1, target_x))
            
            target_velocity = (target_x - smooth_x) * current_smooth_factor_x
            target_velocity = target_velocity * current_speed_scale_x
            velocity_x = velocity_x * 0.7 + target_velocity * 0.3
            smooth_x = smooth_x + velocity_x
            smooth_x = max(0, min(SCREEN_WIDTH - 1, smooth_x))
            
            # Click detection
            click_type, click_strength = detect_mouth_pull(mesh, filtered_rotation)
            executed_click = process_clicks(click_type, click_strength, current_time)
            
            if executed_click:
                click_name = "LEFT" if executed_click == 'left' else "RIGHT"
                logger.info(f"🖱️  {click_name} CLICK!")
            
            # Y-axis from Arduino
            with arduino_lock:
                if arduino_y_position is not None:
                    target_y = arduino_y_position
                    current_y_zone = get_y_zone(smooth_y)
                    
                    if current_y_zone == "TOP_EDGE" or current_y_zone == "BOTTOM_EDGE":
                        y_smooth_factor = SMOOTH_FACTOR_Y_EDGE
                        y_speed_scale = SPEED_SCALE_EDGE_Y
                    else:
                        y_smooth_factor = SMOOTH_FACTOR_Y_NORMAL
                        y_speed_scale = SPEED_SCALE_SLOW if is_lips_pursed else 1.0
                    
                    y_diff = target_y - smooth_y
                    y_diff = y_diff * y_speed_scale
                    smooth_y = smooth_y * (1 - y_smooth_factor) + (smooth_y + y_diff) * y_smooth_factor
                    smooth_y = max(0, min(SCREEN_HEIGHT - 1, smooth_y))
            
            enforce_lock()
            
            if not SAFE_MODE:
                try:
                    pyautogui.moveTo(int(smooth_x), int(smooth_y), duration=0, _pause=False)
                except:
                    pass
        
        # UI overlay - dark background
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (w-10, h-10), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        
        # Border
        cv2.rectangle(frame, (10, 10), (w-10, h-10), (0, 200, 255), 2)
        
        # ═══════════════════════════════════════════
        # TOP SECTION - Status
        # ═══════════════════════════════════════════
        
        if SAFE_MODE:
            mode_color = (0, 255, 255)
            mode_text = "🛡️  SAFE MODE"
        else:
            mode_color = (0, 0, 255)
            mode_text = "⚠️  LIVE MODE"
        
        cv2.rectangle(frame, (15, 15), (w-15, 50), mode_color, 2)
        cv2.putText(frame, mode_text, (25, 38), cv2.FONT_HERSHEY_DUPLEX, 0.8, mode_color, 2)
        
        # FPS
        cv2.putText(frame, f"FPS: {actual_fps:.0f}", (w - 150, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        
        # ═══════════════════════════════════════════
        # MIDDLE SECTION - Coordinates & Lock Status
        # ═══════════════════════════════════════════
        
        # Coordinates box
        cv2.rectangle(frame, (15, 60), (w-15, 120), (100, 100, 100), 1)
        cv2.putText(frame, "POSITION", (25, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(frame, f"X: {int(smooth_x)} / {SCREEN_WIDTH}", (25, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 2)
        
        percent_x = (smooth_x / SCREEN_WIDTH) * 100
        cv2.putText(frame, f"({percent_x:.0f}%)", (w - 150, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 255), 1)
        
        # Y Position
        cv2.putText(frame, f"Y: {int(smooth_y)} / {SCREEN_HEIGHT}", (25, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        
        percent_y = (smooth_y / SCREEN_HEIGHT) * 100
        cv2.putText(frame, f"({percent_y:.0f}%)", (w - 150, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 255), 1)
        
        # Lock Status
        cv2.rectangle(frame, (15, 130), (w-15, 180), (100, 100, 100), 1)
        if lock_mode == 'up':
            lock_color = (255, 165, 0)
            lock_text = "🔵 LOCKED: TOP 5%"
        elif lock_mode == 'down':
            lock_color = (255, 100, 0)
            lock_text = "🟠 LOCKED: BOTTOM 5%"
        else:
            lock_color = (0, 255, 0)
            lock_text = "🟢 FREE MODE"
        
        cv2.rectangle(frame, (15, 130), (w-15, 160), lock_color, 2)
        cv2.putText(frame, lock_text, (25, 155), cv2.FONT_HERSHEY_DUPLEX, 0.7, lock_color, 2)
        
        if saved_position is not None:
            cv2.putText(frame, f"Saved: ({int(saved_position[0])}, {int(saved_position[1])})", (25, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        # ═══════════════════════════════════════════
        # SPEED & ZONES
        # ═══════════════════════════════════════════
        
        cv2.rectangle(frame, (15, 190), (w-15, 240), (100, 100, 100), 1)
        
        # Speed Mode
        if current_speed_mode == "SLOW":
            speed_color = (0, 165, 255)
            speed_text = f"🐢 SLOW MODE x{current_speed_scale_x:.2f}"
        else:
            speed_color = (0, 255, 0)
            speed_text = f"🚀 NORMAL MODE x{current_speed_scale_x:.2f}"
        
        cv2.putText(frame, speed_text, (25, 210), cv2.FONT_HERSHEY_DUPLEX, 0.65, speed_color, 2)
        
        # Y Zone
        if current_y_zone == "TOP_EDGE":
            zone_color = (255, 165, 0)
            zone_text = f"📍 Edge: TOP (Y x{SPEED_SCALE_EDGE_Y:.2f})"
        elif current_y_zone == "BOTTOM_EDGE":
            zone_color = (255, 165, 0)
            zone_text = f"📍 Edge: BOTTOM (Y x{SPEED_SCALE_EDGE_Y:.2f})"
        else:
            zone_color = (0, 255, 0)
            zone_text = "📍 Normal Zone"
        
        cv2.putText(frame, zone_text, (25, 230), cv2.FONT_HERSHEY_DUPLEX, 0.65, zone_color, 2)
        
        # ═══════════════════════════════════════════
        # DEVICES STATUS
        # ═══════════════════════════════════════════
        
        cv2.rectangle(frame, (15, 250), (w-15, 310), (100, 100, 100), 1)
        cv2.putText(frame, "DEVICES", (25, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 255), 1)
        
        arduino_status = "✅ OK" if arduino_y_position is not None else "❌ OFFLINE"
        arduino_color = (0, 255, 0) if arduino_y_position is not None else (0, 0, 255)
        cv2.putText(frame, f"Arduino (COM3): {arduino_status}", (25, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.55, arduino_color, 1)
        
        voice_status = "ON" if voice_processing_enabled else "OFF"
        voice_color = (0, 255, 0) if (esp32 and voice_processing_enabled) else (255, 0, 0) if esp32 else (0, 0, 255)
        esp32_text = f"ESP32 Voice: {voice_status}" if esp32 else "ESP32: ❌"
        cv2.putText(frame, esp32_text, (25, 305), cv2.FONT_HERSHEY_SIMPLEX, 0.55, voice_color, 1)
        
        # ═══════════════════════════════════════════
        # CONTROLS HELP
        # ═══════════════════════════════════════════
        
        cv2.rectangle(frame, (15, 320), (w-15, h-15), (100, 100, 100), 1)
        cv2.putText(frame, "CONTROLS", (25, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 255), 1)
        
        cv2.putText(frame, "SPACE: Safe Mode | U: Lock Top | D: Lock Bottom | R: Release", (25, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(frame, "0: Disable Voice | 1: Enable Voice | Q: Quit", (25, 378), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(frame, "Voice: 'up' | 'down' | 'release'", (25, 396), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        
        # ═══════════════════════════════════════════
        # X-AXIS VISUALIZATION BAR
        # ═══════════════════════════════════════════
        
        bar_y = h - 25
        bar_h = 20
        bar_x1 = 50
        bar_x2 = w - 50
        bar_width = bar_x2 - bar_x1
        
        # Bar background
        cv2.rectangle(frame, (bar_x1, bar_y - bar_h), (bar_x2, bar_y), (50, 50, 50), -1)
        
        # Bar border
        cv2.rectangle(frame, (bar_x1, bar_y - bar_h), (bar_x2, bar_y), (100, 100, 100), 1)
        
        # Indicator position
        indicator_x = int(bar_x1 + (smooth_x / SCREEN_WIDTH) * bar_width)
        indicator_color = (0, 255, 0) if current_speed_mode == "NORMAL" else (0, 165, 255)
        
        cv2.circle(frame, (indicator_x, bar_y - bar_h//2), 8, indicator_color, -1)
        cv2.circle(frame, (indicator_x, bar_y - bar_h//2), 10, (255, 255, 255), 1)
        
        cv2.putText(frame, "X-AXIS", (bar_x1 - 40, bar_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 200, 255), 1)
        
        cv2.imshow('Face Control - Press Q to quit or SPACE for Safe Mode', frame)
        
        # Process voice commands
        with voice_lock:
            while voice_command_queue:
                cmd = voice_command_queue.pop(0)
                if cmd == 'U':
                    lock_to_top()
                elif cmd == 'D':
                    lock_to_bottom()
                elif cmd == 'R':
                    release_lock()
        
        # Process terminal commands
        with command_lock:
            while command_queue:
                cmd = command_queue.pop(0)
                if cmd == 'U':
                    lock_to_top()
                elif cmd == 'D':
                    lock_to_bottom()
                elif cmd == 'R':
                    release_lock()
                elif cmd == '0':
                    voice_processing_enabled = False
                    logger.info("🔴 Voice: DISABLED")
                elif cmd == '1':
                    voice_processing_enabled = True
                    logger.info("✅ Voice: ENABLED")
                elif cmd == 'Q':
                    return
                elif cmd == 'SPACE':
                    SAFE_MODE = not SAFE_MODE
                    status = "ENABLED ✅" if SAFE_MODE else "DISABLED ⚠️"
                    logger.info(f"🛡️  Safe Mode: {status}")
        
        # CV2 keyboard
        key = cv2.waitKey(1) & 0xFF
        
        if key == 27 or key == ord('q') or key == ord('Q'):
            break
        elif key == ord(' '):
            SAFE_MODE = not SAFE_MODE
            status = "ENABLED ✅" if SAFE_MODE else "DISABLED ⚠️"
            logger.info(f"🛡️  Safe Mode: {status}")
        elif key == ord('u') or key == ord('U'):
            lock_to_top()
        elif key == ord('d') or key == ord('D'):
            lock_to_bottom()
        elif key == ord('r') or key == ord('R'):
            release_lock()
        elif key == ord('0'):
            voice_processing_enabled = False
            logger.info("🔴 Voice: DISABLED")
        elif key == ord('1'):
            voice_processing_enabled = True
            logger.info("✅ Voice: ENABLED")

# ═══════════════════════════════════════════════════════════════
# 🚀 MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except:
        ctypes.windll.user32.SetProcessDPIAware()
    
    logger.info("=" * 60)
    logger.info("🎯 PNUAI Integrated System - Starting")
    logger.info("=" * 60)
    logger.info("✅ Face Control running in background")
    logger.info("✅ Control Panel ready")
    logger.info("=" * 60)
    
    # Start face control in separate thread
    face_thread = threading.Thread(target=run_face_control, daemon=True)
    face_thread.start()
    
    # Start PyQt6 app
    app = QApplication(sys.argv)
    ex = PNUAI_Assistant()
    ex.show()
    
    sys.exit(app.exec())
    
    # Cleanup
    cap.release()
    cv2.destroyAllWindows()
    
    if arduino:
        arduino.close()
        logger.info("✅ Arduino closed")
    
    if esp32:
        esp32.close()
        logger.info("✅ ESP32 closed")
    
    logger.info("\n✅ System shutdown complete")

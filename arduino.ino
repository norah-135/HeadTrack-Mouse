#include <Wire.h>

const int MPU_addr = 0x68;
int16_t AcX, AcY, AcZ;

// ═══════════════════════════════════════════
// 🔧 القيم المحفوظة من آخر معايرة
// ═══════════════════════════════════════════
// ✅ غيّر هذي القيم بعد ما تسوي معايرة جديدة!
#define SAVED_OFFSET_X -5071
#define SAVED_OFFSET_Z -6150
#define SAVED_MAX_X 8911
#define SAVED_MIN_X -5085
// Range: 13996

// ✅ تفعيل/تعطيل استخدام القيم المحفوظة
#define USE_SAVED_CALIBRATION true  // غيّرها لـ false عشان تعيد المعايرة

// متغيرات التخزين المرجعي
long offX = SAVED_OFFSET_X;
long offZ = SAVED_OFFSET_Z;
long maxX = SAVED_MAX_X;
long minX = SAVED_MIN_X;

// ✨ إعدادات Position Mapping
const int SCREEN_HEIGHT = 1080;  // غيّريها حسب شاشتك
const int DEADZONE_Y = 800;      // ✅ تقليل Deadzone

// ✨ نطاق أوسع للوصول للأطراف
const float RANGE_LIMIT = 0.85;  // 85% بدل 70%

// ✨ Smoothing معتدل
float smooth_position = SCREEN_HEIGHT / 2;
const float SMOOTH_FACTOR = 0.12;

// ✅ متغيرات للإرسال الذكي
int last_sent_position = -1;
const int SEND_THRESHOLD = 3;  // يرسل فقط إذا الفرق أكبر من 3 بكسل

bool calibrated = USE_SAVED_CALIBRATION;  // ✅ يبدأ معاير إذا في قيم محفوظة

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x6B); 
  Wire.write(0); 
  Wire.endTransmission(true);
  delay(1000);
  
  // ✅ عرض حالة المعايرة
  if (USE_SAVED_CALIBRATION) {
    Serial.println("\n╔════════════════════════════════════════╗");
    Serial.println("║  ✅ استخدام قيم معايرة محفوظة        ║");
    Serial.println("╠════════════════════════════════════════╣");
    Serial.print("║  Offset X: "); 
    Serial.print(offX);
    for(int i=0; i<(30-String(offX).length()); i++) Serial.print(" ");
    Serial.println("║");
    
    Serial.print("║  Offset Z: "); 
    Serial.print(offZ);
    for(int i=0; i<(30-String(offZ).length()); i++) Serial.print(" ");
    Serial.println("║");
    
    Serial.print("║  Range: "); 
    long range = maxX - minX;
    Serial.print(range);
    for(int i=0; i<(33-String(range).length()); i++) Serial.print(" ");
    Serial.println("║");
    
    Serial.println("╠════════════════════════════════════════╣");
    Serial.println("║  💡 للمعايرة الجديدة: اضغط C ثم V   ║");
    Serial.println("╚════════════════════════════════════════╝\n");
  } else {
    Serial.println("\n⚠️  القيم المحفوظة معطّلة");
    Serial.println("   سوي معايرة جديدة: C ثم V\n");
  }
  
  printMenu();
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    
    if (cmd == '0') printMenu();
    else if (cmd == 'C' || cmd == 'c') runCenterCalibration();
    else if (cmd == 'V' || cmd == 'v') runVerticalScan();
    else if (cmd == 'P' || cmd == 'p') printCurrentValues();  // ✅ جديد: طباعة القيم الحالية
    
    while(Serial.available() > 0) Serial.read(); 
  }

  if (calibrated) {
    sendVerticalPosition();
  }
  
  delay(10);
}

// ═══════════════════════════════════════════
// ✨ دالة Exponential Curve
// ═══════════════════════════════════════════
float applyExpoCurve(float value, float power) {
  float sign = (value >= 0) ? 1.0 : -1.0;
  return sign * pow(abs(value), power);
}

// ═══════════════════════════════════════════
// ✨ دالة إرسال الموضع (مع إرسال ذكي)
// ═══════════════════════════════════════════
void sendVerticalPosition() {
  readMPU();
  
  float dV = AcX - offX;
  
  // Deadzone
  if (abs(dV) < DEADZONE_Y) {
    dV = 0;
  }
  
  // حساب النطاق
  float rangeV = maxX - minX;
  if (rangeV < 100) rangeV = 5000;
  
  // التطبيع من -1 إلى +1
  float normalizedV = constrain(dV / (rangeV / 2.0), -1.0, 1.0);
  
  // ✅ نطاق أوسع
  normalizedV = constrain(normalizedV, -RANGE_LIMIT, RANGE_LIMIT);
  normalizedV = normalizedV / RANGE_LIMIT;
  
  // ✅ Exponential Curve أقوى للأطراف
  float expoV = applyExpoCurve(normalizedV, 2.0);  // كان 1.8
  
  // عكس الاتجاه
  expoV = -expoV;
  
  // تحويل لموضع Y
  int targetY = (int)(((expoV + 1) / 2.0) * SCREEN_HEIGHT);
  targetY = constrain(targetY, 0, SCREEN_HEIGHT - 1);
  
  // Smoothing
  smooth_position = smooth_position * (1 - SMOOTH_FACTOR) + targetY * SMOOTH_FACTOR;
  
  int positionY = (int)smooth_position;
  positionY = constrain(positionY, 0, SCREEN_HEIGHT - 1);
  
  // ✅ إرسال فقط عند التغيير المحسوس!
  if (last_sent_position == -1 || abs(positionY - last_sent_position) >= SEND_THRESHOLD) {
    Serial.print("Y:");
    Serial.println(positionY);
    last_sent_position = positionY;
  }
}

// ═══════════════════════════════════════════
// المعايرة المركزية
// ═══════════════════════════════════════════
void runCenterCalibration() {
  Serial.println("\n[1/2] جاري تثبيت المركز.. ابقي رأسك ثابتاً تماماً...");
  
  long tx = 0, tz = 0;
  
  for(int i=0; i<150; i++) {
    readMPU();
    tx += AcX; 
    tz += AcZ;
    delay(10);
  }
  
  offX = tx / 150;
  offZ = tz / 150;
  
  // إعادة تعيين
  smooth_position = SCREEN_HEIGHT / 2;
  last_sent_position = -1;
  
  Serial.println("✅ تم التصفير:");
  Serial.print("   Offset X: "); Serial.println(offX);
  Serial.print("   Offset Z: "); Serial.println(offZ);
}

// ═══════════════════════════════════════════
// المسح الرأسي المُحسّن
// ═══════════════════════════════════════════
void runVerticalScan() {
  Serial.println("\n[2/2] جاري المسح الرأسي.. حركي رأسك فوق وتحت بأقصى قوة!");
  Serial.println("⚠️  المطلوب: وصول كامل للأطراف!");
  Serial.println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  
  maxX = -32000; 
  minX = 32000;
  
  long startTime = millis();
  int lastPrintTime = 0;
  
  // ✅ مسح لمدة 8 ثواني
  while(millis() - startTime < 8000) {
    readMPU();
    long currentV = AcX - offX;
    
    bool updated = false;
    
    if(currentV > maxX) {
      maxX = currentV;
      updated = true;
    }
    if(currentV < minX) {
      minX = currentV;
      updated = true;
    }
    
    // ✅ طباعة كل ثانية فقط
    if (millis() - lastPrintTime > 1000) {
      long range = maxX - minX;
      Serial.print("⏱️  ");
      Serial.print((millis() - startTime) / 1000);
      Serial.print("s | Range: ");
      Serial.print(range);
      
      // تقييم النطاق
      if (range < 2000) {
        Serial.println(" ❌ ضعيف - حركي أكثر!");
      } else if (range < 3500) {
        Serial.println(" ⚠️  جيد - ممكن أكثر!");
      } else {
        Serial.println(" ✅ ممتاز!");
      }
      
      lastPrintTime = millis();
    }
    
    delay(10);
  }
  
  long rangeV = maxX - minX;
  
  Serial.println("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  Serial.println("✅ تقرير المسح النهائي:");
  Serial.print("   أقصى فوق (MaxX): "); Serial.println(maxX);
  Serial.print("   أقصى تحت (MinX): "); Serial.println(minX);
  Serial.print("   النطاق الكلي: "); Serial.println(rangeV);
  Serial.println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  
  // ✅ تقييم النطاق
  if (rangeV > 4000) {
    calibrated = true;
    Serial.println("\n🎉 معايرة ممتازة!");
    Serial.println("   ستصلين لكل الشاشة بسهولة!");
  } else if (rangeV > 2500) {
    calibrated = true;
    Serial.println("\n✅ معايرة مقبولة");
    Serial.println("   لكن يُفضّل إعادة المعايرة بحركة أقوى");
  } else {
    calibrated = false;
    Serial.println("\n❌ النطاق ضعيف جداً!");
    Serial.println("   كرري المعايرة (V) وحركي رأسك بقوة أكبر");
  }
  
  // ✅ طباعة القيم للنسخ
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║  📋 انسخي هذي القيم للكود:          ║");
  Serial.println("╠════════════════════════════════════════╣");
  Serial.print("║  #define SAVED_OFFSET_X ");
  Serial.println(offX);
  Serial.print("║  #define SAVED_OFFSET_Z ");
  Serial.println(offZ);
  Serial.print("║  #define SAVED_MAX_X ");
  Serial.println(maxX);
  Serial.print("║  #define SAVED_MIN_X ");
  Serial.println(minX);
  Serial.println("╚════════════════════════════════════════╝");
}

// ═══════════════════════════════════════════
// ✅ طباعة القيم الحالية
// ═══════════════════════════════════════════
void printCurrentValues() {
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║  📊 القيم الحالية:                   ║");
  Serial.println("╠════════════════════════════════════════╣");
  Serial.print("║  Offset X: "); Serial.println(offX);
  Serial.print("║  Offset Z: "); Serial.println(offZ);
  Serial.print("║  Max X: "); Serial.println(maxX);
  Serial.print("║  Min X: "); Serial.println(minX);
  Serial.print("║  Range: "); Serial.println(maxX - minX);
  Serial.println("╠════════════════════════════════════════╣");
  Serial.print("║  Calibrated: ");
  Serial.println(calibrated ? "YES ✅" : "NO ❌");
  Serial.println("╚════════════════════════════════════════╝");
}

// ═══════════════════════════════════════════
// قراءة MPU
// ═══════════════════════════════════════════
void readMPU() {
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_addr, 6, true);
  
  if (Wire.available() >= 6) {
    AcX = Wire.read() << 8 | Wire.read();
    AcY = Wire.read() << 8 | Wire.read();
    AcZ = Wire.read() << 8 | Wire.read();
  }
}

// ═══════════════════════════════════════════
// القائمة
// ═══════════════════════════════════════════
void printMenu() {
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║  Arduino Y-Axis Controller v2.2       ║");
  Serial.println("╠════════════════════════════════════════╣");
  Serial.println("║  C - تصفير المركز (Center)            ║");
  Serial.println("║  V - المسح الرأسي (Vertical)          ║");
  Serial.println("║  P - عرض القيم الحالية                ║");
  Serial.println("║  0 - عرض القائمة                      ║");
  Serial.println("╠════════════════════════════════════════╣");
  Serial.println("║  ✨ Saved Calibration Mode            ║");
  Serial.println("╚════════════════════════════════════════╝");
  Serial.println("\n💡 معايرة جيدة = Range أكبر من 3500");
}

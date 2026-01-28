#include <driver/i2s.h>

#define I2S_SD 25 
#define I2S_WS 26
#define I2S_SCK 27
#define I2S_PORT I2S_NUM_0

const int sample_rate = 16000;
const float record_seconds = 1.7; 
int VAD_THRESHOLD = 1000;           
bool auto_vad_enabled = false;      

void setupI2S() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = sample_rate,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = (i2s_comm_format_t)(I2S_COMM_FORMAT_I2S | I2S_COMM_FORMAT_I2S_MSB),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
    .use_apll = false
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK, 
    .ws_io_num = I2S_WS, 
    .data_out_num = I2S_PIN_NO_CHANGE, 
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
}

void recordAndSend() {
  Serial.println("\n[RECORDING_START]");
  size_t bytesRead;
  long total_samples = sample_rate * record_seconds;
  
  for (long i = 0; i < total_samples; i++) {
    int32_t rawSample;
    i2s_read(I2S_PORT, &rawSample, 4, &bytesRead, portMAX_DELAY);
    int16_t processedSample = (int16_t)((rawSample >> 14) & 0xFFFF);
    Serial.write((uint8_t*)&processedSample, 2);
    delayMicroseconds(2);
  }
  
  Serial.println("\n[RECORDING_END]");
  Serial.flush();

  while (Serial.available() == 0) { 
    delay(10); 
  }
  String result = Serial.readStringUntil('\n');
  Serial.println(">>> AI RESULT: " + result);

  while(Serial.available()) Serial.read(); 
}

void printMenu() {
  Serial.println("\n--- HCI VOICE CONTROL PANEL ---");
  Serial.println("0: Show Menu");
  Serial.println("1: Manual Record (1.5s)");
  Serial.println("2: Toggle Auto VAD (ON/OFF)");
  Serial.println("-------------------------------");
}

void setup() {
  Serial.begin(921600);
  setupI2S();
  printMenu();
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    if (cmd == '0') {
      printMenu();
    } else if (cmd == '1') {
      recordAndSend();
    } else if (cmd == '2') {
      auto_vad_enabled = !auto_vad_enabled;
      Serial.print("Auto VAD Mode: ");
      Serial.println(auto_vad_enabled ? "ACTIVE" : "DISABLED");
    }
  }

  if (auto_vad_enabled) {
    size_t bytesRead;
    int32_t sample = 0;
    i2s_read(I2S_PORT, &sample, 4, &bytesRead, 10);
    if (abs(sample >> 14) > VAD_THRESHOLD) {
      recordAndSend();
    }
  }
}
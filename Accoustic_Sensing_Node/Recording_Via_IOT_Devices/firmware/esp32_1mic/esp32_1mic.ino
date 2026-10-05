void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("--- Chip Info ---");
  
  // Get the full model string (e.g., "ESP32-D0WDQ6")
  Serial.printf("Chip Model: %s\n", ESP.getChipModel());
  
  // Get the number of CPU cores
  Serial.printf("Cores: %d\n", ESP.getChipCores());
  
  // Get the silicon revision number
  Serial.printf("Silicon Revision: %d\n", ESP.getChipRevision());
}

void loop() {}

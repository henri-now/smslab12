// Lab 2 PPG acquisition
// AI assistance: ChatGPT drafted the ADC acquisition and CSV output code.

#include <Arduino.h>
#include <SPI.h>

constexpr int PIN_CLOCK_ENABLE = D0;
constexpr int PIN_ADC_CS = D2;
constexpr int PIN_ADC_RESET = D3;
constexpr int PIN_LED_GREEN = D4;
constexpr int PIN_LED_RED = D5;
constexpr int PIN_LED_IR = D6;
constexpr int PIN_ADC_DRDY = D7;

constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t DRDY_TIMEOUT_US = 20000;

// Starting values for bench testing. Increase the discard count if
// conversions still show a transient after an LED switch.
constexpr int DISCARD_FRAMES = 12;
constexpr int AVERAGE_FRAMES = 8;

const SPISettings adcSpiSettings(2000000, MSBFIRST, SPI_MODE1);

enum class Illumination { Off, Red, Infrared };

void setIllumination(Illumination illumination) {
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_IR, LOW);

  if (illumination == Illumination::Red) {
    digitalWrite(PIN_LED_RED, HIGH);
  } else if (illumination == Illumination::Infrared) {
    digitalWrite(PIN_LED_IR, HIGH);
  }
}

void resetAdc() {
  digitalWrite(PIN_ADC_RESET, LOW);
  delay(1);
  digitalWrite(PIN_ADC_RESET, HIGH);
  delay(10);
}

uint32_t transferWord24() {
  uint32_t word = SPI.transfer(0x00);
  word = (word << 8) | SPI.transfer(0x00);
  return (word << 8) | SPI.transfer(0x00);
}

bool readChannel0(int32_t &sample) {
  const uint32_t started = micros();
  while (digitalRead(PIN_ADC_DRDY) == HIGH) {
    if (micros() - started >= DRDY_TIMEOUT_US) {
      return false;
    }
    delayMicroseconds(5);
  }

  // Reset-default frame: response, channel 0, channel 1, output CRC.
  // Sending zeros issues a NULL command.
  SPI.beginTransaction(adcSpiSettings);
  digitalWrite(PIN_ADC_CS, LOW);
  transferWord24();
  const uint32_t raw = transferWord24();
  transferWord24();
  transferWord24();
  digitalWrite(PIN_ADC_CS, HIGH);
  SPI.endTransaction();

  sample = static_cast<int32_t>(raw);
  if (raw & 0x800000u) {
    sample -= 0x1000000;
  }
  return true;
}

bool measureSlot(Illumination illumination, int32_t &mean) {
  setIllumination(illumination);

  int32_t sample;
  for (int i = 0; i < DISCARD_FRAMES; ++i) {
    if (!readChannel0(sample)) {
      return false;
    }
  }

  int32_t sum = 0;
  for (int i = 0; i < AVERAGE_FRAMES; ++i) {
    if (!readChannel0(sample)) {
      return false;
    }
    sum += sample;
  }

  mean = sum / AVERAGE_FRAMES;
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);

  pinMode(PIN_ADC_CS, OUTPUT);
  digitalWrite(PIN_ADC_CS, HIGH);
  pinMode(PIN_ADC_RESET, OUTPUT);
  digitalWrite(PIN_ADC_RESET, HIGH);
  pinMode(PIN_ADC_DRDY, INPUT);

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_IR, OUTPUT);
  digitalWrite(PIN_LED_GREEN, LOW);
  setIllumination(Illumination::Off);

  SPI.begin(D8, D9, D10, PIN_ADC_CS);

  pinMode(PIN_CLOCK_ENABLE, OUTPUT);
  digitalWrite(PIN_CLOCK_ENABLE, HIGH);
  delay(5);
  resetAdc();

  // Give the USB serial interface time to appear after reset.
  delay(1000);
  Serial.println("time_ms,off,red,ir,red_minus_off,ir_minus_off");
}

void loop() {
  const uint32_t timeMs = millis();
  int32_t off;
  int32_t red;
  int32_t ir;

  if (!measureSlot(Illumination::Off, off) ||
      !measureSlot(Illumination::Red, red) ||
      !measureSlot(Illumination::Infrared, ir)) {
    setIllumination(Illumination::Off);
    Serial.println("# ADC DRDY timeout");
    resetAdc();
    delay(250);
    return;
  }

  setIllumination(Illumination::Off);
  Serial.printf("%lu,%ld,%ld,%ld,%ld,%ld\n",
                static_cast<unsigned long>(timeMs),
                static_cast<long>(off),
                static_cast<long>(red),
                static_cast<long>(ir),
                static_cast<long>(red - off),
                static_cast<long>(ir - off));
}
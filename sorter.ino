/*
  sorter.ino – Reject Arm Controller
  AI-Driven Packaging Line Defect Sorter

  Board:  Arduino Uno / Nano / Mega
  Serial: 9600 baud

  Protocol (single ASCII character from the PC):
    '1'  -> REJECT: servo pushes rod UP to block the package,
            holds briefly, then returns DOWN.
    '0'  -> PASS: servo stays at home (no action).
    'T'  -> TEST: blink LED and do a quick servo sweep (for connection test).

  WIRING:
    Servo signal (orange/yellow)  -> Arduino pin 9
    Servo power  (red)            -> External 5V supply (+)
    Servo ground (brown/black)    -> External supply (-)  AND  Arduino GND
    Status LED                    -> Arduino pin 13 (built-in) or external

    IMPORTANT: Use an external 5V supply for the servo, NOT the Arduino 5V pin.
    Add a 470–1000µF capacitor across the servo power wires to prevent resets.

  UPLOAD:
    1. Open this file in Arduino IDE
    2. Tools > Board > Arduino Uno
    3. Tools > Port > (your COM port)
    4. Upload
    5. Close Serial Monitor before starting main.py
*/

#include <Servo.h>

// ── Pin Configuration ──
const uint8_t  SERVO_PIN     = 9;
const uint8_t  LED_PIN       = 13;

// ── Servo Angles ──
const int      HOME_ANGLE    = 0;     // resting position (rod DOWN)
const int      PUSH_ANGLE    = 90;    // blocking position (rod UP)

// ── Timing ──
const uint16_t PUSH_HOLD_MS  = 500;   // how long the rod stays up (ms)
const uint8_t  OUT_STEP_MS   = 4;     // ms per degree going up  (faster push)
const uint8_t  BACK_STEP_MS  = 6;     // ms per degree coming back (gentle return)

Servo pusher;
bool rejectActive = false;

// Smooth servo sweep – prevents current spikes
void sweep(int from, int to, uint8_t stepDelayMs) {
  const int dir = (to > from) ? 1 : -1;
  for (int angle = from; angle != to; angle += dir) {
    pusher.write(angle);
    delay(stepDelayMs);
  }
  pusher.write(to);
}

// REJECT sequence: rod UP, hold, rod DOWN
void rejectPackage() {
  rejectActive = true;
  digitalWrite(LED_PIN, HIGH);       // LED ON during reject

  sweep(HOME_ANGLE, PUSH_ANGLE, OUT_STEP_MS);   // rod goes UP
  delay(PUSH_HOLD_MS);                            // hold to block package
  sweep(PUSH_ANGLE, HOME_ANGLE, BACK_STEP_MS);  // rod comes DOWN

  digitalWrite(LED_PIN, LOW);        // LED OFF
  rejectActive = false;
}

// TEST sequence: quick blink + mini sweep
void testConnection() {
  // Blink LED 3 times
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(100);
    digitalWrite(LED_PIN, LOW);
    delay(100);
  }
  // Mini sweep
  sweep(HOME_ANGLE, 45, OUT_STEP_MS);
  delay(200);
  sweep(45, HOME_ANGLE, BACK_STEP_MS);
}

void setup() {
  Serial.begin(9600);
  pinMode(LED_PIN, OUTPUT);
  pusher.attach(SERVO_PIN);
  pusher.write(HOME_ANGLE);
  delay(500);  // let servo reach home position

  // Startup indication
  digitalWrite(LED_PIN, HIGH);
  delay(200);
  digitalWrite(LED_PIN, LOW);

  Serial.println("SORTER_READY");
}

void loop() {
  if (Serial.available() > 0) {
    const char cmd = Serial.read();

    switch (cmd) {
      case '1':  // REJECT
        if (!rejectActive) {
          rejectPackage();
          Serial.println("REJECTED");
        }
        break;

      case '0':  // PASS (no action needed)
        Serial.println("PASSED");
        break;

      case 'T':  // TEST connection
      case 't':
        testConnection();
        Serial.println("TEST_OK");
        break;

      // Ignore newlines and other characters
      default:
        break;
    }
  }
}

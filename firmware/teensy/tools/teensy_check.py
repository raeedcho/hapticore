import time
import serial

s = serial.Serial("/dev/ttyACM0", 115200)

def send(cmd: str, wait: float = 0.0) -> None:
    s.write(f"{cmd}\n".encode())
    time.sleep(wait)

# 1. Camera, scope pin 3: 100 µs pulses every 16.67 ms, then every 8.33 ms; LOW after T0
send("C60"); send("T1", 3.0); send("C120", 3.0); send("T0", 0.5)

# 2. Reward vs. strobe, scope pins 4 and 22: strobe lands ~300 ms into a 1 s pulse
send("R1000", 0.3); send("E42", 1.5)

# 3. Overlap, pin 4: one 1 s pulse, no second pulse
send("R1000", 0.3); send("R1000", 2.0)

# 4. Maximum duration, pin 4: a 10 s pulse that ends on time
send("R10000", 11.0)
s.close()
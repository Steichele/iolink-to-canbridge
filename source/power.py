# ------------------------------------------------------------
# Imports
# ------------------------------------------------------------
import socket
import time
import logging
import struct
import re
from threading import Thread
from can import Bus, Message, CanError


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("steuerung")


# ------------------------------------------------------------
# Klasse
# ------------------------------------------------------------
class Steuerung:
    def __init__(self):

        # Netzteil
        self.IP = "192.168.1.20"
        self.PORT = 9221
        self.CHANNEL = 1

        self.SET_VOLTAGE = 28.0
        self.SET_CURRENT = 2.5

        # CAN
        self.CAN_INTERFACE = "socketcan"
        self.CAN_CHANNEL = "vcan0"
        self.BAUDRATE = 250000
        self.CAN_ID_POWER = 0x900

        # runtime
        self.psu = None
        self.bus = None

        # Threads
        self.running = True


    # --------------------------------------------------------
    # PSU
    # --------------------------------------------------------
    def send_psu_cmd(self, cmd: str):
        line = (cmd + "\r\n").encode("ascii")
        self.psu.sendall(line)


    def read_line(self):
        time.sleep(0.05)
        return self.psu.recv(64).decode().strip()


    def parse_value(self, s: str) -> float:
        return float(re.findall(r"[-+]?\d*\.\d+|\d+", s)[0])


    def read_psu_values(self):
        try:
            self.send_psu_cmd(f"V{self.CHANNEL}O?")
            voltage = self.parse_value(self.read_line())

            self.send_psu_cmd(f"I{self.CHANNEL}O?")
            current = self.parse_value(self.read_line())

            return voltage, current

        except Exception as e:
            log.error("PSU Read Fehler: %s", e)
            return 0.0, 0.0


    # --------------------------------------------------------
    # CAN
    # --------------------------------------------------------
    def send_can(self, voltage, current):

        v = int(max(0, min(0xFFFF, round(voltage * 1000))))
        i = int(max(0, min(0xFFFF, round(current * 1000))))

        data = struct.pack("<HH", v, i)

        try:
            msg = Message(
                arbitration_id=self.CAN_ID_POWER,
                data=data,
                is_extended_id=False
            )
            self.bus.send(msg)

            log.info("CAN: U=%dmV I=%dmA", v, i)

        except CanError as e:
            log.error("CAN Fehler: %s", e)


    # --------------------------------------------------------
    # THREAD 1 → MESSEN + SENDEN (100 ms)
    # --------------------------------------------------------
    def thread_measure(self):
        log.info("Mess-Thread gestartet (100 ms)")

        while self.running:
            start = time.time()

            voltage, current = self.read_psu_values()
            self.send_can(voltage, current)

            # exakt 100 ms Zyklus
            dt = time.time() - start
            sleep_time = max(0.1 - dt, 0)
            time.sleep(sleep_time)


    # --------------------------------------------------------
    # THREAD 2 → POWER ON (alle 10 s)
    # --------------------------------------------------------
    def thread_power(self):
        log.info("Power-Thread gestartet (10 s)")

        while self.running:
            try:
                self.send_psu_cmd(f"V{self.CHANNEL} {self.SET_VOLTAGE:.3f}")
                self.send_psu_cmd(f"I{self.CHANNEL} {self.SET_CURRENT:.3f}")
                self.send_psu_cmd(f"OP{self.CHANNEL} 1")

                log.info("Power ON sichergestellt")

            except Exception as e:
                log.error("Power Fehler: %s", e)

            time.sleep(10)


    # --------------------------------------------------------
    # Main
    # --------------------------------------------------------
    def run(self):

        try:
            # CAN
            self.bus = Bus(
                interface=self.CAN_INTERFACE,
                channel=self.CAN_CHANNEL,
                bitrate=self.BAUDRATE
            )
            log.info("CAN bereit")

            # PSU
            self.psu = socket.create_connection(
                (self.IP, self.PORT),
                timeout=3.0
            )
            log.info("PSU verbunden")

            # Threads starten
            t1 = Thread(target=self.thread_measure, daemon=True)
            t2 = Thread(target=self.thread_power, daemon=True)

            t1.start()
            t2.start()

            # Hauptthread läuft einfach weiter
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            log.warning("Abbruch")
            self.running = False

        finally:
            if self.psu:
                self.psu.close()
            if self.bus:
                self.bus.shutdown()
            log.info("Beendet")


# ------------------------------------------------------------
# Start
# ------------------------------------------------------------
if __name__ == "__main__":
    Steuerung().run()
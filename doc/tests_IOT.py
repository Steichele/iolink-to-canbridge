#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IO-Link Multiturn Sensor Testscript
für IFM AL1342 IO-Link Master

Funktionen:
- Lesen/Schreiben von ISDU (Index/Subindex)
- Lesen der Prozessdaten
- Lesen von Events
- Negativtests (ungültige Werte prüfen)
"""

import requests
import time
import json

MASTER_IP = "192.168.1.250"      # <- AL1342 IP eintragen
PORT = 8080
PORT_CHANNEL = 1                # IO-Link Port, an dem der Sensor hängt

BASE_URL = f"http://{MASTER_IP}:{PORT}/iolink/v1"


# ----------------------------------------------------------
#  BASIS-FUNKTIONEN AL1342
# ----------------------------------------------------------

def read_isdu(index, subindex=0):
    url = f"{BASE_URL}/master/port[{PORT_CHANNEL}]/device/isdu/read"
    payload = {"index": index, "subindex": subindex}
    r = requests.post(url, json=payload)
    r.raise_for_status()
    return r.json()["value"]


def write_isdu(index, subindex, value):
    url = f"{BASE_URL}/master/port[{PORT_CHANNEL}]/device/isdu/write"
    payload = {"index": index, "subindex": subindex, "value": value}
    r = requests.post(url, json=payload)
    r.raise_for_status()
    return r.json()


def read_processdata():
    url = f"{BASE_URL}/master/port[{PORT_CHANNEL}]/device/pdin/getdata"
    r = requests.get(url)
    r.raise_for_status()
    data_hex = r.json()["data"]
    # Hexstring → Bytes
    pd = bytes.fromhex(data_hex)
    return pd


def read_events():
    url = f"{BASE_URL}/master/events"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()["events"]


# ----------------------------------------------------------
#  HILFSFUNKTIONEN (für Multiturn Sensor)
# ----------------------------------------------------------

def decode_a10_processdata(pd):
    """
    Byte 0: Status/SSC
    Byte 1: Scaling Factor
    Byte 2–5: Measurement Value (Position)
    """
    status = pd[0]
    scaling = int.from_bytes(pd[1:2], "little", signed=True)
    position_raw = int.from_bytes(pd[2:6], "little", signed=True)

    ssc1 = status & 0x01
    ssc2 = (status >> 1) & 0x01

    return {
        "status": status,
        "ssc1": ssc1,
        "ssc2": ssc2,
        "scaling": scaling,
        "position_raw": position_raw,
        "position_deg": position_raw * (10 ** scaling)
    }


# ----------------------------------------------------------
#  NEGATIVTESTS
# ----------------------------------------------------------

INVALID_VALUES_POSITION = {
    0x7FFFFFFC,  # General error
    0x7FFFFFF8,  # Out-of-range (+)
    0x80000008,  # Out-of-range (-)
}

def is_invalid_position(value):
    return value in INVALID_VALUES_POSITION


# ----------------------------------------------------------
#  TESTCASE-FUNKTIONEN (TC-01…TC-13)
# ----------------------------------------------------------

def test_tc08_diagnosis_suppression():
    """
    Ungültige Werte:
    SI1 gültig: 0..2 → ungültig: >=3
    SI2 gültig: 0..1 → ungültig: >=2
    """
    print("\n--- TC-08: Diagnosis Suppression Test ---")

    print("Teste ungültige Werte…")

    invalid_values_si1 = [3, 4, 99, -1]
    invalid_values_si2 = [2, 3, 99, -1]

    for v in invalid_values_si1:
        try:
            write_isdu(0x0045, 1, v)
            print(f"❌ Fehler: SI1 akzeptierte ungültigen Wert {v}")
        except:
            print(f"✔️ Korrekt: SI1 verweigerte ungültigen Wert {v}")

    for v in invalid_values_si2:
        try:
            write_isdu(0x0045, 2, v)
            print(f"❌ Fehler: SI2 akzeptierte ungültigen Wert {v}")
        except:
            print(f"✔️ Korrekt: SI2 verweigerte ungültigen Wert {v}")


def test_tc12_processdata():
    print("\n--- TC-12: Prozessdatenprüfung ---")

    for i in range(10):
        pd = read_processdata()
        d = decode_a10_processdata(pd)

        print(f"Status={hex(d['status'])}, PosRaw={d['position_raw']}")

        if is_invalid_position(d["position_raw"]):
            print("❌ Ungültiger Wert erkannt – wird NICHT verarbeitet")
        else:
            print(f"✔️ Gültige Position: {d['position_deg']:.2f}°")

        time.sleep(0.2)


def test_read_events():
    print("\n--- IO-Link Events aus AL1342 ---")
    events = read_events()
    for e in events:
        print(f"Event: {e['eventcode']} - {e['text']}")


# ----------------------------------------------------------
#  MAIN
# ----------------------------------------------------------

if __name__ == "__main__":

    print("Starte IO-Link Testscript für Novotechnik Multiturn + IFM AL1342…")

    # Beispiel: TC‑08 Negativtest
    test_tc08_diagnosis_suppression()

    # Beispiel: TC‑12 Prozessdatencheck
    test_tc12_processdata()

    # Events lesen
    test_read_events()

    print("\nFertig!")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PySOEM + EL6224 Demo (PCI-Adapter, Python 3.7 kompatibel)
- Adapterliste
- PCI-NIC wählen
- Slaves scannen, EL6224 finden
- SDO lesen: 0x1008:00 (Device Name), 0x1018:01 (Vendor ID)
- Neu: pro Kanal (1..4) einen 'Wert' finden (OD durchsuchen) und drucken
"""

import pysoem
import platform
import argparse
from typing import List, Tuple


# --------- Adapter ---------

def find_adapters_normalized() -> List[Tuple[str, str]]:
    adapters = pysoem.find_adapters()
    def to_str(x):
        return x.decode(errors='ignore') if isinstance(x, (bytes, bytearray)) else str(x)
    return [(to_str(a.name), to_str(a.desc)) for a in adapters]

def _norm(s: str) -> str:
    return s.lower() if isinstance(s, str) else str(s).lower()

def choose_pci_adapter_interactive(ad_list: List[Tuple[str, str]]) -> str:
    if not ad_list:
        raise RuntimeError("Keine Adapter gefunden (pysoem.find_adapters()).")
    EXCLUDE = ("wi-fi", "wifi", "wireless", "wlan", "bluetooth", "loopback", "virtual", "fortinet")
    candidates = []
    for name, desc in ad_list:
        if any(k in _norm(name) or k in _norm(desc) for k in EXCLUDE):
            continue
        candidates.append((name, desc))
    if not candidates:
        candidates = ad_list

    STRONG_PREF = ("twincat-intel", "pci")
    WEAK_PREF   = ("intel", "ethernet", "gbit", "gigabit", "gbe")
    # 1) NPF + starke Präferenz
    for name, desc in candidates:
        if r"\\device\\npf_" in _norm(name) and any(p in _norm(name) or p in _norm(desc) for p in STRONG_PREF):
            return name
    # 2) NPF + schwache Präferenz
    for name, desc in candidates:
        if r"\\device\\npf_" in _norm(name) and any(p in _norm(name) or p in _norm(desc) for p in WEAK_PREF):
            return name
    # 3) Windows: irgendein NPF
    if platform.system().lower().startswith("win"):
        for name, _ in candidates:
            if r"\\device\\npf_" in _norm(name):
                return name
    # 4) Interaktiv
    print("\nMehrere mögliche NICs gefunden. Bitte Auswahl eingeben:")
    for i, (name, desc) in enumerate(candidates):
        print(f"  [{i}] name='{name}'  desc='{desc}'")
    try:
        idx = int(input("Index (Zahl) eingeben: ").strip())
        if 0 <= idx < len(candidates):
            return candidates[idx][0]
    except Exception:
        pass
    # 5) Fallback
    return candidates[0][0]


# --------- CoE/SDO-Helfer ---------

def read_sdo_string(slave, index: int, subindex: int) -> str:
    raw = slave.sdo_read(index, subindex)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("ascii", errors="ignore")

def read_sdo_udint(slave, index: int, subindex: int) -> int:
    raw = slave.sdo_read(index, subindex)
    return int.from_bytes(raw, byteorder="little", signed=False)

def read_first_data_subindex(slave, index: int) -> Tuple[int, bytes]:
    """
    Liest vom gegebenen 'index' den ersten Subindex mit Daten (data_type>0, bit_length>0).
    Gibt (subindex, raw_bytes) zurück. Wir lesen die Rohdaten per sdo_read(index, subindex).
    """
    try:
        od = slave.od
    except pysoem.SdoInfoError:
        # Kein SDO-Info -> probiere Standard: Subindex 1
        return 1, slave.sdo_read(index, 1)
    for obj in od:
        if obj.index == index:
            for si, entry in enumerate(obj.entries):
                if entry.data_type > 0 and entry.bit_length > 0:
                    return si, slave.sdo_read(index, si)
    # Fallback: Subindex 1
    return 1, slave.sdo_read(index, 1)

def find_channel_indices(slave) -> List[int]:
    """
    Sucht heuristisch bis zu 4 'kanalbezogene' Indizes im OD.
    Bevorzugt Bereiche und Namen, die typischerweise Inputs/kanalspezifische Daten halten
    (z. B. 0x6000… für Eingangs-PDOs oder 0x8000… für Kanalparameter).
    Die EL6224-Doku beschreibt CoE-Objekte und die Einordnung; hier wählen wir pragmatisch.  # siehe Quellen
    """
    indices = []
    try:
        od = slave.od
    except pysoem.SdoInfoError:
        return indices

    def score_obj(obj) -> int:
        # Heuristik: Kanal/Port/Input im Namen? Bereich 0x6000..0x60FF oder 0x8000..?
        name_l = _norm(obj.name or "")
        sc = 0
        if "input" in name_l or "port" in name_l or "channel" in name_l or "ch" in name_l:
            sc += 2
        idx = obj.index
        if 0x6000 <= idx <= 0x60FF:
            sc += 3  # Eingangs-PDOs
        if 0x8000 <= idx <= 0x8FFF:
            sc += 2  # Kanalparameterbereich
        # Mindestens ein datentragender Eintrag?
        if any(e.data_type > 0 and e.bit_length > 0 for e in obj.entries):
            sc += 1
        return sc

    scored = sorted([obj for obj in od], key=lambda o: (-score_obj(o), o.index))
    for obj in scored:
        if len(indices) >= 4:
            break
        # nur Objekte mit Daten
        if any(e.data_type > 0 and e.bit_length > 0 for e in obj.entries):
            # vermeide reine Identitäts-/System-Objekte
            if obj.index not in (0x1000, 0x1008, 0x1018):
                indices.append(obj.index)

    # Falls Heuristik zu wenig geliefert hat, ergänze Standard-Kandidaten
    while len(indices) < 4:
        # 0x6000, 0x6001, 0x6002, 0x6003 sind in vielen Slaves Input-PDOs pro Kanal
        cand = 0x6000 + len(indices)
        if cand not in indices:
            indices.append(cand)
        else:
            break

    return indices[:4]


def format_value(raw: bytes) -> str:
    """
    Versucht eine sinnvolle Darstellung:
    - als hex-String
    - als little-endian integer (bis 8 Bytes)
    - für kurze Strings: ASCII-Teil vor erster 0x00
    """
    hexstr = raw.hex(" ")
    out = f"hex[{hexstr}]"
    if 1 <= len(raw) <= 8:
        out += f", intLE[{int.from_bytes(raw, 'little', signed=False)}]"
    if len(raw) >= 2:
        txt = raw.split(b"\x00", 1)[0]
        try:
            s = txt.decode("ascii")
            if s:
                out += f", ascii['{s}']"
        except Exception:
            pass
    return out


# --------- Hauptprogramm ---------

def main():
    parser = argparse.ArgumentParser(description="PySOEM EL6224 Demo (Kanalwerte)")
    parser.add_argument("--prefer", type=str, default=None, help="Optionaler Adapter-Filter (z. B. 'Intel').")
    args = parser.parse_args()

    # Adapterliste
    ad_list = find_adapters_normalized()
    print("Verfügbare Adapter:")
    for i, (name, desc) in enumerate(ad_list):
        print(f"  [{i}] name='{name}'  desc='{desc}'")

    # PCI wählen (+ optionaler Benutzer-Filter)
    ifname = choose_pci_adapter_interactive(ad_list)
    if args.prefer:
        pref = args.prefer.lower()
        for name, desc in ad_list:
            if pref in _norm(name) or pref in _norm(desc):
                ifname = name
                break
    print(f"\nGewählter Adapter: {ifname}\n")

    master = pysoem.Master()
    master.open(ifname)

    try:
        if master.config_init() <= 0:
            raise RuntimeError("Keine EtherCAT-Slaves gefunden – ist die Linie aktiv/verkabelt?")

        print("Gefundene Slaves:")
        for i, s in enumerate(master.slaves):
            print(f"  [{i}] '{s.name}'")

        # EL6224 suchen
        pos = None
        for i, s in enumerate(master.slaves):
            if "EL6224" in s.name:
                pos = i
                break
        if pos is None:
            print("Keine EL6224 gefunden – nehme ersten Slave.")
            pos = 0

        el = master.slaves[pos]
        print(f"\nZiel-Slave: Position {pos}, Name '{el.name}'\n")

        # Standard-SDOs
        dev_name  = read_sdo_string(el, 0x1008, 0)
        vendor_id = read_sdo_udint(el, 0x1018, 1)
        print(f"SDO 0x1008:00 (Device Name): {dev_name}")
        print(f"SDO 0x1018:01 (Vendor ID)  : {vendor_id}")

        # Bus (optional) in OP
        master.state = pysoem.OP_STATE
        master.write_state()
        master.state_check(pysoem.OP_STATE, 50000)
        print("\nBus in OP-State (optional).")

        # --- NEU: Kanalwerte für CH1..CH4 finden & lesen ---
        print("\nKanalwerte (heuristisch aus OD):")
        ch_indices = find_channel_indices(el)   # bis zu 4 Indizes
        for ch, idx in enumerate(ch_indices, start=1):
            try:
                si, raw = read_first_data_subindex(el, idx)
                print(f"  Kanal {ch}: Index 0x{idx:04X}, Sub {si} -> {format_value(raw)}")
            except pysoem.SdoError as e:
                print(f"  Kanal {ch}: Index 0x{idx:04X} -> SDO-Fehler: {e}")
            except Exception as e:
                print(f"  Kanal {ch}: Index 0x{idx:04X} -> Fehler: {e}")

    finally:
        try:
            master.state = pysoem.INIT_STATE
            master.write_state()
            master.state_check(pysoem.INIT_STATE, 5000)
        except Exception:
            pass
        master.close()
        print("\n Master geschlossen.")


if __name__ == "__main__":
    main()
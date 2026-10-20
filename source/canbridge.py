#!/usr/bin/python3

"""! Python Bridge for IFM IO-Link Master to Meassure over Modbus and send over Can.

   The information regarding usage of the script can be found in the readme.md in this folder.
   Information regarding installation and usage of Python 3 and the virtual environment can be found here:
   https://novo-wiki/xwiki/bin/view/Wissens-Db/Entwicklung/Tools/Python/

   Copycw Novotechnik Messwertaufnehmer OHG - All cws Reserved, June 2023
   Unauthorized copying of this file, via any medium is strictly prohibited.
   Proprietary and confidential.
 
 @section authors Author(s)
 - Created by %auteur% on %date%.
 @section Copyright 
   Copyright Novotechnik Messwertaufnehmer OHG - All Rights Reserved, February 2023
   Unauthorized copying of this file, via any medium is strictly prohibited.
   Proprietary and confidential. 

"""

'''
Notizen:
Bezug von Kanaele auf Register Ch1 = 1002 , Ch2 = 2002 usw . . 
Diagnosedaten 1001 ,2001. 
Diagnosedaten und Events 30 = ch1 ,40 =ch2, 100= ch8 | Seite 83
199 299 Statusinformationen
'''



__author__ = "Markus Steichele"
__credits__ = ["Markus Steichele"]
__version__ = "1.0.0"
__maintainer__ = "QE "
__email__ = "markus.steichele@novotechnik.de"
__status__ = "WIP"


import time
from datetime import datetime, timedelta
import time
from pyModbusTCP.client import ModbusClient
import xml.etree.ElementTree as ET
import struct
import can
from threading import Thread, Lock
from flask import Flask, render_template, request
from flask import Flask, send_file
from flask import Flask, make_response
from flask_socketio import SocketIO
from flask_caching import Cache
from waitress import serve
import subprocess
import psutil
from cantools.database import Database
from cantools.database.can import Message
from cantools.database.can.signal import Signal
from cantools.database.conversion import LinearConversion
import yaml




## @class Canbridge
#  @brief Hauptklasse zur Verwaltung einer CAN‑Bridge zwischen IO‑Link‑Modulen und einem CAN‑Netzwerk.
#  @details Diese Klasse übernimmt:
#   - Modbus‑Kommunikation zu IO‑Link‑Modulen  
#   - Auslesen von Prozess‑, Diagnose‑ und Eventregistern  
#   - Identifikationsobjekte (acyclic read)  
#   - Erstellung von DBC‑Dateien  
#   - CAN‑Senden aller gesammelten Daten  
class Canbridge:
    
    ## @brief Konstruktor der CAN‑Bridge.
    #  @details Initialisiert:
    #   - CAN‑Hardwareparameter  
    #   - Registerlisten  
    #   - Modbus‑Timeouts  
    #   - Verbindungs‑Cache  
    #   - DBC‑Dateipfade  
    def __init__(self):
        self.canhw = 'socketcan'
        self.channel = 'vcan0'
        self.bitrate = (250000) 
        self.id = 700
        self.startid = 800
        self.maxchn = 8
        self.subnet = "192.168.1."
        self.startip = 250
        self.channelspermodul = 8 #Kanäle / Modul
        self.module = 2#Modulanzahl
        self.iddict = {}
        self.maxid = None
        self.iplist = []
        self.messregister = [1002, 2002, 3002, 4002, 5002, 6002, 7002, 8002] # Messregister ohne PQI info. Obsolete!
        self.diagregister = [1001, 2001, 3001, 4001, 5001, 6001, 7001, 8001] # Beinhaltet PQI & Messregister  
        self.eventregister = [30, 40, 50, 60, 70, 80, 90, 100] 
        self.busv = can.interface.Bus(bustype='socketcan', channel='vcan0', bitrate=self.bitrate) #can.interface.Bus('virtual',bustype='virtual')
        self.masterIP = "192.168.1.250"
        self.port = 502
        self.iodevice = None#ModbusClient(host=self.masterIP, port=self.port, auto_open=True, timeout = 0.02)
        self.running = False
        self.cycle = 1
        self.dlfileTemp = "static/Canbride.dbc" 
        # self.dlfileTemp = "/home/pi/mycanlogger/config/config.dbc" # test workflow handling 
        self.dlfile = "uploads/Canbride.dbc"
        self.modbus_lock = Lock() # Vorbereitung
        self.pause_modbus = 0.01
        self.lastip = self.masterIP
        self.connection_cache = {}
        self.status = None
        self.counter = 10000
        self.deactivadedID = []

      
    ## @brief Konvertiert Integerwerte in Bytes.
    #  @param values Liste von Integerwerten.
    #  @return Liste aus Bytewerten.
    #  @throws ValueError falls Werte außerhalb des gültigen Bereichs liegen.
    def int_to_bytes(self, values):
        if len(values) != 4:
            raise ValueError("Die Liste muss genau zwei Werte enthalten.")
        
        for value in values:
            if not (0 <= value < 2**32):
                raise ValueError("Jeder Wert muss zwischen 0 und 2^32-1 liegen.")
        
        byte_list = []
        for value in values:
            byte_data = struct.pack('<H', value)
            byte_list.extend(byte_data)
        return byte_list

    ## @brief Wählt je nach Register die relevanten Werte aus.
    #  @param valuelist Eingelesene Modbus‑Registerdaten.
    #  @param register Modbus‑Registeradresse.
    #  @return Gefilterte Liste.
    def register_range(self,valuelist, register):
        if register >= 101:
            valuelist = valuelist[0:4]
        else:
            valuelist = [valuelist[0], valuelist[1], valuelist[3], valuelist[5]]
        return(valuelist)

    ## @brief Holt oder erzeugt eine cached Modbus‑TCP‑Verbindung.
    #  @param ip Modul‑IP.
    #  @return ModbusClient‑Instanz.
    def get_connection(self, ip):
        if ip not in self.connection_cache:
            self.connection_cache[ip] = ModbusClient(host=ip, port=self.port, auto_open=True, timeout= self.cycle * 1.2)
        return self.connection_cache[ip]

    ## @brief Liest Prozess‑ oder Diagnosedaten eines IO‑Link‑Ports aus.
    #  @details Beinhaltet PQI‑Prüfung, rekursives Auslesen und Fehlerbehandlung.
    #  @param id Identifikationsnummer / CAN‑ID.
    #  @param retry Retry‑Zähler.
    def iodata(self, id, retry=0):
        ''' Ließt das Prozessdatenregister bis PQI Byte einen Fehler
        meldet. Dann wird self.detect_thread_io_data() Aufgerufen um die Events zu lesen
        '''
        valuelist = None
        ip = self.iddict[id][0]
        register = self.iddict[id][1]
        iodevice = self.get_connection(ip)
        try:
            if not register in self.eventregister or retry is False :
                with self.modbus_lock: # Vorbereitung
                    valuelist = iodevice.read_holding_registers(register, 6)
                    if valuelist is not None:
                        if not any(getattr(item, "isError", lambda: False)() for item in valuelist): 
                            valuelist = self.register_range(valuelist, register)
                            self.iddict[id][2] = self.int_to_bytes(valuelist)
                            if valuelist[0] >= 2: # PQI Check
                                evthread = Thread(target=self.iodata, args=(id+8, False,))
                                evthread.start()
                        else:
                            print("valuelist is Error")
                            valuelist = [255, 255, 255, 255, 255, 255, 255, 255]
                            self.iddict[id][2] = valuelist
                    else:
                        failid = self.startid - 1
                        # ID sicher auf 16 Bit begrenzen
                        elog_id = id & 0xFFFF  
                        elog = self.int_to_bytes([elog_id, 0, 0, 0])
                        self.iddict[failid][2] = elog

        except Exception as e:
            failid = self.startid - 1
            # ID sicher auf 16 Bit begrenzen
            elog_id = id & 0xFFFF  
            elog = self.int_to_bytes([elog_id, 0, 0, 0])
            self.iddict[failid][2] = elog
            print(e)

        
    ## @brief Führt paralleles Auslesen aller IO‑Daten durch.
    #  @param detect Wenn True, alternativer Thread‑Startmodus.
    def thread_io_data(self, detect = False):
        for id in self.iddict.keys():
            if not id in self.deactivadedID:
                if detect == False:
                    rv = Thread(target=self.iodata, args=(id,))
                else:
                    # wird nur einmalig bei der initialisierung ausgeführt um den Portstatus ( belegt, nicht belegt, health ) zu bekommen
                    rv = Thread(target=self.iodata, args=(id, False,))
                rv.start()
                time.sleep(self.pause_modbus)
    
    ## @brief Erstellt IP‑Liste aus Subnetz, Startadresse & Modulanzahl.
    def setip(self):
        for ip in range(0, self.module):
            modulip = self.startip + ip
            self.iplist.append(self.subnet + str(modulip))

            
    ## @brief Baut vollständiges ID‑Mapping (ID → IP → Register → Bytewerte).
    def copy_temp_to_upload(self):
        with open(self.dlfileTemp, "rb") as src:
            with open(self.dlfile, "wb") as dst:
                dst.write(src.read())

    ## @brief Berechnet maximale Kanal‑ und ID‑Anzahl.
    def setmaxchannels(self):
        self.maxchn = self.channelspermodul * self.module
        self.maxid = self.maxchn * 2

    ## @brief Baut vollständiges ID‑Mapping (ID → IP → Register → Bytewerte).
    #  @details Ruft im Anschluss dict_to_dbc() auf.
    def set_id_dict(self):
        dummybytelist = [255, 255, 255, 255, 255, 255, 255, 255]
        kanalregister = self.diagregister + self.eventregister
        for modul in range(self.module):
            register = 0
            ip = self.iplist[modul]
            self.status = f"Modul mir der Adresse {ip} wird konfiguriert"
            print(f"Modul: {modul}, IP: {ip}")  # Debugging-Ausgabe
            if self.module == 1:
                lastid = float(self.maxid / self.module)
            lastid = float(self.maxid / self.module)
            for id in range(int(lastid)):
                self.iddict[self.startid + id + modul * int(self.maxid / self.module)] = [ip, kanalregister[register], dummybytelist]
                print(f"{id} {self.startid + id + modul * int(self.maxid / self.module)} { kanalregister[register]}")
                register = (register + 1) % 24
            modul = modul + 1
        self.dict_to_dbc()
        
    ## @brief Initialisiert CAN‑Hardwareobjekt.
    def inithw(self):
        self.bus = can.interface.Bus(bustype=self.canhw, channel=self.channel, bitrate=250000)
        
    ## @brief Sendet alle gesammelten Messdaten als CAN‑Nachrichten.
    def send_dict_to_can(self):
        for key , value in self.iddict.items():
            if not key in self.deactivadedID:
                if not value[2] == None:
                    msg = can.Message(arbitration_id=key, data=value[2])
                    self.bus.send(msg)
                    # time.sleep(self.pause_modbus)

    ## @brief Schreibt Identifikationsdaten eines Sensors als XML.
    #  @param data Dictionary der IO‑Link‑Identifikationsobjekte.
    def writexml(self, data):
        aliasDict = {16: "Vendor_Name", 17: "Vendor_Text", 18: "Product_Name", 19: "Product_ID", 20: "Product_Text", 21: "Serial_Number"}
        if data[21] is not None or not data[21] == "":
            node_id = data[21]
            filename = f"SN{node_id}.xml"
            root = ET.Element("root")
            for key, value in data.items():
                element = ET.SubElement(root, aliasDict[key])
                element.text = str(value)
                print(str(key) + "||" +str(value))
            tree = ET.ElementTree(root)
            if not filename == "SN.xml":
                tree.write(f"./uploads/{filename}", encoding='utf-8', xml_declaration=True )
            print(filename)
    
    ## @brief Führt IO‑Link Acyclic Read (Identifikationsobjekte) aus.
    #  @param hostip  IP des IO‑Link‑Masters.
    #  @param port    IO‑Link‑Port.
    #  @return Dictionary der gelesenen Identifikationsobjekte.
    def iolink_identifikation(self, hostip, port):
        identDict = {}
        client = self.get_connection(hostip)
        index_list = [16, 17, 18, 19, 20, 21]
        subindex = 0
        for index in index_list:
            print(f"\n--- Abfrage Index {index} ---")
            request = [0] * 22
            request[0] = port
            request[1] = index
            request[2] = subindex
            request[3] = (1 << 8) | 1
            request[4] = 0

            client.write_multiple_registers(500, request)
            time.sleep(0.2)

            response = client.read_holding_registers(0, 22)

            if response:
                hex_string = ''.join(f'{r:04x}' for r in response[6:22])
                try:
                    ascii_string = bytes.fromhex(hex_string).decode('ascii', errors='ignore').strip('\x00')
                    identDict[index] = ascii_string.replace("/","_").replace("\x00", "").replace("OH","").replace("\x01", "").replace(" ", "").replace(".", "").replace(":", "").replace("-", "_").replace("\t", ", ")
                    print("ASCII:", ascii_string)
                except Exception as e:
                    print("Fehler beim Dekodieren:", e)
            else:
                print("Keine Antwort erhalten.")
            client.write_multiple_registers(500, [0]*5)
            time.sleep(0.10)
        client.close()
        self.writexml(identDict)
        return identDict

    ## @brief Bereinigt Rohsensornamen für DBC‑Erstellung.
    #  @param sensorraw Ursprungsstring.
    #  @return Bereinigter Name.
    def clean_sensor_name(self, sensorraw):
        sensor = sensorraw.replace("/","_").replace("OH","").replace(" ", "").replace(".", "").replace(":", "").replace("-", "_").replace("\t", ", ")
        return sensor

    ## @brief Lädt YAML‑Konfigurationen für Sensoren.
    #  @param path Pfad zur YAML‑Datei.
    #  @return Parsed YAML Struktur.
    def load_sensor_config(self, path="knownSensors.yaml"):
        with open(path, "r") as f:
            return yaml.safe_load(f)

    ## @brief Erstellt ein DBC‑Signalobjekt.
    #  @param cfg YAML‑Konfiguration.
    #  @param startbit Bitoffset.
    #  @param signalname Signalname.
    #  @param comment Kommentartext.
    #  @return Cantools Signalobjekt.
    def build_signal(self, cfg, startbit, signalname, comment):
        pfix=None
        pfix = cfg.get("pfix")
        signalname=cfg.get("name", signalname)
        if not pfix == None:
            signalname = f"{pfix}{signalname[2:]}"
        signalname = signalname[:32]
        signal = Signal(
            name=signalname,
            start=startbit,
            length=16,
            byte_order="little_endian",
            is_signed=cfg["is_signed"],
            conversion=LinearConversion(
                scale=cfg["scale"],
                offset=cfg["offset"],
                is_float=False
            ),
            minimum=-3000,
            maximum=3000,
            unit=cfg["unit"],
            comment=comment
        )
        return signal

    ## @brief Ermittelt passende Sensorkonfiguration oder Default‑Signal.
    #  @param signalname Ursprungs‑Signalname.
    #  @param value_startbit Bitoffset.
    #  @param signalcomment Kommentartext.
    #  @param iddict Identifikationsdaten.
    #  @param register IO‑Link‑ oder Event‑Register.
    #  @return DBC‑Signalobjekt.
    def check_known_sensors(self, signalname, value_startbit, signalcomment, iddict, register):
        sensor_base = self.clean_sensor_name(iddict[19])
        if register in self.eventregister:
            sensor_base = "diag"
            startbit = value_startbit + 64
        else:
            startbit = value_startbit
        sensor_key = f"{sensor_base}_{startbit}"
        config = self.load_sensor_config()
        cfg = config.get(sensor_key, config["defaultSignal"])
        return self.build_signal(
            cfg=cfg,
            startbit=value_startbit,
            signalname=signalname,
            comment=signalcomment
        )

    ## @brief Wandelt IO‑Daten vollständig in eine DBC‑Datei um.
    #  @details Für jede ID:
    #   - Identifikationsdaten lesen  
    #   - Signale erzeugen  
    #   - Nachrichtenlänge erzeugen  
    #   - In Datenbank aufnehmen  
    #   - DBC schreiben  
    def dict_to_dbc(self):
        portDict = {}
        db = Database()
        def signal_add(id, idict, register): 
            signallist = []
            start = {0:0, 1:16, 2:32, 3:48}
            for key, value in start.items():
                signalname = f'SignalID_{id}_{key}'
                signalcomment = ""
                try:
                    tempname = f"SN{idict[21]}ID{id}S{key}"
                    if not tempname.startswith("SNID"):
                        signalname = tempname
                        signalname = signalname.replace("/","_").replace("\x00", "").replace("OH","").replace("\x01", "").replace(" ", "").replace(".", "").replace(":", "").replace("-", "_").replace("\t", ", ")
                        for cvalue in idict.values():
                            comment = cvalue.replace("/","_").replace("\x00", "").replace("OH","").replace("\x01", "").replace(" ", "").replace("\t", ", ")
                            if not comment == "" or not comment == None:
                                signalcomment = f"SN{idict[21]}"
                            else:
                                signalcomment = f"IOLink {str(signalname)}"
                except Exception as e:
                    print(e)
                signal = self.check_known_sensors(signalname, value, signalcomment, idict, register)
                signallist.append(signal)
                self.status = f"{signalname} zur dbc hinzugefügt"
            return signallist

        for key, value in self.iddict.items():
            identDict = None
            if value[1] in self.diagregister:  
                port = int((value[1]-1)/1000) 
                identDict = self.iolink_identifikation(value[0], port)
                portDict[port] = identDict
            if value[1] in self.eventregister:  
                port = int((value[1]/10)-2) 
                identDict = portDict[port]
            signallist = signal_add(key, identDict, value[1])
            max_bit_position = max(signal.start + signal.length for signal in signallist)
            message_length = (max_bit_position + 7) // 8
            message = Message(
                frame_id=key,
                name=f'ExampleMessage{key}',
                length=message_length,
                signals=signallist
            )
            try:
                db.messages.append(message)
            except Exception as e:
                print(e)
                pass

        with open(self.dlfile, 'w', encoding="iso-8859-1") as f:
            self.status = f"{f} wird ins DBC übernommen"
            f.write(db.as_dbc_string())


    ## @brief Hauptschleife: IO lesen → CAN senden → Zykluszeit warten.
    def runbridge(self):
        while self.running == True:
            z1 = datetime.now()
            t1 = Thread(target=self.thread_io_data)
            t2 = Thread(target=self.send_dict_to_can)
            t1.start()
            offset = (datetime.now() - z1).total_seconds()
            sleep_time = max(0, self.cycle - offset - 0.004)
            time.sleep(sleep_time)
            t1.join() #Warten um polling issus zu eleminieren
            t2.start()



## @class CanbridgeWeb
#  @brief Webinterface zur Steuerung der Canbridge.
#  @details
#   Stellt ein Flask‑Webinterface bereit, verwaltet Routing, Konfiguration,
#   Systeminformationen, Modul‑Setup, CAN‑Konfiguration und Download von DBC‑Dateien.
class CanbridgeWeb:
    ## @brief Konstruktor der Weboberfläche.
    #  @details Initialisiert:
    #   - Flask‑App  
    #   - Canbridge‑Instanz  
    #   - Routen  
    #   - Systeminformationen (Hostname, IP, OS)  
    #   - Dropdown‑Parameter (Baudrate, Module, Start‑IP, etc.)  
    #   - interne Statusvariablen  
    def __init__(self):
        self.app = Flask(__name__)
        self.cb = Canbridge()
        self.cs = None
        self.add_routes()
        self.cg = None
        self.comments = {}
        self.candb = None
        self.myip = None
        self.status = self.cb.status
        self.osname = self.get_osname()
        self.myip = self.get_myip()
        self.hostname = self.get_myhostname()
        self.modulcount = {1:1, 2:2, 3:3, 4:4, 5:5, 6:6, 7:7, 8:8}
        self.subnet = "192.168.1."
        self.canids = {800:800, 700:700, 600:600, 500:500, 400:400, 300:300, 200:200, 100:100}
        self.canhw = {"Socketcan" : "socketcan" } 
        self.knoten = {"Can0":"can0" , "VitualCan" : "vcan0"}
        self.bitrate = {"50kBaud" : 50000, "125kBaud" : 125000, "250kBaud" : 250000 , "500kBaud" : 500000, "800kBaud" : 800000, "1000kBaud" : 1000000} 
        self.freeRam = self.get_free_mem()
        self.diskusage = self.get_free_space()
        self.cycle = {"1000ms" : 1.0, "500ms" : 0.5, "250ms" : 0.25, "100ms" : 0.1, "50ms" : 0.05, "30ms" : 0.03, "10ms" : 0.01}
        self.function = {"config" : 0, "show" : 1, "run" : 2, "Stop" : 3}
        self.startip = {245:245, 246:246, 247:247, 248:248, 249:249, 250:250, 251:251, 252:252, 253:253, 254:254, 255:255}
        self.cb.id = 700
        self.cb.startid = 800
        self.cb.maxchn = 8
        self.cb.subnet = "192.168.1."
        self.cb.startip = None
        self.channelspermodul = 8
        self.cb.module = 2
        self.dlfile = self.cb.dlfile
        self.savefile = None
        self.belegung = None
        self.cycletime = None


    ## @brief Ermittelt die lokale IPv4‑Adresse aus dem System.
    #  @return IP‑Adresse oder None bei Fehler.
    def get_myip(self):
        time.sleep(1)
        try:
            command = "ip addr show eth0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 | head -n 1"
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Error executing command: {e}")
            self.getstatus(e)
            self.get_myip()
            return None

    ## @brief Liefert den Hostnamen des Systems.
    #  @return Hostname oder None bei Fehler.
    def get_myhostname(self):
        try:
            command = "hostname"
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Error executing command: {e}")
            self.getstatus(e)
            return None
        
    ## @brief Liefert den Betriebssystemnamen.
    #  @return "Linux", "Windows" oder OS‑String.
    def get_osname(self):
        try:
            command = "uname"
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Error executing command: {e}")
            self.getstatus(e)
            return("Windows")
    
    ## @brief Auswahl des passenden CAN‑Treibers je nach OS.
    def get_driver(self):
        if self.osname == "Linux":
            self.canhw = {"Socketcan" : "socketcan" } 
            self.knoten = {"Can0":"can0", "VCan0":"vcan0" }
        else:
            self.canhw = {"Ixxat" : "ixxat" , "Peak" : 'pcan'} 
            self.knoten = {"Channel 0" : "0" }

    ## @brief Priorisiert Modbus‑TCP Port 502 mittels Shellskript.
    def prio_modbus(self):
        time.sleep(0.1)
        try:
            command = "bash qosPrio502.sh "
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            print(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            print(f"Error executing command: {e}")
            self.getstatus(f"Fehler {e} beim Priorisieren des Port 502")
            return None
       
    ## @brief Dummy‑Endlosschleife als Thread‑Hintergrundprozess.
    def start_loop(self):
        '''
        Threads
        '''
        i = 0
        while True:
            time.sleep(1)
    
    ## @brief Liefert freien Speicherplatz auf dem Root‑Filesystem.
    #  @return Prozentwerte der Belegung.
    def get_free_space(self):
        disk_usage = psutil.disk_usage('/')
        return(disk_usage.percent)

    ## @brief Liefert freien RAM in MB.
    #  @return Freier RAM in Megabyte.
    def get_free_mem(self):
        mem = psutil.virtual_memory()
        free_ram = mem.available
        free_ram_mb = round(free_ram / (1024 * 1024))
        return(free_ram_mb)

    ## @brief Registriert HTTP‑Routen der Flask‑App.
    def add_routes(self):
        ''' legt das Routing der url fest
        '''
        self.app.add_url_rule('/', view_func=self.index, methods=['GET', 'POST'])
        self.app.add_url_rule('/download', view_func=self.downloadfile, methods=['GET'])

    ## @brief Index‑Seite, nimmt Web‑Kommandos entgegen.
    def index(self):
        ''' reciving the commands from webinterface'''
        if 'transmit' in request.form:
            self.read_post_setup()
        return render_template('index.html', cs=self)
     
    ## @brief Konfiguriert Linux‑CAN‑Interface über Bash.
    #  @param interface Name des Interfaces.
    #  @param bitrate CAN‑Baudrate.
    def bash_set_can_interface(self, interface, bitrate = 500000):
        ''' configure the caninterface in Linux '''
        if interface == "vcan0":
            try:
                subprocess.run(f"sudo ip link set {interface} down", shell=True, check=True, capture_output=True, text=True)
                try:
                    subprocess.run("sudo ip link add dev vcan0 type vcan",  shell=True, check=True, capture_output=True, text=True)
                except:
                    pass
                subprocess.run("sudo ip link set up vcan0" , shell=True, check=True, capture_output=True, text=True)
                result = subprocess.run(f"ip -details link show {interface}", shell=True, check=True, capture_output=True, text=True)
                print(result.stdout)
            except subprocess.CalledProcessError as e:
                print(f"An error occurred while setting the CAN interface: {e.stderr}")
                raise
        else:
            try:
                subprocess.run(f"sudo ip link set {interface} down", shell=True, check=True, capture_output=True, text=True)
                subprocess.run(f"sudo ip link set {interface} up ", shell=True, check=True, capture_output=True, text=True)
                result = subprocess.run(f"ip -details link show {interface}", shell=True, check=True, capture_output=True, text=True)
                print(result.stdout)
            except subprocess.CalledProcessError as e:
                print(f"An error occurred while setting the CAN interface: {e.stderr}")
                raise

    ## @brief Liest POST‑Parameter des Webinterfaces und wandelt sie in ein dict um.
    def read_post_setup(self):
            ''' recives commands'''
            cmddict = {}
            options = ['function', 'modulcount', 'startip', 'canids', 'cycle', 'knoten']
            for option in options:
                try:
                    if option == 'knoten':
                        cmddict[option] = request.form[option]
                    else:
                        if option == 'cycle':
                            cmddict[option] = float(request.form[option])
                        else:
                            cmddict[option] = int(request.form[option])
                except Exception as e:
                    print(e)
                    self.getstatus(e)
            print(cmddict)
            self.ex(cmddict)

    ## @brief Konfiguriert die CAN‑Bridge anhand der Web‑Parameter.
    #  @param cmddict Dictionary mit Setup‑Parametern.
    def config(self, cmddict):
        self.cb = Canbridge()
        self.cb.running = False
        status = None
        try:
            self.cb.iddict = {}
            self.cb.startip = cmddict['startip']
            self.cb.module = cmddict['modulcount']
            self.cb.startid = cmddict['canids']
            self.cb.cycle = cmddict['cycle']
            self.cb.channel = cmddict['knoten']
            self.cb.setip()
            self.cb.setmaxchannels()
            self.cb.inithw()
            self.bash_set_can_interface(cmddict['knoten'])
            self.cb.copy_temp_to_upload()
            config = Thread(target=self.cb.set_id_dict , args=())
            config.start()
            while config.is_alive():
                self.getstatus(f"{self.cb.status}")     
            self.detect()
            self.setdeactivaded()
            view = self.check_min_cycleTime()                                                                                                                                                                                                                                                                          
            self.show(view)

        except Exception as e:
            print(e)
            self.getstatus(e)

    ## @brief Berechnet minimale Kanal‑Cycle‑Time basierend auf aktiven Ports.
    #  @return Zeit pro Port.
    def check_min_cycleTime(self):
        posiblePorts = len(self.cb.iddict.items()) * 0.5
        deactivadedPorts = len(self.cb.deactivadedID) * 0.5
        activePorts = (posiblePorts - deactivadedPorts) 
        cyclePerPort = self.cb.cycle / activePorts
        self.cb.pause_modbus = (self.cb.cycle * 0.5) / activePorts  
        self.cycletime = cyclePerPort
        view = (
        f"iddict={len(self.cb.iddict)}, "
        f"deact={len(self.cb.deactivadedID)}, "
        f"possible={posiblePorts}, "
        f"active={activePorts}, "
        f"cycle={self.cb.cycle}, "
        f"cycletime/Port={cyclePerPort}, "
        f"pause_modbus={self.cb.pause_modbus}"
        )
        return view

    ## @brief Führt eine Sensor‑Belegungserkennung durch.
    #  @return dict mit (IP, Kanal) → Status
    def detect(self):
        belegung = {}
        kanal = 1
        self.cb.thread_io_data(detect=True)
        for key, value in self.cb.iddict.items():
            if value[1] in self.cb.diagregister:
                belegung[(value[0], kanal)] = value[2][0]
            kanal = kanal + 1
            if kanal > 8:
                kanal = 1
        self.belegung = belegung

    ## @brief deaktiviert nicht belegte Kanäle.
    def setdeactivaded(self):
        kanal = 1
        try:
            for key, value in self.cb.iddict.items():
                port = self.belegung[(value[0], kanal)]
                if port == 7:
                    self.cb.deactivadedID.append(key)
                    self.getstatus(f"{key} wurde deaktiviert")
                kanal = kanal + 1
                if kanal > 8:
                    kanal = 1
        except Exception as e:
            self.getstatus(e)

    ## @brief Zeigt die CAN‑zu‑IO‑Link‑Zuordnung an.
    def show(self, head = ""):
        ''' show the canbridge '''
        status = {1:"I.O.", 5:"SenErr", 7:"n.c.", 255:"IPErr"}
        belegung = self.belegung.copy()
        kanal = 1
        view = f"{head}\nMapping Canmatrix zu I.O. Linkmaster\n"
        view = f"{view}\tCanId \t : \t Port \t : \t IP Modul \t : \t State \t : \t Reg \t\n"
        try:
            for key, value in self.cb.iddict.items():
                port = belegung[(value[0], kanal)]
                if not port == 255:
                    view = (f"{view}\t {key} \t : \t {kanal} \t : \t {value[0]} \t : \t {status[port]} \t : \t {value[1]} \t\n")
                else:
                    view = (f"{view}\t {key} \t : \t {kanal} \t : \t {value[0]} \t : \t {status[port]} \t : \t {value[1]} \t\n")
                kanal = kanal + 1
                if kanal > 8:
                    kanal = 1
            if self.cycletime < 0.01:
                error = (
                    f'<font color="#FFC72C">'
                    f'Achtung: Abtastrate {self.cb.cycle * 1000:.0f} ms → '
                    f'max. {int(self.cb.cycle / 0.01)} Kanäle.<br>'
                    f'Aktuell: {self.cycletime * 1000:.0f} ms/Kanal – mögliche Verluste.<br>'
                    f'Messung kann unter Berücksichtigung dieser Meldung fortgesetzt werden.<br>'
                    f'</font>'
                )
                view = (f"{error}{view}")
            self.getstatus(view)
        except:
            self.getstatus("Error, bitte prüfen ob Sensor angeschlossen")

    ## @brief Startet die CAN‑Bridge (Loop).
    def run_bridge(self):
        ''' start the canbridge '''
        self.cb.running = True
        r = Thread(target=self.cb.runbridge, daemon=False)
        r.start()
        self.getstatus("Bridge aktiv")
    
    ## @brief Stoppt die CAN‑Bridge.
    def stop_bridge(self):
        ''' stop the canbridge '''
        self.cb.running = False
        self.getstatus("Bridge gestopt")

    ## @brief Führt konfigurierte Funktion abhängig vom Web‑Command aus.
    #  @param cmddict Parameterdict.
    def ex(self,cmddict):
        if cmddict['function'] == 0:
            self.savefile = True
            self.stop_bridge()
            self.cb.__init__()
            self.config(cmddict)

        if cmddict['function'] == 1:
            self.savefile = True
            self.show()

        if cmddict['function'] == 2:
            self.prio_modbus()
            self.run_bridge()

        if cmddict['function'] == 3:
            self.savefile = None
            self.stop_bridge()

    ## @brief Stellt DBC‑Datei via Webdownload bereit.
    def downloadfile(self):
        ''' filedownload selection '''
        if self.dlfile[-4:] == '.dbc':
            response = make_response(send_file(self.dlfile, as_attachment=True, download_name=self.dlfile))
            response.headers["Content-Type"] = "application/xml"
            response.headers["Content-Disposition"] = "attachment; filename=" +  str(self.dlfile)
            self.getstatus(str(self.dlfile) + " wurde runter geladen")
        return response

    ## @brief Setzt neuen Status zur Anzeige im Webinterface.
    #  @param status Text des neuen Status.
    def getstatus(self, status):
        self.status = status
        
## @brief Startet die Flask‑Webapplikation.
    #  @details
    #    - Startet Webserver auf Port 8082  
    #    - Debugmodus jederzeit aktivierbar  
    #    - Optionaler Loop‑Thread deaktiviert  
    def run(self):
        ''' Startet die Webapp  
            Aukommetierte Zeile fürs Debuggen
        '''
        # loop_thread = Thread(target=self.start_loop)
        # loop_thread.daemon = True  # Damit der Loop-Thread automatisch beendet wird, wenn die Hauptanwendung beendet wird
        # loop_thread.start()
        # webbrowser.get().open("http://127.0.0.1:8081")
        self.app.run(debug=True,  host='0.0.0.0' ,port=8082)
        # serve(self.app,  port=8082, threads=30)
        # self.cache = Cache(self.app, config={'CACHE_TYPE': 'simple', 'CACHE_DEFAULT_TIMEOUT': 30})


    ## @brief Setzt den aktuellen Status für das Webinterface.
    #  @param status Neuer Statusstring.
    def getstatus(self, status):
        self.status = status


## @brief Hauptfunktion zum Starten der Weboberfläche.
#  @details
#    Wird ausgeführt, wenn das Script direkt gestartet wird.
def main():
    cb = CanbridgeWeb()
    cb.run()


## @brief Eintrittspunkt des Programms (wenn als Skript ausgeführt).
if __name__ == '__main__':
    main()

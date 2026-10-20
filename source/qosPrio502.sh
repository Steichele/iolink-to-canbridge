#!/bin/bash

# Subnetz definieren
SUBNET="192.168.1.0/24"

# DSCP-Wert für AF41
DSCP_VALUE="AF41"

# DSCP-Markierung für ausgehenden Traffic auf Port 502 im Subnetz
sudo iptables -t mangle -A OUTPUT -p tcp --dport 502 -d $SUBNET -j DSCP --set-dscp-class $DSCP_VALUE

# DSCP-Markierung für eingehenden Traffic auf Port 502 im Subnetz
sudo iptables -t mangle -A PREROUTING -p tcp --sport 502 -s $SUBNET -j DSCP --set-dscp-class $DSCP_VALUE

# tc-Regeln setzen
sudo tc qdisc del dev eth0 root 2>/dev/null
sudo tc qdisc add dev eth0 root handle 1: prio
sudo tc filter add dev eth0 protocol ip parent 1: prio 1 u32 \
    match ip tos 0x88 0xfc flowid 1:1

echo "QoS-Regeln für Port 502 im Subnetz $SUBNET erfolgreich gesetzt."

# sudo tcpdump -i eth0 -nn -v host 192.168.1.2 # Prüfen tos 0x88 = o.k
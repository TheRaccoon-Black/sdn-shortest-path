#!/usr/bin/env python3
"""
Diagnostic test to understand ARP behavior in Fat-tree
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from skrip_topologi import SkripsiTopo
import time

setLogLevel('info')

def diagnose_arp():
    topo = SkripsiTopo(topo_type='fattree', k=4)
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False
    )
    
    net.start()
    
    h1 = net.get('h1')
    h16 = net.get('h16')
    e0_0 = net.get('e0_0')  # Edge switch for h1
    e3_1 = net.get('e3_1')  # Edge switch for h16
    
    # Configure IPs
    h1.cmd('ip link set h1-eth0 up')
    h1.cmd('ip addr add 10.0.0.1/8 dev h1-eth0')
    h16.cmd('ip link set h16-eth0 up')
    h16.cmd('ip addr add 10.0.0.16/8 dev h16-eth0')
    
    # Enable ARP debugging on h16
    h16.cmd('tcpdump -i h16-eth0 -w /tmp/h16_traffic.pcap &')
    time.sleep(1)
    
    # Monitor h1's ARP
    print("\n[DIAG] h1 sending ARP request...")
    result = h1.cmd('ip neigh flush all; ping -c 1 -W 2 10.0.0.16 & sleep 1')
    
    # Check what h16 received
    time.sleep(2)
    h16.cmd('pkill tcpdump')
    
    print("\n[DIAG] h1 ARPtable after ping attempt:")
    print(h1.cmd('ip neigh show'))
    
    print("\n[DIAG] h16 ARP table:")
    print(h16.cmd('ip neigh show'))
    
    # Check if h16 ever receives the ARP
    print("\n[DIAG] Checking if h16-eth0 received any packets:")
    print(h16.cmd('ip -s link show h16-eth0'))
    
    # Try reverse: h16 pings h1
    print("\n[DIAG] h16 sending ping to h1...")
    result = h16.cmd('ping -c 1 -W 2 10.0.0.1')
    print(result)
    
    print("\n[DIAG] h1 ARP table after h16's ping:")
    print(h1.cmd('ip neigh show'))
    
    net.stop()

if __name__ == '__main__':
    diagnose_arp()

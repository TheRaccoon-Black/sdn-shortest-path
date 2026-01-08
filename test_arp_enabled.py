#!/usr/bin/env python3
"""
Test with explicit ARP responding enabled on h16
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from skrip_topologi import SkripsiTopo
import time
import subprocess

setLogLevel('info')

def test_with_arp_responder():
    topo = SkripsiTopo(topo_type='fattree', k=4)
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False
    )
    
    net.start()
    
    # Wait for all switches to come up
    time.sleep(5)
    
    h1 = net.get('h1')
    h16 = net.get('h16')
    
    # Configure IPs
    h1.cmd('ip link set h1-eth0 up')
    h1.cmd('ip addr add 10.0.0.1/8 dev h1-eth0')
    h16.cmd('ip link set h16-eth0 up')
    h16.cmd('ip addr add 10.0.0.16/8 dev h16-eth0')
    
    time.sleep(1)
    
    # CRITICAL: Make sure h16 will respond to ARP
    print("\n[CONFIG] Enabling ARP responding on h16...")
    h16.cmd('ip link set h16-eth0 arp on')
    h16.cmd('sysctl -w net.ipv4.conf.all.arp_ignore=0')
    h16.cmd('sysctl -w net.ipv4.conf.h16-eth0.arp_ignore=0')
    h16.cmd('sysctl -w net.ipv4.conf.h16-eth0.rp_filter=0')
    
    # Explicitly add h1's IP to h16's ARP table (static ARP)
    h16.cmd('arp -s 10.0.0.1 62:a6:21:12:3c:f7')  # h1's MAC
    
    h1.cmd('sysctl -w net.ipv4.conf.all.arp_ignore=0')
    h1.cmd('sysctl -w net.ipv4.conf.h1-eth0.rp_filter=0')
    
    time.sleep(1)
    
    print("\n[TEST] h1 pinging h16...")
    result = h1.cmd('ping -c 3 -W 1 10.0.0.16')
    print(result)
    
    print("\n[CHECK] h1 ARP table:")
    print(h1.cmd('ip neigh show'))
    
    print("\n[CHECK] h16 ARP table:")
    print(h16.cmd('ip neigh show'))
    
    net.stop()

if __name__ == '__main__':
    test_with_arp_responder()

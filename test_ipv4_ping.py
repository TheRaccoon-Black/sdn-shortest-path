#!/usr/bin/env python3
"""
Test basic IPv4 connectivity in Fat-tree topology
Explicitly configure IPv4 on all hosts first
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from skrip_topologi import SkripsiTopo
import time

setLogLevel('info')

def test_ipv4_ping():
    # Create topology
    topo = SkripsiTopo(topo_type='fattree', k=4)
    
    # Create network with remote controller
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False  # Prevent auto MAC assignment
    )
    
    net.start()
    
    # Get h1 and h16
    h1 = net.get('h1')
    h16 = net.get('h16')
    
    print("\n[DEBUG] Explicitly configuring IPv4 on hosts...")
    
    # Explicitly configure IPv4 with proper ARP
    h1.cmd('ip link set h1-eth0 up')
    h1.cmd('ip addr add 10.0.0.1/8 dev h1-eth0')
    h1.cmd('arp -d 10.0.0.16 2>/dev/null || true')  # Clear any cached ARP
    
    h16.cmd('ip link set h16-eth0 up')
    h16.cmd('ip addr add 10.0.0.16/8 dev h16-eth0')
    h16.cmd('arp -d 10.0.0.1 2>/dev/null || true')
    
    # Enable IPv4 forwarding and ARP
    h1.cmd('sysctl -w net.ipv4.conf.all.arp_ignore=0')
    h1.cmd('sysctl -w net.ipv4.conf.h1-eth0.arp_ignore=0')
    h1.cmd('sysctl -w net.ipv4.conf.h1-eth0.arp_respond=1')
    
    h16.cmd('sysctl -w net.ipv4.conf.all.arp_ignore=0')
    h16.cmd('sysctl -w net.ipv4.conf.h16-eth0.arp_ignore=0')
    h16.cmd('sysctl -w net.ipv4.conf.h16-eth0.arp_respond=1')
    
    time.sleep(2)
    
    print("\n[DEBUG] Checking IPs:")
    print(f"h1 ifconfig:\n{h1.cmd('ifconfig')}")
    print(f"\nh16 ifconfig:\n{h16.cmd('ifconfig')}")
    
    print("\n[DEBUG] Checking routes:")
    print(f"h1 route -n:\n{h1.cmd('route -n')}")
    print(f"h16 route -n:\n{h16.cmd('route -n')}")
    
    print("\n[DEBUG] Testing ARP discovery...")
    # Send explicit ARP request
    result = h1.cmd('arping -c 1 -w 1 10.0.0.16')
    print(f"arping result:\n{result}")
    
    time.sleep(2)
    
    print("\n[DEBUG] Testing ping...")
    result = h1.cmd(f'ping -c 5 -W 1 10.0.0.16')
    print(f"ping result:\n{result}")
    
    net.stop()

if __name__ == '__main__':
    test_ipv4_ping()

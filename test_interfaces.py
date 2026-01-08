#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/ojik/Documents/habibti')

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi import SkripsiTopo
import time

setLogLevel('info')

# Create Fat-tree topology
topo = SkripsiTopo(topo_type='fattree', k=4)

# Create Mininet with OVS switches
net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

info("*** Starting network...\n")
net.start()

info("*** Waiting 40 seconds...\n")
time.sleep(40)

h1 = net.get('h1')
h16 = net.get('h16')
e0_0 = net.get('e0_0')
e3_1 = net.get('e3_1')

info(f"\nh1 interfaces:\n")
info(h1.cmd("ip addr show"))

info(f"\nh16 interfaces:\n")
info(h16.cmd("ip addr show"))

info(f"\ne0_0 interfaces:\n")
info(e0_0.cmd("ovs-vsctl list-ifaces e0_0"))

info(f"\ne3_1 interfaces:\n")
info(e3_1.cmd("ovs-vsctl list-ifaces e3_1"))

info(f"\nnetwork connectivity test:\n")
# Test if hosts can reach their own switch
info(f"h1 -> e0_0 (same switch):\n")
result = h1.cmd("ping -c 1 -W 1 10.0.0.1")
info(result)

# Check ARP
info(f"\nh1 ARP table before:\n")
info(h1.cmd("arp -n"))

info(f"\nTrying to send ARP request...\n")
h1.cmd("arping -c 1 10.0.0.16 || true")

info(f"\nh1 ARP table after:\n")
info(h1.cmd("arp -n"))

net.stop()

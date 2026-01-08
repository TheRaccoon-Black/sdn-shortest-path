#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/ojik/Documents/habibti')

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from skrip_topologi import SkripsiTopo

setLogLevel('info')

# Create Fat-tree topology
topo = SkripsiTopo(topo_type='fattree', k=4)

# Create Mininet with OVS switches
net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

info("*** Starting network...\n")
net.start()

info("*** Network started. Waiting 40 seconds for topology to stabilize...\n")
import time
time.sleep(40)

info("*** Testing ping h1 -> h16...\n")
h1 = net.get('h1')
h16 = net.get('h16')

info(f"h1 IP: {h1.IP()}, h16 IP: {h16.IP()}\n")

result = h1.cmd(f'ping -c 5 {h16.IP()}')
info(f"Ping result:\n{result}\n")

info("*** Stopping network...\n")
net.stop()

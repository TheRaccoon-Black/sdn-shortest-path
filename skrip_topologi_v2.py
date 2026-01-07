import sys
from functools import partial
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.link import TCLink
import argparse

class SkripsiTopo(Topo):
    """
    Topology class untuk berbagai jenis topologi SDN
    VERSION 2 - FIXED Fat-Tree Implementation
    """
    def __init__(self, topo_type='tree', nodes=10, k=4):
        Topo.__init__(self)
        
        if topo_type == 'tree':
            self.create_tree(nodes)
        elif topo_type == 'mesh':
            self.create_mesh(nodes)
        elif topo_type == 'fattree':
            self.create_fattree(k)
        elif topo_type == 'ring':
            self.create_ring(nodes)
            
    def create_tree(self, nodes):
        print(f"*** Membuat topologi TREE dengan {nodes} host")
        switches = []
        for i in range(nodes):
            switches.append(self.addSwitch(f's{i+1}'))
            
        for i in range(nodes):
            h = self.addHost(f'h{i+1}', ip=f'10.0.0.{i+1}/24')
            self.addLink(switches[i], h)
            
        # Linear/Tree structure
        for i in range(nodes - 1):
            self.addLink(switches[i], switches[i+1])

    def create_mesh(self, nodes):
        print(f"*** Membuat topologi MESH dengan {nodes} host")
        switches = []
        for i in range(nodes):
            s = self.addSwitch(f's{i+1}')
            h = self.addHost(f'h{i+1}', ip=f'10.0.0.{i+1}/24')
            self.addLink(s, h)
            switches.append(s)
            
        # Full Mesh
        for i in range(len(switches)):
            for j in range(i + 1, len(switches)):
                self.addLink(switches[i], switches[j])

    def create_fattree(self, k):
        """
        FIXED Fat-Tree Topology
        
        Struktur Fat-Tree untuk k=4:
        - Core: 4 switches (c1, c2, c3, c4)
        - Pods: 4 pods, masing-masing dengan:
          - Aggregation: 2 switches
          - Edge: 2 switches
          - Hosts: 4 hosts (2 per edge)
        - Total: 20 switches, 16 hosts
        """
        print(f"*** Membuat topologi FAT-TREE dengan k={k}")
        
        # Validate k
        if k % 2 != 0:
            print(f"ERROR: k must be even! Got k={k}")
            k = 4
        
        # Calculate topology size
        num_pods = k
        num_core = (k // 2) ** 2
        num_aggr_per_pod = k // 2
        num_edge_per_pod = k // 2
        num_hosts_per_edge = k // 2
        
        total_aggr = num_pods * num_aggr_per_pod
        total_edge = num_pods * num_edge_per_pod
        total_hosts = num_pods * num_edge_per_pod * num_hosts_per_edge
        total_switches = num_core + total_aggr + total_edge
        
        print(f"*** Fat-Tree Parameters:")
        print(f"    K = {k}")
        print(f"    Pods = {num_pods}")
        print(f"    Core Switches = {num_core}")
        print(f"    Aggregation Switches = {total_aggr} ({num_aggr_per_pod} per pod)")
        print(f"    Edge Switches = {total_edge} ({num_edge_per_pod} per pod)")
        print(f"    Total Switches = {total_switches}")
        print(f"    Hosts = {total_hosts} ({num_hosts_per_edge} per edge)")

        # Create Core Switches
        cores = []
        for i in range(num_core):
            core_sw = self.addSwitch(f'c{i+1}')
            cores.append(core_sw)
        
        print(f"*** Created {len(cores)} core switches")

        # Create Pods
        host_counter = 1
        
        for pod_id in range(num_pods):
            print(f"*** Building Pod {pod_id}...")
            
            # Create Aggregation Switches for this pod
            pod_aggrs = []
            for aggr_id in range(num_aggr_per_pod):
                aggr_sw = self.addSwitch(f'a{pod_id}_{aggr_id}')
                pod_aggrs.append(aggr_sw)
                
                # Connect aggregation to core switches
                # Each aggregation switch connects to k/2 core switches
                core_start = aggr_id * (k // 2)
                core_end = core_start + (k // 2)
                
                for core_idx in range(core_start, core_end):
                    if core_idx < len(cores):
                        self.addLink(aggr_sw, cores[core_idx], bw=10)
            
            # Create Edge Switches for this pod
            pod_edges = []
            for edge_id in range(num_edge_per_pod):
                edge_sw = self.addSwitch(f'e{pod_id}_{edge_id}')
                pod_edges.append(edge_sw)
                
                # Connect edge to ALL aggregation switches in the same pod
                for aggr_sw in pod_aggrs:
                    self.addLink(edge_sw, aggr_sw, bw=10)
                
                # Connect hosts to this edge switch
                for host_idx in range(num_hosts_per_edge):
                    host = self.addHost(
                        f'h{host_counter}',
                        ip=f'10.{pod_id}.{edge_id}.{host_idx + 2}/24',
                        mac=f'00:00:00:00:{pod_id:02x}:{host_counter:02x}'
                    )
                    self.addLink(edge_sw, host, bw=10)
                    host_counter += 1
        
        print(f"*** Fat-Tree topology created successfully!")
        print(f"*** Total hosts created: {host_counter - 1}")
        return

    def create_ring(self, nodes):
        print(f"*** Membuat topologi RING dengan {nodes} node")
        switches = []
        for i in range(nodes):
            s = self.addSwitch(f's{i+1}')
            h = self.addHost(f'h{i+1}', ip=f'10.0.0.{i+1}/24')
            self.addLink(s, h)
            switches.append(s)
        
        # Ring topology
        for i in range(nodes):
            s_curr = switches[i]
            s_next = switches[(i + 1) % nodes] 
            self.addLink(s_curr, s_next)

def run():
    parser = argparse.ArgumentParser(description='Skrip Topologi Skripsi SDN - V2')
    parser.add_argument('type', choices=['tree', 'mesh', 'fattree', 'ring'], 
                       help='Jenis topologi')
    parser.add_argument('--nodes', type=int, default=10, 
                       help='Jumlah node (untuk tree, mesh, ring)')
    parser.add_argument('--k', type=int, default=4, 
                       help='Parameter k untuk Fat-Tree (harus genap)')
    
    args = parser.parse_args()
    
    # Create topology
    topo = SkripsiTopo(topo_type=args.type, nodes=args.nodes, k=args.k)
    
    # Force OpenFlow 1.3
    switch_class = partial(OVSKernelSwitch, protocols='OpenFlow13')
    
    # Create network
    net = Mininet(
        topo=topo, 
        controller=None, 
        switch=switch_class, 
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True
    )
    
    # Add remote controller
    net.addController('c0', controller=RemoteController, 
                     ip='127.0.0.1', port=6653)
    
    net.start()
    print(f"\n*** Topologi {args.type} berhasil dibuat.")
    print(f"*** Controller: tcp:127.0.0.1:6653")
    print(f"*** Gunakan 'pingall' untuk test konektivitas")
    print(f"*** Gunakan 'exit' untuk keluar\n")
    
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()
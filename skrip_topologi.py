import sys
from functools import partial
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.link import TCLink
import argparse

# INI ADALAH CLASS YANG DICARI OLEH SCRIPT OTOMASI
class SkripsiTopo(Topo):
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
            h = self.addHost(f'h{i+1}')
            self.addLink(switches[i], h)
            
        # Linear/Tree structure sederhana: s1-s2-s3...
        for i in range(nodes - 1):
            self.addLink(switches[i], switches[i+1])

    def create_mesh(self, nodes):
        print(f"*** Membuat topologi MESH dengan {nodes} host")
        switches = []
        for i in range(nodes):
            s = self.addSwitch(f's{i+1}')
            h = self.addHost(f'h{i+1}')
            self.addLink(s, h)
            switches.append(s)
            
        # Full Mesh: Setiap switch terhubung ke semua switch lain
        for i in range(len(switches)):
            for j in range(i + 1, len(switches)):
                self.addLink(switches[i], switches[j])

    def create_fattree(self, k):
        print(f"*** Membuat topologi FAT-TREE dengan k={k}")
        pod = k
        core_switches = (pod // 2) ** 2
        aggr_switches = pod * (pod // 2)
        edge_switches = pod * (pod // 2) # Per pod, total pod * (pod/2) = pod^2 / 2
        
        # Total edge switches = (k * k) / 2
        total_edge = (pod * pod) // 2
        total_aggr = (pod * pod) // 2
        
        print(f"Topology Details: Core={core_switches}, Aggr={total_aggr}, Edge={total_edge}")

        cores = []
        for i in range(core_switches):
            cores.append(self.addSwitch(f'c{i+1}'))

        # Buat Pods
        for p in range(pod):
            pod_aggrs = []
            pod_edges = []
            
            # Aggregation Switches di Pod ini
            for i in range(pod // 2):
                # ID unik untuk switch
                sw_name = f'a{p}_{i}'
                s = self.addSwitch(sw_name)
                pod_aggrs.append(s)
                
                # Connect Aggr ke Core (Sederhana: round robin atau block)
                # Standar FatTree: Aggr switch i di pod p connect ke Core switch grup i
                start_core = i * (pod // 2)
                for c_idx in range(start_core, start_core + (pod // 2)):
                    self.addLink(s, cores[c_idx])

            # Edge Switches di Pod ini
            for i in range(pod // 2):
                sw_name = f'e{p}_{i}'
                s = self.addSwitch(sw_name)
                pod_edges.append(s)
                
                # Connect Edge ke semua Aggr di pod yang sama
                for a_sw in pod_aggrs:
                    self.addLink(s, a_sw)
                
                # Add Hosts (k/2 hosts per edge switch)
                for h_idx in range(pod // 2):
                    # Host ID unik global
                    host_id = (p * (pod // 2) * (pod // 2)) + (i * (pod // 2)) + h_idx + 1
                    h = self.addHost(f'h{host_id}')
                    self.addLink(s, h)

    def create_ring(self, nodes):
        print(f"*** Membuat topologi RING dengan {nodes} node")
        switches = []
        for i in range(nodes):
            s = self.addSwitch(f's{i+1}')
            h = self.addHost(f'h{i+1}')
            self.addLink(s, h)
            switches.append(s)
        
        # Hubungkan Switch membentuk lingkaran
        for i in range(nodes):
            s_curr = switches[i]
            s_next = switches[(i + 1) % nodes] 
            self.addLink(s_curr, s_next)

def run():
    parser = argparse.ArgumentParser(description='Skrip Topologi Skripsi SDN')
    parser.add_argument('type', choices=['tree', 'mesh', 'fattree', 'ring'], help='Jenis topologi')
    parser.add_argument('--nodes', type=int, default=10, help='Jumlah node')
    parser.add_argument('--k', type=int, default=4, help='Parameter k FatTree')
    
    args = parser.parse_args()
    
    # Memanggil Class SkripsiTopo
    topo = SkripsiTopo(topo_type=args.type, nodes=args.nodes, k=args.k)
    
    # UPDATE PENTING: Memaksa Switch menggunakan OpenFlow 1.3
    switch_class = partial(OVSKernelSwitch, protocols='OpenFlow13')
    
    # Controller=None agar kita bisa add manual atau biarkan RemoteController default
    net = Mininet(topo=topo, controller=None, switch=switch_class, link=TCLink)
    
    # Tambahkan Controller Remote (Pastikan port sesuai dengan Ryu, default 6633 atau 6653)
    # Kita set ke 6653 karena Mininet sering default ke sana
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    
    net.start()
    print(f"*** Topologi {args.type} berhasil dibuat.")
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()

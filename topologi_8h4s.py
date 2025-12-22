#!/usr/bin/python3

"""
topologi_8h4s.py: Membuat topologi statis dengan 4 switch dan 8 host.
- Setiap switch terhubung ke 2 host.
- Semua switch terhubung satu sama lain dalam topologi full mesh.
"""

from mininet.net import Mininet
from mininet.node import RemoteController, Controller
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.topo import Topo

class CustomTopo(Topo):
    "Topologi Kustom 8 Host, 4 Switch."
    def build(self):
        # Tambahkan 4 Switch
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')

        # Tambahkan 8 Host
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h5 = self.addHost('h5')
        h6 = self.addHost('h6')
        h7 = self.addHost('h7')
        h8 = self.addHost('h8')

        # Hubungkan Host ke Switch (2 host per switch)
        info("*** Menghubungkan Host ke Switch\n")
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s2)
        self.addLink(h4, s2)
        self.addLink(h5, s3)
        self.addLink(h6, s3)
        self.addLink(h7, s4)
        self.addLink(h8, s4)

        # Hubungkan Switch dalam topologi Full Mesh
        info("*** Menghubungkan Switch (Full Mesh)\n")
        self.addLink(s1, s2)
        self.addLink(s1, s3)
        self.addLink(s1, s4)
        self.addLink(s2, s3)
        self.addLink(s2, s4)
        self.addLink(s3, s4)

def run():
    "Membuat dan menjalankan jaringan."
    topo = CustomTopo()
    
    # PERUBAHAN 1: Inisialisasi Mininet TANPA controller default
    # Kita menggunakan 'controller=None' untuk mencegah Mininet membuat controller default
    net = Mininet(topo=topo, controller=None, autoSetMacs=True)
    
    # PERUBAHAN 2: Menambahkan pesan debug yang jelas
    info("*** [BUKTI] Menambahkan controller secara manual ke PORT 6633 ***\n")
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)
    
    info("*** Memulai Jaringan\n")
    net.start()

    info("*** Menjalankan CLI\n")
    CLI(net)

    info("*** Menghentikan Jaringan\n")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()

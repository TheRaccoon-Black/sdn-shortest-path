import time
import sys
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi import SkripsiTopo 

def set_ovs_protocol_and_timeout(net, timeout=180):
    """
    Mengatur protokol OpenFlow 1.3 dan timeout yang panjang (Inactivity Probe)
    agar switch tidak disconnect saat Controller sedang sibuk menghitung rute.
    """
    info("*** [FIX] Mengatur Inactivity Probe ke {} detik untuk mencegah disconnect...\n".format(timeout))
    for sw in net.switches:
        sw.cmd('ovs-vsctl set Bridge {} protocols=OpenFlow13'.format(sw.name))
        sw.cmd('ovs-vsctl set-controller {} tcp:127.0.0.1:6653'.format(sw.name))
        sw.cmd('ovs-vsctl set controller {} inactivity_probe={}'.format(sw.name, timeout * 1000))

def measure_convergence(net, target_host_1, target_host_2, timeout=180):
    info(f"*** [TEST] Mengukur Convergence Time antara {target_host_1.name} dan {target_host_2.name}...\n")
    info(f"*** [INFO] Menunggu maksimal {timeout} detik agar jaringan stabil...\n")
    start_time = time.time()
    while True:
        # Kirim 1 ping dengan timeout singkat
        result = target_host_1.cmd(f'ping -c 1 -W 1 {target_host_2.IP()}')
        if "1 received" in result:
            end_time = time.time()
            return end_time - start_time
        if time.time() - start_time > timeout:
            info(f"*** [GAGAL] Timeout Convergence > {timeout} detik.\n")
            return None
        time.sleep(1)

def measure_throughput(net, client, server):
    info(f"*** [TEST] Mengukur Throughput antara {client.name} dan {server.name}...\n")
    server.cmd('killall -9 iperf')
    time.sleep(0.5)
    server.cmd('iperf -s &')
    time.sleep(1)
    
    # Jalankan iperf selama 5 detik
    iperf_output = client.cmd(f'iperf -c {server.IP()} -t 5 -f m')
    
    try:
        lines = iperf_output.split('\n')
        # Cari baris hasil yang mengandung bits/sec
        result_line = [l for l in lines if 'bits/sec' in l][-1]
        throughput_val = result_line.split()[-2] + " " + result_line.split()[-1]
        server.cmd('killall -9 iperf')
        return throughput_val
    except:
        server.cmd('killall -9 iperf')
        return "N/A"

def measure_recovery(net, s_src, s_dst, h_src, h_dst):
    info(f"*** [TEST] Mengukur Recovery Time (Memutus link {s_src}-{s_dst})...\n")
    
    # Pastikan koneksi lancar dulu
    h_src.cmd(f'ping -c 1 {h_dst.IP()}')
    
    # Ping flood background (interval 0.1s)
    h_src.cmd(f'ping -i 0.1 {h_dst.IP()} > ping_log.txt &')
    time.sleep(3)
    
    info(f"*** [ACTION] Memutus Link {s_src} <-> {s_dst} sekarang!\n")
    start_fail_time = time.time()
    
    # Putus link di Mininet
    net.configLinkStatus(s_src, s_dst, 'down')
    
    recovered = False
    recovery_duration = 0
    max_wait = 60 # Tunggu recovery maks 60 detik
    
    while time.time() - start_fail_time < max_wait:
        res = h_src.cmd(f'ping -c 1 -W 1 {h_dst.IP()}')
        if "1 received" in res:
            recovery_duration = time.time() - start_fail_time
            recovered = True
            break
        time.sleep(0.1)
    
    h_src.cmd('killall ping')
    
    if not recovered: return f"> {max_wait}s (Gagal/Tree)"
    return recovery_duration

def run_fattree_test(k, algo_name="TEST"):
    info(f"\n{'='*40}\nMEMULAI OTOMASI FAT-TREE: {algo_name} (K={k})\n{'='*40}\n")
    
    # 1. Bangun Topologi Fat-Tree
    topo = SkripsiTopo(topo_type='fattree', k=k)
    
    # Inisialisasi Mininet dengan OVS Kernel Switch
    net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
    
    # Tambahkan Remote Controller (Ryu)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    
    net.start()
    
    # 2. Fix Protocol & Timeout (PENTING untuk K besar)
    set_ovs_protocol_and_timeout(net, timeout=300)
    
    # 3. Tentukan Waktu Tunggu Berdasarkan K
    # Semakin besar K, semakin lama waktu yang dibutuhkan Ryu untuk mapping
    initial_wait = 30
    if k >= 6: initial_wait = 120  # ~45 Switches
    if k >= 8: initial_wait = 600  # ~80 Switches (Berat)
        
    info(f"*** Menunggu {initial_wait} detik agar Controller memetakan Fat-Tree...\n")
    time.sleep(initial_wait)
    
    # 4. Identifikasi Host & Switch untuk Test
    # Rumus host FatTree: (k^3)/4
    num_hosts = (k ** 3) // 4
    h_start = net.get('h1')
    h_end = net.get(f'h{num_hosts}')
    
    # Skenario Putus Kabel: Antara Edge Switch dan Aggregation Switch di Pod 0
    # Nama switch di skrip_topologi.py: e{pod}_{index} dan a{pod}_{index}
    try:
        s_fail_1 = net.get('e0_0').name
        s_fail_2 = net.get('a0_0').name
        info(f"*** Target Link Failure: {s_fail_1} <-> {s_fail_2}\n")
    except:
        # Fallback jika penamaan switch berbeda, ambil index awal
        s_fail_1 = net.switches[0].name
        s_fail_2 = net.switches[1].name
        info(f"*** Target Link Failure (Fallback): {s_fail_1} <-> {s_fail_2}\n")

    # 5. Jalankan Pengukuran
    ping_timeout = 180
    if k >= 8: ping_timeout = 600

    conv_time = measure_convergence(net, h_start, h_end, timeout=ping_timeout)
    
    if conv_time is None:
        th_val = "Skipped"
        rec_time = "Skipped"
    else:
        th_val = measure_throughput(net, h_start, h_end)
        rec_time = measure_recovery(net, s_fail_1, s_fail_2, h_start, h_end)
    
    info(f"\n{'='*40}\nLaporan Akhir FAT-TREE ({algo_name})\n{'='*40}\n")
    info(f"Parameter K     : {k} (Total Hosts: {num_hosts})\n")
    info(f"Convergence Time: {conv_time if conv_time else '> Timeout'}\n")
    info(f"Throughput      : {th_val}\n")
    info(f"Recovery Time   : {rec_time}\n")
    info(f"{'='*40}\n")
    
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    
    # --- KONFIGURASI FAT-TREE ---
    # PENTING: Jangan lupa ganti controller di Terminal 1 sesuai algo yang dipilih!
    
    # === SKENARIO 1: BELLMAN-FORD ===
    # Terminal 1: ryu-manager controller_bellman_fattree.py --ofp-tcp-listen-port 6653 --observe-links
    run_fattree_test(k=2, algo_name="BELLMAN_FATTREE")
    # run_fattree_test(k=6, algo_name="BELLMAN_FATTREE")
    # run_fattree_test(k=8, algo_name="BELLMAN_FATTREE")

    # === SKENARIO 2: JOHNSON ===
    # Terminal 1: ryu-manager controller_johnson_fattree.py --ofp-tcp-listen-port 6653 --observe-links
    # run_fattree_test(k=4, algo_name="JOHNSON_FATTREE")
    # run_fattree_test(k=6, algo_name="JOHNSON_FATTREE")
    # run_fattree_test(k=8, algo_name="JOHNSON_FATTREE")

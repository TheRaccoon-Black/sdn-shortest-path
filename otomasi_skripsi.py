import time
import sys
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi import SkripsiTopo 

def measure_convergence(net, target_host_1, target_host_2, timeout=240):
    """
    Mengukur waktu konvergensi dengan timeout yang lebih panjang (default 180s/3menit)
    untuk mengakomodasi topologi besar (100 node).
    """
    info(f"*** [TEST] Mengukur Convergence Time antara {target_host_1.name} dan {target_host_2.name}...\n")
    info(f"*** [INFO] Menunggu maksimal {timeout} detik agar jaringan stabil...\n")
    
    start_time = time.time()
    while True:
        # Gunakan timeout ping ping 1 detik
        result = target_host_1.cmd(f'ping -c 1 -W 1 {target_host_2.IP()}')
        
        if "1 received" in result:
            end_time = time.time()
            duration = end_time - start_time
            info(f"*** [BERHASIL] Jaringan Konvergen dalam {duration:.4f} detik\n")
            return duration
            
        # Cek apakah sudah melewati batas waktu
        if time.time() - start_time > timeout:
            info(f"*** [GAGAL] Timeout Convergence > {timeout} detik.\n")
            return None
            
        time.sleep(1) # Cek setiap 1 detik

def measure_throughput(net, client, server):
    info(f"*** [TEST] Mengukur Throughput antara {client.name} dan {server.name}...\n")
    # Pastikan server iperf mati dulu sebelum mulai
    server.cmd('killall -9 iperf')
    time.sleep(0.5)
    
    server.cmd('iperf -s &')
    # Tunggu server iperf siap
    time.sleep(1)
    
    # Jalankan client
    iperf_output = client.cmd(f'iperf -c {server.IP()} -t 5 -f m') # Durasi naik ke 5 detik
    
    try:
        lines = iperf_output.split('\n')
        # Cari baris yang mengandung Mbits/sec atau Gbits/sec
        result_line = [l for l in lines if 'bits/sec' in l][-1]
        throughput_val = result_line.split()[-2] + " " + result_line.split()[-1]
        server.cmd('killall -9 iperf')
        return throughput_val
    except:
        server.cmd('killall -9 iperf')
        return "N/A"

def measure_recovery(net, s_src, s_dst, h_src, h_dst):
    info(f"*** [TEST] Mengukur Recovery Time (Memutus link {s_src}-{s_dst})...\n")
    
    # Pastikan koneksi awal lancar
    h_src.cmd(f'ping -c 1 {h_dst.IP()}')
    
    # Ping flood background (interval 0.1s)
    h_src.cmd(f'ping -i 0.1 {h_dst.IP()} > ping_log.txt &')
    time.sleep(3) # Tunggu log terisi
    
    info(f"*** [ACTION] Memutus Link {s_src} <-> {s_dst} sekarang!\n")
    start_fail_time = time.time()
    net.configLinkStatus(s_src, s_dst, 'down')
    
    recovered = False
    recovery_duration = 0
    
    # Tunggu recovery maksimal 60 detik (dinaikkan dari 20s)
    max_wait = 60
    while time.time() - start_fail_time < max_wait:
        res = h_src.cmd(f'ping -c 1 -W 1 {h_dst.IP()}')
        if "1 received" in res:
            recovery_duration = time.time() - start_fail_time
            recovered = True
            break
        time.sleep(0.1)
    
    h_src.cmd('killall ping')
    
    if not recovered: 
        return f"> {max_wait}s (Gagal/Tree)"
        
    return recovery_duration

def run_automated_test(topo_type, nodes_or_k):
    info(f"\n{'='*40}\nMEMULAI OTOMASI: {topo_type.upper()} ({nodes_or_k} Nodes/K)\n{'='*40}\n")
    
    if topo_type == 'fattree':
        topo = SkripsiTopo(topo_type=topo_type, k=nodes_or_k)
    else:
        topo = SkripsiTopo(topo_type=topo_type, nodes=nodes_or_k)

    switch_class = partial(OVSKernelSwitch, protocols='OpenFlow13')
    net = Mininet(topo=topo, controller=None, switch=switch_class, link=TCLink)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    
    net.start()
    
    # LOGIKA PENYESUAIAN WAKTU TUNGGU
    # Semakin banyak node, semakin lama Ryu butuh waktu untuk handshake & LLDP discovery
    initial_wait = 15
    if topo_type != 'fattree' and nodes_or_k >= 50:
        initial_wait = 120 # Beri waktu 30 detik untuk 50+ node
    elif topo_type != 'fattree' and nodes_or_k >= 100:
        initial_wait = 120 # Beri waktu 45 detik untuk 100+ node
        
    info(f"*** Jaringan Berjalan. Menunggu {initial_wait} detik agar Controller memetakan {nodes_or_k} node...\n")
    time.sleep(initial_wait)
    
    # Identifikasi Host
    if topo_type == 'fattree':
        pod = nodes_or_k
        num_hosts = (pod ** 3) // 4
        h_start = net.get('h1')
        h_end = net.get(f'h{num_hosts}')
        s_fail_1 = net.switches[0].name 
        s_fail_2 = net.switches[pod].name 
    else:
        h_start = net.get('h1')
        h_end = net.get(f'h{nodes_or_k}') 
        s_fail_1 = 's1'
        s_fail_2 = 's2'

    conv_time = measure_convergence(net, h_start, h_end, timeout=240)
    
    # Jika convergence gagal, throughput dan recovery tidak perlu dijalankan
    if conv_time is None:
        th_val = "Skipped (No Convergence)"
        rec_time = "Skipped (No Convergence)"
    else:
        th_val = measure_throughput(net, h_start, h_end)
        rec_time = measure_recovery(net, s_fail_1, s_fail_2, h_start, h_end)
    
    info(f"\n{'='*40}\nLaporan Akhir {topo_type.upper()}\n{'='*40}\n")
    info(f"Topology: {topo_type} (Scale: {nodes_or_k})\n")
    info(f"Convergence Time: {conv_time if conv_time else '> Timeout'}\n")
    info(f"Throughput      : {th_val}\n")
    info(f"Recovery Time   : {rec_time}\n")
    info(f"{'='*40}\n")
    
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    
    # --- KONFIGURASI PENGUJIAN ---
    # Uncomment salah satu baris di bawah ini untuk menjalankan tes
    
    # run_automated_test('tree', 10)
    # run_automated_test('tree', 50)
    # run_automated_test('tree', 100)

    #run_automated_test('ring', 10)
    #run_automated_test('ring', 50)
    run_automated_test('ring', 100) # <-- Kita tes yang ini sekarang
    
    # run_automated_test('mesh', 10)

    # run_automated_test('fattree', 4)

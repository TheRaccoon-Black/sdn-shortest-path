import time
import sys
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi import SkripsiTopo

def set_ovs_protocol_and_timeout(net, timeout=600):
    """
    UPDATE: Timeout dinaikkan ke 600 detik (10 menit).
    Switch tidak akan disconnect meskipun Ryu macet selama 10 menit.
    """
    info("*** [FIX] Mengatur Inactivity Probe ke {} detik (Ultra Stability)...\n".format(timeout))
    for sw in net.switches:
        sw.cmd('ovs-vsctl set Bridge {} protocols=OpenFlow13'.format(sw.name))
        sw.cmd('ovs-vsctl set-controller {} tcp:127.0.0.1:6653'.format(sw.name))
        sw.cmd('ovs-vsctl set controller {} inactivity_probe={}'.format(sw.name, timeout * 1000))

def measure_convergence(net, target_host_1, target_host_2, timeout=180):
    info(f"*** [TEST] Mengukur Convergence Time antara {target_host_1.name} dan {target_host_2.name}...\n")
    info(f"*** [INFO] Menunggu maksimal {timeout} detik agar jaringan stabil...\n")
    start_time = time.time()
    while True:
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
    iperf_output = client.cmd(f'iperf -c {server.IP()} -t 5 -f m')
    try:
        lines = iperf_output.split('\n')
        result_line = [l for l in lines if 'bits/sec' in l][-1]
        throughput_val = result_line.split()[-2] + " " + result_line.split()[-1]
        server.cmd('killall -9 iperf')
        return throughput_val
    except:
        server.cmd('killall -9 iperf')
        return "N/A"

def measure_recovery(net, s_src, s_dst, h_src, h_dst):
    info(f"*** [TEST] Mengukur Recovery Time (Memutus link {s_src}-{s_dst})...\n")
    h_src.cmd(f'ping -c 1 {h_dst.IP()}')
    h_src.cmd(f'ping -i 0.1 {h_dst.IP()} > ping_log.txt &')
    time.sleep(3)
    info(f"*** [ACTION] Memutus Link {s_src} <-> {s_dst} sekarang!\n")
    start_fail_time = time.time()
    net.configLinkStatus(s_src, s_dst, 'down')
    recovered = False
    recovery_duration = 0
    max_wait = 60
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

def run_mesh_test(nodes_or_k, algo_name="BELLMAN"):
    info(f"\n{'='*40}\nMEMULAI OTOMASI: {algo_name} - MESH ({nodes_or_k} Nodes)\n{'='*40}\n")
   
    topo = SkripsiTopo(topo_type='mesh', nodes=nodes_or_k)
    net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
   
    net.start()
   
    # FIX KRUSIAL: Set Timeout Ekstrem (600 detik)
    set_ovs_protocol_and_timeout(net, timeout=600)
   
    initial_wait = 15
    if nodes_or_k >= 20: initial_wait = 90
   
    # UPDATE EKSTREM: 30 Menit untuk 50 Node agar Ryu bisa napas
    if nodes_or_k >= 50: initial_wait = 600
    # UPDATE EKSTREM: 1 Jam untuk 100 Node
    if nodes_or_k >= 100: initial_wait = 3600
       
    info(f"*** Menunggu {initial_wait} detik agar Controller memetakan topologi...\n")
    time.sleep(initial_wait)
   
    h_start = net.get('h1')
    h_end = net.get(f'h{nodes_or_k}')
    s_fail_1 = 's1'
    s_fail_2 = 's2'

    ping_timeout = 180
    if nodes_or_k >= 50: ping_timeout = 1400

    conv_time = measure_convergence(net, h_start, h_end, timeout=ping_timeout)
   
    if conv_time is None:
        th_val = "Skipped"
        rec_time = "Skipped"
    else:
        th_val = measure_throughput(net, h_start, h_end)
        rec_time = measure_recovery(net, s_fail_1, s_fail_2, h_start, h_end)
   
    info(f"\n{'='*40}\nLaporan Akhir {algo_name} - MESH\n{'='*40}\n")
    info(f"Scale           : {nodes_or_k} Nodes\n")
    info(f"Convergence Time: {conv_time if conv_time else '> Timeout'}\n")
    info(f"Throughput      : {th_val}\n")
    info(f"Recovery Time   : {rec_time}\n")
    info(f"{'='*40}\n")
   
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
   
    # Jalankan MESH 50 Node
    run_mesh_test(50, algo_name="BELLMAN")
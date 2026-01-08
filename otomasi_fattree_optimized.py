import time
import sys
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi import SkripsiTopo 

def set_ovs_protocol_and_timeout(net, timeout=300):
    """
    Mengatur protokol OpenFlow 1.3 dan timeout yang panjang.
    OPTIMASI TAMBAHAN: Disable in-band control untuk stabilitas.
    """
    info(f"*** [FIX] Mengatur OVS untuk stabilitas maksimal...\n")
    for sw in net.switches:
        try:
            # Set OpenFlow 1.3
            sw.cmd(f'ovs-vsctl set Bridge {sw.name} protocols=OpenFlow13')
            
            # Set controller dengan timeout panjang
            sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6653')
            sw.cmd(f'ovs-vsctl set controller {sw.name} inactivity_probe={timeout * 1000}')
            
            # PENTING: Set max-backoff untuk reconnection
            sw.cmd(f'ovs-vsctl set controller {sw.name} max-backoff=1000')
            
            # Disable fail mode standalone (agar tidak flooding saat disconnect)
            sw.cmd(f'ovs-vsctl set-fail-mode {sw.name} secure')
        except Exception as e:
            info(f"*** [WARN] Error configuring {sw.name}: {e}\n")

def wait_for_topology_ready(net, target_switches, max_wait=300):
    """
    Tunggu hingga semua switch terhubung ke controller.
    Lebih reliable daripada sleep fixed.
    """
    info(f"*** [WAIT] Menunggu {target_switches} switches terhubung ke controller...\n")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        connected = 0
        for sw in net.switches:
            result = sw.cmd('ovs-vsctl show')
            if 'is_connected: true' in result:
                connected += 1
        
        if connected >= target_switches:
            elapsed = time.time() - start_time
            info(f"*** [SUCCESS] {connected}/{target_switches} switches connected dalam {elapsed:.1f} detik\n")
            # Tunggu extra 30 detik untuk topology discovery
            info("*** [WAIT] Extra 30 detik untuk topology mapping...\n")
            time.sleep(30)
            return True
        
        time.sleep(2)
    
    info(f"*** [WARNING] Timeout setelah {max_wait} detik\n")
    return False

def measure_convergence(net, target_host_1, target_host_2, timeout=180):
    info(f"*** [TEST] Mengukur Convergence Time antara {target_host_1.name} dan {target_host_2.name}...\n")
    
    start_time = time.time()
    ping_count = 0
    
    while True:
        result = target_host_1.cmd(f'ping -c 1 -W 1 {target_host_2.IP()}')
        ping_count += 1
        
        if "1 received" in result:
            end_time = time.time()
            conv_time = end_time - start_time
            info(f"*** [SUCCESS] Convergence achieved after {ping_count} pings ({conv_time:.2f}s)\n")
            return conv_time
        
        if time.time() - start_time > timeout:
            info(f"*** [FAIL] Timeout Convergence > {timeout} detik.\n")
            return None
        
        time.sleep(1)

def measure_throughput(net, client, server):
    info(f"*** [TEST] Mengukur Throughput antara {client.name} dan {server.name}...\n")
    
    # Kill existing iperf
    server.cmd('killall -9 iperf 2>/dev/null')
    client.cmd('killall -9 iperf 2>/dev/null')
    time.sleep(1)
    
    # Start iperf server
    server.cmd('iperf -s &')
    time.sleep(2)
    
    # Run iperf client (10 detik untuk hasil lebih stabil)
    iperf_output = client.cmd(f'iperf -c {server.IP()} -t 10 -f m')
    
    try:
        lines = iperf_output.split('\n')
        result_line = [l for l in lines if 'bits/sec' in l][-1]
        throughput_val = result_line.split()[-2] + " " + result_line.split()[-1]
        info(f"*** [RESULT] Throughput: {throughput_val}\n")
    except:
        throughput_val = "N/A (iperf failed)"
    
    # Cleanup
    server.cmd('killall -9 iperf 2>/dev/null')
    return throughput_val

def measure_recovery(net, s_src, s_dst, h_src, h_dst, max_wait=120):
    info(f"*** [TEST] Mengukur Recovery Time (Link Failure {s_src}-{s_dst})...\n")
    
    # Verify koneksi awal
    info("*** [VERIFY] Checking initial connectivity...\n")
    result = h_src.cmd(f'ping -c 3 -W 1 {h_dst.IP()}')
    if "3 received" not in result:
        info("*** [WARNING] Initial connectivity check failed!\n")
        return "N/A (No initial connectivity)"
    
    # Start background ping dengan interval 0.2s
    h_src.cmd(f'ping -i 0.2 {h_dst.IP()} > /tmp/ping_recovery.txt &')
    time.sleep(2)
    
    info(f"*** [ACTION] Breaking link {s_src} <-> {s_dst}...\n")
    fail_time = time.time()
    
    # Break link
    net.configLinkStatus(s_src, s_dst, 'down')
    time.sleep(1)
    
    # Wait for recovery
    recovered = False
    recovery_time = 0
    
    while time.time() - fail_time < max_wait:
        result = h_src.cmd(f'ping -c 1 -W 1 {h_dst.IP()}')
        if "1 received" in result:
            recovery_time = time.time() - fail_time
            recovered = True
            info(f"*** [SUCCESS] Recovery achieved in {recovery_time:.2f}s\n")
            break
        time.sleep(0.5)
    
    # Stop ping
    h_src.cmd('killall -9 ping 2>/dev/null')
    
    if not recovered:
        return f"> {max_wait}s (Failed)"
    
    return f"{recovery_time:.2f}s"

def run_fattree_test(k, algo_name="TEST"):
    info(f"\n{'='*60}\n")
    info(f"  FAT-TREE TEST: {algo_name} (K={k})\n")
    info(f"{'='*60}\n")
    
    # Calculate topology size
    num_hosts = (k ** 3) // 4
    num_switches = 5 * (k ** 2) // 4
    
    info(f"Topology: {num_hosts} hosts, {num_switches} switches\n")
    
    # Build topology
    topo = SkripsiTopo(topo_type='fattree', k=k)
    net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    
    info("*** Starting network...\n")
    net.start()
    
    # Apply OVS optimizations
    # NOTE: Disabled because it causes switches to disconnect
    # set_ovs_protocol_and_timeout(net, timeout=300)
    
    # Wait for topology ready (smart wait)
    if not wait_for_topology_ready(net, num_switches, max_wait=180):
        info("*** [ERROR] Network failed to initialize properly\n")
        net.stop()
        return
    
    # Select test hosts (furthest apart)
    h_start = net.get('h1')
    h_end = net.get(f'h{num_hosts}')
    
    # DEBUG: Check host configurations
    info(f"*** [DEBUG] h1 IP: {h_start.IP()}, MAC: {h_start.MAC()}\n")
    info(f"*** [DEBUG] h{num_hosts} IP: {h_end.IP()}, MAC: {h_end.MAC()}\n")
    info(f"*** [DEBUG] Testing h1 -> h{num_hosts} direct ping...\n")
    result = h_start.cmd(f'ping -c 1 -W 2 {h_end.IP()}')
    info(f"*** [DEBUG] Ping result: {result}\n")
    
    # Select switches for failure test
    try:
        s_fail_1 = 'e0_0'
        s_fail_2 = 'a0_0'
        info(f"*** Link failure target: {s_fail_1} <-> {s_fail_2}\n")
    except:
        s_fail_1 = net.switches[0].name
        s_fail_2 = net.switches[1].name
    
    # Run tests
    info("\n" + "="*60 + "\n")
    info("STARTING MEASUREMENTS\n")
    info("="*60 + "\n")
    
    conv_time = measure_convergence(net, h_start, h_end, timeout=180)
    
    if conv_time is None:
        th_val = "Skipped (No convergence)"
        rec_time = "Skipped (No convergence)"
    else:
        # Extra wait untuk flow table stabil
        info("*** [WAIT] Letting flows stabilize (10s)...\n")
        time.sleep(10)
        
        th_val = measure_throughput(net, h_start, h_end)
        rec_time = measure_recovery(net, s_fail_1, s_fail_2, h_start, h_end)
    
    # Print results
    info("\n" + "="*60 + "\n")
    info(f"HASIL PENGUJIAN FAT-TREE - {algo_name}\n")
    info("="*60 + "\n")
    info(f"Parameter K       : {k}\n")
    info(f"Total Hosts       : {num_hosts}\n")
    info(f"Total Switches    : {num_switches}\n")
    info(f"Convergence Time  : {conv_time if conv_time else 'TIMEOUT'}\n")
    info(f"Throughput        : {th_val}\n")
    info(f"Recovery Time     : {rec_time}\n")
    info("="*60 + "\n")
    
    info("*** Stopping network...\n")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    
    # === BELLMAN-FORD FAT-TREE TEST ===
    # Terminal 1: ryu-manager controller_bellman_fattree_fixed.py --ofp-tcp-listen-port 6653
    # Terminal 2: sudo python3 otomasi_fattree_optimized.py
    
    run_fattree_test(k=4, algo_name="BELLMAN_FORD_FATTREE")
    
    # Uncomment untuk test K=6 (setelah K=4 sukses)
    # run_fattree_test(k=6, algo_name="BELLMAN_FORD_FATTREE")

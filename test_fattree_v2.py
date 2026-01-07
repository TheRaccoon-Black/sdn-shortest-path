import time
import sys
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from skrip_topologi_v2 import SkripsiTopo 

def set_ovs_protocol_and_timeout(net, timeout=600):
    """Configure OVS switches for OpenFlow 1.3 with long timeout"""
    info("*** [CONFIG] Configuring switches for stability...\n")
    for sw in net.switches:
        # Set OpenFlow 1.3
        sw.cmd('ovs-vsctl set Bridge {} protocols=OpenFlow13'.format(sw.name))
        # Set controller
        sw.cmd('ovs-vsctl set-controller {} tcp:127.0.0.1:6653'.format(sw.name))
        # Set inactivity probe (prevent disconnection)
        sw.cmd('ovs-vsctl set controller {} inactivity_probe={}'.format(sw.name, timeout * 1000))
        # Set connection mode to prevent multiple connections
        sw.cmd('ovs-vsctl set controller {} connection-mode=out-of-band'.format(sw.name))
    
    info("*** [CONFIG] Switch configuration complete\n")
    time.sleep(5)

def verify_connectivity(net):
    """Verify all switches are properly connected"""
    info("*** [VERIFY] Checking switch connectivity...\n")
    for sw in net.switches:
        result = sw.cmd('ovs-vsctl show')
        if 'is_connected: true' not in result:
            info(f"*** [WARNING] Switch {sw.name} not fully connected\n")
            return False
    info("*** [VERIFY] All switches connected\n")
    return True

def measure_convergence(net, target_host_1, target_host_2, timeout=300):
    """Measure time for network to converge"""
    info(f"*** [TEST] Measuring Convergence Time: {target_host_1.name} -> {target_host_2.name}\n")
    info(f"*** [INFO] Waiting max {timeout} seconds for network stability...\n")
    
    start_time = time.time()
    success_count = 0
    required_successes = 3  # Need 3 consecutive successes
    
    while True:
        result = target_host_1.cmd(f'ping -c 1 -W 2 {target_host_2.IP()}')
        
        if "1 received" in result:
            success_count += 1
            if success_count >= required_successes:
                end_time = time.time()
                conv_time = end_time - start_time
                info(f"*** [SUCCESS] Network converged in {conv_time:.2f} seconds\n")
                return conv_time
        else:
            success_count = 0  # Reset on failure
        
        elapsed = time.time() - start_time
        if elapsed > timeout:
            info(f"*** [FAILED] Timeout after {timeout} seconds\n")
            return None
        
        if int(elapsed) % 30 == 0 and int(elapsed) > 0:  # Progress update every 30s
            info(f"*** [PROGRESS] {int(elapsed)}s elapsed, still waiting...\n")
        
        time.sleep(2)

def measure_throughput(net, client, server):
    """Measure network throughput using iperf"""
    info(f"*** [TEST] Measuring Throughput: {client.name} -> {server.name}\n")
    
    # Cleanup any existing iperf
    server.cmd('killall -9 iperf 2>/dev/null')
    time.sleep(1)
    
    # Start iperf server
    server.cmd('iperf -s &')
    time.sleep(2)
    
    # Run iperf client
    iperf_output = client.cmd(f'iperf -c {server.IP()} -t 10 -f m')
    
    try:
        lines = iperf_output.split('\n')
        result_line = [l for l in lines if 'bits/sec' in l][-1]
        throughput_val = result_line.split()[-2] + " " + result_line.split()[-1]
        server.cmd('killall -9 iperf 2>/dev/null')
        info(f"*** [RESULT] Throughput: {throughput_val}\n")
        return throughput_val
    except Exception as e:
        server.cmd('killall -9 iperf 2>/dev/null')
        info(f"*** [ERROR] Throughput measurement failed: {e}\n")
        return "N/A"

def measure_recovery(net, s_src, s_dst, h_src, h_dst):
    """Measure recovery time after link failure"""
    info(f"*** [TEST] Measuring Recovery Time (Link: {s_src} <-> {s_dst})\n")
    
    # Verify connectivity first
    result = h_src.cmd(f'ping -c 3 -W 2 {h_dst.IP()}')
    if "3 received" not in result:
        info("*** [ERROR] No initial connectivity for recovery test\n")
        return "No Initial Connectivity"
    
    # Start background ping
    h_src.cmd(f'ping -i 0.1 {h_dst.IP()} > /tmp/ping_recovery.txt 2>&1 &')
    time.sleep(3)
    
    info(f"*** [ACTION] Breaking link: {s_src} <-> {s_dst}\n")
    start_fail_time = time.time()
    net.configLinkStatus(s_src, s_dst, 'down')
    
    # Wait for recovery
    recovered = False
    recovery_duration = 0
    max_wait = 60
    
    while time.time() - start_fail_time < max_wait:
        res = h_src.cmd(f'ping -c 1 -W 1 {h_dst.IP()}')
        if "1 received" in res:
            recovery_duration = time.time() - start_fail_time
            recovered = True
            info(f"*** [SUCCESS] Network recovered in {recovery_duration:.2f}s\n")
            break
        time.sleep(0.2)
    
    # Stop ping
    h_src.cmd('killall ping 2>/dev/null')
    
    # Restore link
    net.configLinkStatus(s_src, s_dst, 'up')
    time.sleep(2)
    
    if not recovered:
        info(f"*** [FAILED] No recovery after {max_wait}s\n")
        return f"> {max_wait}s"
    
    return f"{recovery_duration:.2f}s"

def run_fattree_test(k, algo_name="JOHNSON_V2"):
    """Main test function for Fat-Tree topology"""
    info(f"\n{'='*60}\n")
    info(f"FAT-TREE TEST V2: {algo_name} (K={k})\n")
    info(f"{'='*60}\n\n")
    
    # Create topology
    topo = SkripsiTopo(topo_type='fattree', k=k)
    net = Mininet(topo=topo, controller=None, switch=OVSKernelSwitch, link=TCLink)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    
    info("*** Starting Mininet\n")
    net.start()
    
    # Configure switches
    set_ovs_protocol_and_timeout(net, timeout=600)
    
    # Verify connectivity
    if not verify_connectivity(net):
        info("*** [WARNING] Some switches not properly connected\n")
    
    # Calculate wait time based on K
    # Fat-Tree needs: 3 stability checks × 30s interval = 90s minimum
    # Plus computation time: ~30-60s for Johnson
    # Total: 120-180s for K=4
    initial_wait = 180  # 3 minutes for K=4
    if k >= 6: 
        initial_wait = 360  # 6 minutes for K=6
    if k >= 8: 
        initial_wait = 900  # 15 minutes for K=8
    
    info(f"*** [WAIT] Allowing {initial_wait}s for controller to stabilize...\n")
    info(f"*** [INFO] Controller needs:\n")
    info(f"***        - Topology discovery\n")
    info(f"***        - 3x 30s stability checks\n")
    info(f"***        - Johnson algorithm computation\n")
    
    # Progress updates during wait
    for i in range(initial_wait // 30):
        time.sleep(30)
        elapsed = (i+1)*30
        info(f"*** [PROGRESS] {elapsed}s / {initial_wait}s elapsed...\n")
    
    # Select test hosts
    num_hosts = (k ** 3) // 4
    h_start = net.get('h1')
    h_end = net.get(f'h{num_hosts}')
    
    info(f"*** [INFO] Testing connectivity: {h_start.name} <-> {h_end.name}\n")
    
    # Select switches for recovery test
    try:
        s_fail_1 = net.get('e0_0').name
        s_fail_2 = net.get('a0_0').name
    except:
        s_fail_1 = net.switches[0].name
        s_fail_2 = net.switches[1].name if len(net.switches) > 1 else net.switches[0].name
    
    # Run tests
    ping_timeout = 300  # 5 minutes
    if k >= 8: 
        ping_timeout = 600  # 10 minutes for large topologies
    
    conv_time = measure_convergence(net, h_start, h_end, timeout=ping_timeout)
    
    if conv_time is None:
        th_val = "Skipped (No Convergence)"
        rec_time = "Skipped (No Convergence)"
    else:
        th_val = measure_throughput(net, h_start, h_end)
        rec_time = measure_recovery(net, s_fail_1, s_fail_2, h_start, h_end)
    
    # Final report
    info(f"\n{'='*60}\n")
    info(f"FINAL REPORT: FAT-TREE {algo_name}\n")
    info(f"{'='*60}\n")
    info(f"Scale (K)           : {k}\n")
    info(f"Number of Hosts     : {num_hosts}\n")
    info(f"Number of Switches  : {len(net.switches)}\n")
    info(f"Convergence Time    : {f'{conv_time:.2f}s' if conv_time else 'TIMEOUT'}\n")
    info(f"Throughput          : {th_val}\n")
    info(f"Recovery Time       : {rec_time}\n")
    info(f"{'='*60}\n\n")
    
    # Cleanup
    info("*** Stopping network\n")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    
    # Run test
    run_fattree_test(k=4, algo_name="JOHNSON_FATTREE_V2")
# Fat-Tree SDN Controller V2 - Complete Package

## 📦 File List

1. **skrip_topologi_v2.py** - Topology generator (FIXED Fat-Tree)
2. **controller_fattree_johnson_v2.py** - SDN Controller (Ultra-stable version)
3. **test_fattree_v2.py** - Automated test script
4. **fix_ovs_v2.sh** - Fix script untuk multiple connections
5. **diagnose_ovs_v2.sh** - Diagnostic tool
6. **README_V2.md** - This file

## 🔧 Setup

```bash
# Make scripts executable
chmod +x fix_ovs_v2.sh
chmod +x diagnose_ovs_v2.sh

# Verify all files are present
ls -l *_v2.*
```

## 🚀 Quick Start

### Step 1: Clean Environment
```bash
sudo ./fix_ovs_v2.sh
```

### Step 2: Start Controller
```bash
# Terminal 1
ryu-manager controller_fattree_johnson_v2.py
```

**Wait for these logs:**
```
Johnson Fat-Tree Controller V2 - Ultra-Stable
>>> [CONNECT] Switch 0000000000000001 connected
>>> [CONNECT] Switch 0000000000000002 connected
...
>>> [STATS] Switches: 20, Links: 48, Hosts: 0, Status: STABILIZING (1/3)
>>> [READY] ✓ Network ready for traffic!
```

### Step 3: Run Test
```bash
# Terminal 2 (after controller shows READY)
sudo python3 test_fattree_v2.py
```

## 📊 Expected Results

### For K=4 Fat-Tree:
- **Switches**: 20 (4 core + 8 aggregation + 8 edge)
- **Hosts**: 16 
- **Links**: 48 (bidirectional)
- **Convergence Time**: < 180 seconds
- **Throughput**: Should show Mbits/sec
- **Recovery Time**: < 60 seconds

## 🐛 Troubleshooting

### If you see "Multiple connections"
```bash
# Run diagnostic
./diagnose_ovs_v2.sh

# If issues found, run fix
sudo ./fix_ovs_v2.sh

# Restart controller
ryu-manager controller_fattree_johnson_v2.py
```

### If convergence timeout
Check controller logs for:
- `>>> [STAB] Waiting for stability (X/3)` - Should reach 3/3
- `>>> [COMPUTE] Topology stable!` - Should appear
- `>>> [READY] ✓ Network ready for traffic!` - Must appear before test

### If topology wrong size
```bash
# Verify topology creation
sudo python3 -c "from skrip_topologi_v2 import SkripsiTopo; SkripsiTopo(topo_type='fattree', k=4)"
```

Should show:
```
*** Fat-Tree Parameters:
    K = 4
    Pods = 4
    Core Switches = 4
    Aggregation Switches = 8 (2 per pod)
    Edge Switches = 8 (2 per pod)
    Total Switches = 20
    Hosts = 16 (2 per edge)
```

## 🔍 What's Fixed in V2

### Topology (skrip_topologi_v2.py):
- ✅ Correct core switch count: (k/2)² = 4 for k=4
- ✅ Correct aggregation/edge per pod: k/2 = 2
- ✅ Correct hosts per edge: k/2 = 2
- ✅ Proper IP addressing: 10.{pod}.{edge}.{host}/24
- ✅ MAC address assignment
- ✅ autoSetMacs and autoStaticArp enabled

### Controller (controller_fattree_johnson_v2.py):
- ✅ Connection tracking to prevent duplicate connections
- ✅ Stability checking (3 consecutive identical topology readings)
- ✅ Background computation with thread pool
- ✅ MST-based intelligent flooding
- ✅ Statistics monitoring every 10s
- ✅ Proper flow cleanup on switch connect

### Test Script (test_fattree_v2.py):
- ✅ Proper wait time: 180s for k=4
- ✅ Progress updates every 30s
- ✅ 3 consecutive ping success requirement
- ✅ Better error handling
- ✅ Comprehensive reporting

## 📈 Performance Tips

### For larger topologies (K=6, K=8):
Edit `test_fattree_v2.py`:
```python
run_fattree_test(k=6, algo_name="JOHNSON_FATTREE_V2")  # 6 minutes wait
run_fattree_test(k=8, algo_name="JOHNSON_FATTREE_V2")  # 15 minutes wait
```

### Monitor controller in real-time:
```bash
# Terminal 3
watch -n 2 'grep -E "STATS|READY|STAB" /tmp/ryu_controller.log'
```

## 📝 Understanding the Logs

### Good Signs:
```
>>> [CONNECT] Switch ... connected          ← Switch connecting
>>> [STAB] ... (3/3)                        ← Stability achieved
>>> [COMPUTE] Topology stable!              ← Starting computation
>>> [MST] Done: 19 edges in X.XXs           ← MST computed
>>> [JOHNSON] Done: XXX routes in X.XXs     ← Routes computed
>>> [READY] ✓ Network ready for traffic!   ← System ready!
```

### Bad Signs:
```
>>> [DUPLICATE] Switch ... reconnect        ← Multiple connection issue
>>> [DISCONNECT] Switch ... disconnected    ← Switch dropped
>>> [ERROR] Route computation failed        ← Algorithm error
>>> [WARNING] Graph not connected           ← Topology incomplete
```

## 🎯 Testing Checklist

- [ ] Run `diagnose_ovs_v2.sh` - should be clean
- [ ] Run `fix_ovs_v2.sh` - clean environment
- [ ] Start controller - wait for "READY" message
- [ ] Check logs - should see 20 switches, 48 links
- [ ] Run test - should complete in < 10 minutes
- [ ] Check results - convergence time should be < 180s

## 💡 Tips

1. **Always clean before testing**: `sudo ./fix_ovs_v2.sh`
2. **Wait for READY**: Don't start test until controller logs show ready
3. **Monitor both terminals**: Watch controller and test output
4. **Be patient**: K=4 needs ~3-5 minutes to stabilize
5. **Check topology first**: Verify switch/link count in controller logs

## 📞 Support

If issues persist:
1. Save controller logs: `ryu-manager ... > controller.log 2>&1`
2. Save test output: `sudo python3 test_fattree_v2.py > test.log 2>&1`
3. Run diagnostic: `./diagnose_ovs_v2.sh > diagnostic.txt`
4. Check all three files for errors

## 🎓 Educational Notes

### Johnson's Algorithm:
- Computes all-pairs shortest paths
- Works with negative weights
- O(V²log V + VE) complexity
- Better than Floyd-Warshall for sparse graphs

### Fat-Tree Topology:
- Provides full bisection bandwidth
- Non-blocking for specific traffic patterns
- Commonly used in data centers
- Scales predictably with k parameter

Good luck with your testing! 🚀
#!/bin/bash
# Diagnostic tool V2 untuk troubleshooting

echo "========================================="
echo "OVS & Ryu Diagnostic Tool V2"
echo "========================================="
echo ""

# Check OVS service
echo "1. OVS Service Status:"
sudo systemctl status openvswitch-switch | grep -E "Active:|Loaded:"
echo ""

# Check for running controllers
echo "2. Running Ryu Controllers:"
CONTROLLERS=$(ps aux | grep ryu-manager | grep -v grep | wc -l)
if [ "$CONTROLLERS" -eq "0" ]; then
    echo "  ✓ No controllers running"
else
    echo "  Found $CONTROLLERS controller(s):"
    ps aux | grep ryu-manager | grep -v grep | awk '{print "    PID: "$2, "CMD:", $11, $12, $13}'
fi
echo ""

# Check OVS bridges
echo "3. OVS Bridges:"
BRIDGES=$(sudo ovs-vsctl list-br 2>/dev/null | wc -l)
if [ "$BRIDGES" -eq "0" ]; then
    echo "  ✓ No bridges (clean state)"
else
    echo "  Found $BRIDGES bridge(s):"
    sudo ovs-vsctl list-br | while read br; do
        echo "    - $br"
        sudo ovs-vsctl list-ports $br 2>/dev/null | sed 's/^/      Port: /'
    done
fi
echo ""

# Check controller connections
echo "4. Controller Connections:"
for br in $(sudo ovs-vsctl list-br 2>/dev/null); do
    echo "  Bridge: $br"
    CTRL=$(sudo ovs-vsctl get-controller $br 2>/dev/null)
    if [ -z "$CTRL" ]; then
        echo "    No controller set"
    else
        echo "    Controller: $CTRL"
    fi
done
echo ""

# Check active connections to port 6653
echo "5. Active Connections to Port 6653:"
CONNS=$(sudo netstat -antp 2>/dev/null | grep 6653 | wc -l)
if [ "$CONNS" -eq "0" ]; then
    echo "  ✓ No connections"
else
    echo "  Found $CONNS connection(s):"
    sudo netstat -antp 2>/dev/null | grep 6653
fi
echo ""

# Check recent OVS errors
echo "6. Recent OVS Errors (last 10 lines):"
if [ -f /var/log/openvswitch/ovs-vswitchd.log ]; then
    sudo tail -10 /var/log/openvswitch/ovs-vswitchd.log | grep -i "error\|warn" | tail -5
else
    echo "  Log file not found"
fi
echo ""

# Check kernel modules
echo "7. OVS Kernel Module:"
KMOD=$(lsmod | grep openvswitch | wc -l)
if [ "$KMOD" -eq "0" ]; then
    echo "  ✗ Module not loaded!"
else
    echo "  ✓ Module loaded"
    lsmod | grep openvswitch | awk '{print "    "$1, "- Used by:", $3}'
fi
echo ""

# Summary
echo "========================================="
echo "DIAGNOSTIC SUMMARY"
echo "========================================="
echo ""
if [ "$BRIDGES" -eq "0" ] && [ "$CONNS" -eq "0" ]; then
    echo "✓ System is CLEAN - Ready to run tests"
    echo ""
    echo "Next steps:"
    echo "  1. ryu-manager controller_fattree_johnson_v2.py"
    echo "  2. sudo python3 test_fattree_v2.py"
else
    echo "⚠ System needs cleaning"
    echo ""
    echo "Run: sudo ./fix_ovs_v2.sh"
fi
echo ""
#!/bin/bash
# Fix script V2 untuk masalah "Multiple connections"

echo "========================================="
echo "OVS Multiple Connection Fix V2"
echo "========================================="
echo ""

# Step 1: Kill all Ryu controllers
echo "Step 1: Stopping all Ryu controllers..."
sudo pkill -9 ryu-manager
sleep 2
echo "  ✓ Done"
echo ""

# Step 2: Clean Mininet completely
echo "Step 2: Cleaning Mininet..."
sudo mn -c
sleep 2
echo "  ✓ Done"
echo ""

# Step 3: Remove all OVS bridges
echo "Step 3: Removing all OVS bridges..."
for br in $(sudo ovs-vsctl list-br 2>/dev/null); do
    echo "  - Deleting bridge: $br"
    sudo ovs-vsctl del-br $br
done
sleep 1
echo "  ✓ Done"
echo ""

# Step 4: Restart OVS service
echo "Step 4: Restarting OVS service..."
sudo systemctl stop openvswitch-switch
sleep 2
sudo systemctl start openvswitch-switch
sleep 3
echo "  ✓ Done"
echo ""

# Step 5: Verify OVS is clean
echo "Step 5: Verifying clean state..."
BRIDGES=$(sudo ovs-vsctl list-br 2>/dev/null | wc -l)
if [ "$BRIDGES" -eq "0" ]; then
    echo "  ✓ No bridges found (clean)"
else
    echo "  ✗ Warning: $BRIDGES bridge(s) still exist"
fi
echo ""

# Step 6: Kill zombie connections on port 6653
echo "Step 6: Killing zombie connections on port 6653..."
sudo fuser -k 6653/tcp 2>/dev/null
sleep 1
echo "  ✓ Done"
echo ""

# Step 7: Check OVS status
echo "Step 7: Checking OVS status..."
sudo systemctl status openvswitch-switch | grep -E "Active:" || echo "  ✗ OVS not running!"
echo ""

echo "========================================="
echo "FIX COMPLETE"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Terminal 1: ryu-manager controller_fattree_johnson_v2.py"
echo "  2. Terminal 2: sudo python3 test_fattree_v2.py"
echo ""
echo "Expected controller log:"
echo "  >>> [CONNECT] Switch ... connected"
echo "  >>> [STATS] Switches: 20, Links: 48, Status: STABILIZING"
echo "  >>> [READY] ✓ Network ready for traffic!"
echo ""
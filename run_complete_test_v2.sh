#!/bin/bash
# Complete automated test runner V2

echo "========================================="
echo "Fat-Tree SDN Complete Test V2"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root for test
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: This script must be run with sudo${NC}"
    echo "Usage: sudo ./run_complete_test_v2.sh"
    exit 1
fi

# Step 1: Check all files exist
echo "Step 1: Checking required files..."
FILES=(
    "skrip_topologi_v2.py"
    "controller_fattree_johnson_v2.py"
    "test_fattree_v2.py"
    "fix_ovs_v2.sh"
    "diagnose_ovs_v2.sh"
)

MISSING=0
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "  ${GREEN}✓${NC} $file"
    else
        echo -e "  ${RED}✗${NC} $file - MISSING!"
        MISSING=1
    fi
done

if [ $MISSING -eq 1 ]; then
    echo -e "${RED}Error: Some files are missing!${NC}"
    exit 1
fi
echo ""

# Step 2: Make scripts executable
echo "Step 2: Making scripts executable..."
chmod +x fix_ovs_v2.sh diagnose_ovs_v2.sh run_complete_test_v2.sh
echo -e "  ${GREEN}✓${NC} Done"
echo ""

# Step 3: Run diagnostic
echo "Step 3: Running diagnostic..."
./diagnose_ovs_v2.sh | grep -E "CLEAN|needs cleaning"
echo ""

# Step 4: Clean environment
echo "Step 4: Cleaning environment..."
./fix_ovs_v2.sh > /tmp/fix_ovs.log 2>&1
if [ $? -eq 0 ]; then
    echo -e "  ${GREEN}✓${NC} Environment cleaned"
else
    echo -e "  ${RED}✗${NC} Cleaning failed, check /tmp/fix_ovs.log"
    exit 1
fi
echo ""

# Step 5: Check if controller is already running
echo "Step 5: Checking for existing controller..."
CTRL_PID=$(pgrep -f "controller_fattree_johnson_v2.py")
if [ -n "$CTRL_PID" ]; then
    echo -e "  ${YELLOW}⚠${NC} Controller already running (PID: $CTRL_PID)"
    echo "  Killing existing controller..."
    kill -9 $CTRL_PID 2>/dev/null
    sleep 2
fi
echo -e "  ${GREEN}✓${NC} No controller running"
echo ""

# Step 6: Start controller in background
echo "Step 6: Starting controller..."
echo "  Log file: /tmp/controller_v2.log"
ryu-manager controller_fattree_johnson_v2.py > /tmp/controller_v2.log 2>&1 &
CTRL_PID=$!
echo -e "  ${GREEN}✓${NC} Controller started (PID: $CTRL_PID)"
echo ""

# Step 7: Wait for controller to be ready
echo "Step 7: Waiting for controller to initialize..."
echo "  This may take 2-3 minutes..."

MAX_WAIT=180  # 3 minutes
ELAPSED=0
READY=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    # Check if controller is still running
    if ! kill -0 $CTRL_PID 2>/dev/null; then
        echo -e "  ${RED}✗${NC} Controller died! Check /tmp/controller_v2.log"
        exit 1
    fi
    
    # Check for READY message
    if grep -q "Network ready for traffic" /tmp/controller_v2.log 2>/dev/null; then
        READY=1
        break
    fi
    
    # Progress indicator
    if [ $((ELAPSED % 30)) -eq 0 ]; then
        SWITCHES=$(grep -c "CONNECT.*Switch" /tmp/controller_v2.log 2>/dev/null || echo 0)
        STAB=$(grep "STAB.*Waiting for stability" /tmp/controller_v2.log 2>/dev/null | tail -1 | grep -oP '\(\K[0-9]+' || echo 0)
        echo "  ${ELAPSED}s: Switches=$SWITCHES, Stability=$STAB/3"
    fi
    
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ $READY -eq 1 ]; then
    echo -e "  ${GREEN}✓${NC} Controller ready!"
    
    # Show summary
    SWITCHES=$(grep -c "CONNECT.*Switch" /tmp/controller_v2.log)
    echo "  Summary from controller:"
    grep "STATS" /tmp/controller_v2.log | tail -1
else
    echo -e "  ${RED}✗${NC} Controller not ready after ${MAX_WAIT}s"
    echo "  Last 10 lines of log:"
    tail -10 /tmp/controller_v2.log
    echo ""
    echo "Killing controller..."
    kill -9 $CTRL_PID 2>/dev/null
    exit 1
fi
echo ""

# Step 8: Run test
echo "Step 8: Running Fat-Tree test..."
echo "  Log file: /tmp/test_v2.log"
echo "  This will take 5-10 minutes..."
echo ""

python3 test_fattree_v2.py > /tmp/test_v2.log 2>&1
TEST_RESULT=$?

echo ""
echo "========================================="
echo "TEST COMPLETE"
echo "========================================="
echo ""

# Step 9: Show results
if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}✓ Test completed successfully${NC}"
else
    echo -e "${YELLOW}⚠ Test completed with warnings${NC}"
fi
echo ""

# Extract key results
echo "Results:"
grep "Convergence Time" /tmp/test_v2.log | tail -1
grep "Throughput" /tmp/test_v2.log | tail -1
grep "Recovery Time" /tmp/test_v2.log | tail -1
echo ""

# Step 10: Cleanup
echo "Step 10: Cleanup..."
echo "  Stopping controller (PID: $CTRL_PID)..."
kill -9 $CTRL_PID 2>/dev/null
sleep 1
mn -c > /dev/null 2>&1
echo -e "  ${GREEN}✓${NC} Cleanup done"
echo ""

echo "========================================="
echo "SUMMARY"
echo "========================================="
echo "Controller log: /tmp/controller_v2.log"
echo "Test log:       /tmp/test_v2.log"
echo ""
echo "To view full logs:"
echo "  less /tmp/controller_v2.log"
echo "  less /tmp/test_v2.log"
echo ""
echo "To view final report:"
echo "  grep -A 10 'FINAL REPORT' /tmp/test_v2.log"
echo ""
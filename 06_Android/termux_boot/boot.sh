#!/bin/sh
# Termux:Boot startup script
# Starts the Android queue processor monitor

LOG_FILE="/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/state/boot.log"
SCRIPT_DIR="/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/scripts"

echo "$(date): Termux:Boot starting Android layer" >> "$LOG_FILE"

# Ensure directories exist
mkdir -p /data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/{queue,results,state}

# Run selector health probe at boot (verifies UI selectors still valid)
echo "$(date): Running selector health probe..." >> "$LOG_FILE"
python3 "$SCRIPT_DIR/selector_probe.py" >> "$LOG_FILE" 2>&1
PROBE_EXIT=$?
if [ $PROBE_EXIT -ne 0 ]; then
    echo "$(date): WARNING - Selector probe found missing selectors, automation may fail" >> "$LOG_FILE"
fi

# Start the queue monitor loop in background
(
    while true; do
        python3 "$SCRIPT_DIR/process_queue.py" >> "$LOG_FILE" 2>&1
        sleep 30  # Check every 30 seconds
    done
) &

echo "$(date): Queue monitor started (PID: $!)" >> "$LOG_FILE"

# Also ensure Tasker is running
am start -n net.dinglisch.android.taskerm/.TaskerActivity >> "$LOG_FILE" 2>&1

echo "$(date): Termux:Boot complete" >> "$LOG_FILE"
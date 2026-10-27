#!/bin/bash
# Mock providers wrapper with explicit env
export MOCK_KEY_1=key1
export MOCK_KEY_2=key2
cd /data/data/com.termux/files/home/CraftedWorkflowsOS/router/test-env
exec ./mock-providers
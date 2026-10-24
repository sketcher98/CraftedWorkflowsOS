#!/bin/bash
# Test router wrapper — exports BOTH key sets so any config variant works.
export TEST_KEY_1=key1
export TEST_KEY_2=key2
export MOCK_KEY_1=key1
export MOCK_KEY_2=key2
cd /data/data/com.termux/files/home/CraftedWorkflowsOS/router/test-env
exec ./test-router
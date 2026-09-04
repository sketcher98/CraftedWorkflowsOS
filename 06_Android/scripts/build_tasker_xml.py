#!/usr/bin/env python3
"""
Android AutoInput Action Builder — generates Tasker XML with 3-layer fallback.
Primary: text-based UI Query. Fallback: resource ID. Ultimate: coordinates.
"""

import json

CONFIG_PATH = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/config.json"
OUTPUT_XML = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/tasker/Android_Queue_Processor.tsk.xml"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def build_action_chain(platform: str, action: str, config: Dict) -> str:
    """
    Returns Tasker XML snippet that tries:
    1. AutoInput UI Query by text (primary)
    2. AutoInput Click by resource ID (fallback)
    3. AutoInput Tap by coordinates (ultimate fallback)
    """
    # These would come from config.json after user captures them
    text_map = {
        "x": {
            "compose_button": "Post",
            "tweet_input": "What's happening",
            "media_button": "Media",
            "post_button": "Post",
            "search_bar": "Search",
            "dm_button": "Message",
            "message_input": "Message",
            "send_button": "Send",
            "follow_button": "Follow",
            "following_button": "Following",
        },
        "linkedin": {
            "search_bar": "Search",
            "message_button": "Message",
            "message_input": "Write a message",
            "send_button": "Send",
        }
    }
    
    coords_map = {
        # User captures these once via AutoInput "Get Coordinates" or manual
        "x": {},
        "linkedin": {}
    }
    
    # Get resource IDs from config
    resource_ids = config["selectors"].get(platform, {})
    
    text_label = text_map.get(platform, {}).get(action, action)
    resource_id = resource_ids.get(action, "")
    
    # Build the chain: try text → then ID → then coordinates
    chain_xml = []
    
    # Layer 1: Text-based UI Query (primary)
    if text_label:
        chain_xml.append(f'''    <Action sr="act{len(chain_xml)}" ve="7">
      <code>547</code>
      <Str sr="arg0" ve="3">com.joaomgcd.autoinput</Str>
      <Str sr="arg1" ve="3">UI Query</Str>
      <Int sr="arg2" val="0"/>
      <Str sr="arg3" ve="3">%ui_result</Str>
      <Int sr="arg4" val="0"/>
      <Str sr="arg5" ve="3">text:{text_label}</Str>
    </Action>''')
        chain_xml.append(f'''    <Action sr="act{len(chain_xml)}" ve="7">
      <code>38</code>
      <Str sr="arg0" ve="3">%ui_result() > 0</Str>
      <Int sr="arg1" val="0"/>
      <Int sr="arg2" val="0"/>
      <Int sr="arg3" val="1"/>
      <Str sr="arg4" ve="3">true</Str>
    </Action>''')
        chain_xml.append(f'''    <Action sr="act{len(chain_xml)}" ve="7">
      <code>547</code>
      <Str sr="arg0" ve="3">com.joaomgcd.autoinput</Str>
      <Str sr="arg1" ve="3">Click</Str>
      <Int sr="arg2" val="0"/>
      <Str sr="arg3" ve="3">%ui_result(1)</Str>
      <Int sr="arg4" val="1"/>
      <Str sr="arg5" ve="3">%click_result</Str>
    </Action>''')
    
    # Layer 2: Resource ID fallback
    if resource_id:
        chain_xml.append(f'''    <Action sr="act{len(chain_xml)}" ve="7">
      <code>547</code>
      <Str sr="arg0" ve="3">com.joaomgcd.autoinput</Str>
      <Str sr="arg1" ve="3">Click</Str>
      <Int sr="arg2" val="0"/>
      <Str sr="arg3" ve="3">{resource_id}</Str>
      <Int sr="arg4" val="1"/>
      <Str sr="arg5" ve="3">%click_result</Str>
    </Action>''')
    
    # Layer 3: Coordinate fallback
    coords = coords_map.get(platform, {}).get(action, {})
    if coords.get("x") and coords.get("y"):
        chain_xml.append(f'''    <Action sr="act{len(chain_xml)}" ve="7">
      <code>547</code>
      <Str sr="arg0" ve="3">com.joaomgcd.autoinput</Str>
      <Str sr="arg1" ve="3">Tap</Str>
      <Int sr="arg2" val="0"/>
      <Str sr="arg3" ve="3">{coords["x"]}</Str>
      <Int sr="arg4" val="0"/>
      <Str sr="arg5" ve="3">{coords["y"]}</Str>
      <Int sr="arg6" val="1"/>
      <Str sr="arg7" ve="3">%tap_result</Str>
    </Action>''')
    
    return "\n".join(chain_xml)

def main():
    config = load_config()
    
    # This is a template generator - the actual XML is hand-written below
    # because Tasker XML is complex. The above logic shows the 3-layer pattern.
    print("Use the hand-written XML below. This script is a reference for the 3-layer pattern.")
    print(f"Config loaded: {list(config['selectors'].keys())}")

if __name__ == "__main__":
    main()
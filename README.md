# Blender MCP

A lightweight, single-source MCP integration for Blender 3.1. The same `mcp_server.py` runs as a stdio MCP server under regular Python and as the Blender add-on that exposes the live `bpy` runtime over localhost.

## Requirements

- Blender 3.1.0
- Python 3.9-compatible interpreter available to VS Code as `python`
- VS Code with MCP support

## Setup

1. Install `mcp_server.py` as a Blender add-on and enable it in Blender Preferences. The add-on starts its localhost listener on port 9877 by default; set `BLENDER_HOST` or `BLENDER_PORT` to override this.
2. Open this repository in VS Code. The workspace MCP entry is in `.vscode/mcp.json`; trust and start `blender-lite` from the MCP server list.
3. In Copilot Chat, open **Configure Tools** and enable the Blender tools.

The add-on and MCP bridge are separate runtime processes but are implemented in the same source file. Blender API operations are queued onto Blender's main thread. `execute_blender_code` sends code in memory; it does not write per-task script files. Only connect trusted local clients because this tool can execute code inside Blender.

## Tools

- `get_addon_status` — Blender/add-on status and capabilities.
- `get_scene_info` — Inspect current scene objects.
- `bpy_api_lookup` — Look up Blender 3.1 RNA and operator information.
- `execute_blender_code` — Run an inline Blender Python operation.
- `build_house` — Create a lightweight primitive house.
- `simplify_mesh` — Decimate a mesh.
- `animate_camera` and `animate_object` — Add transform keyframes.
- `auto_rig_character` — Create an automatic armature.
- `create_material` — Create/assign a Principled material.
- `setup_compositing` — Create a basic compositor graph with optional Fog Glow.
- `set_render_settings` — Configure render engine, resolution, and samples.

All Blender-side behavior targets Blender 3.1 and keeps Python syntax compatible with Python 3.9.

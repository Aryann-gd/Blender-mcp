# Blender 3.1 MCP Project Instructions

## Single-source architecture

The project implementation lives in `mcp_server.py` only. The same Python 3.9-compatible file has two entry modes because Blender's `bpy` runtime and an MCP stdio client are separate processes:

- Run by VS Code as ordinary Python: it serves MCP over stdio and forwards calls over localhost TCP.
- Loaded as a Blender 3.1 add-on: it starts the local TCP endpoint and runs queued `bpy` work on Blender's main thread using `bpy.app.timers`.

Do not create a new per-action script or add-on file. Add reusable scene operations to `mcp_server.py`; for other Blender tasks, use the generic `execute_blender_code` MCP tool to run code directly in the live Blender process without writing a code file.

## Compatibility and safety

- Target Blender 3.1.0 and Python syntax compatible with Python 3.9.
- Do not use Blender 3.2+ API or Geometry Nodes/modifiers introduced after 3.1.
- `bpy` is unavailable to the standard MCP Python process; only the Blender add-on entry mode imports and runs Blender data operations.
- The add-on listens on `127.0.0.1:9877` by default (`BLENDER_HOST` and `BLENDER_PORT` can override this).
- The listener accepts local commands only. `execute_blender_code` is intentionally powerful; only connect trusted local MCP clients.
- Blender API calls are queued to the main thread. Do not access `bpy` from socket threads.

## MCP capabilities

The server exposes status and scene inspection, Blender RNA/operator lookup, reusable house construction, mesh simplification, camera and object animation, automatic rigging, Principled material creation/assignment, a basic compositor graph, render settings, and generic `execute_blender_code`. Higher-level tools run through the existing generic code endpoint, so they do not require per-feature add-on dispatch changes; restart the MCP server to refresh its advertised tool list after editing this file. Use Blender 3.1 API docs and `bpy_api_lookup` rather than guessing API properties or enum values.

Official API reference: https://docs.blender.org/api/3.1/
Original project reference: https://github.com/ahujasid/mcp-for-blender

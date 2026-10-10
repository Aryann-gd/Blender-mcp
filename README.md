# Blender MCP

A Blender 3.1 MCP bridge implemented in a single Python source file. The project exposes live Blender operations through a local MCP server and supports both a normal Python stdio mode and a Blender add-on mode without splitting the implementation into multiple scripts.

This repository is intentionally kept focused on code and project documentation. Generated image renders are intentionally excluded from version control.

## Project goal

This project provides a lightweight, low-overhead way to:

- inspect the current Blender scene
- look up Blender API and operator information
- execute inline Blender Python code safely through MCP
- create simple environment and prop geometry
- simplify mesh data
- animate objects and cameras
- rig basic character-like meshes
- assign materials and set compositor/render settings

## Repository layout

- `mcp_server.py` — the main Blender MCP server and add-on implementation
- `project-instructions.md` — design constraints and safety guidance
- `README.md` — project overview and usage
- `LICENSE` — MIT license
- `.gitignore` — excludes generated artifacts such as image renders and local editor/cache files

## Requirements

- Blender 3.1.0
- Python 3.9-compatible interpreter available to VS Code
- VS Code with MCP support enabled

## Setup

1. Open the repository in VS Code.
2. Ensure the Blender add-on is loaded in Blender or the server is started through the local MCP configuration in `.vscode/mcp.json`.
3. Start the `blender-lite` MCP server from the VS Code tool list.
4. Use the available tools in Copilot Chat to interact with the live Blender scene.

The add-on and MCP bridge are separated by runtime context, but they share the same source file. Blender API operations are queued to Blender's main thread before execution.

## Safety notes

- Only connect trusted local clients.
- `execute_blender_code` has the power to run Python in Blender directly.
- The listener is local-only and should not be exposed externally.
- This project targets Blender 3.1 APIs and Python 3.9 compatibility.

## Typical usage

- Inspect scene state with the status/scene tools.
- Use `execute_blender_code` for direct inline Blender operations.
- Use `animate_object` and `animate_camera` for scene motion.
- Use `create_material` and `set_render_settings` to finish the look.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

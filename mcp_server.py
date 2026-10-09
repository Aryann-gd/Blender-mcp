"""Single-file MCP bridge and Blender 3.1 add-on.

Run under regular Python 3.9 for MCP stdio. Install this same file as a Blender
add-on to provide the live bpy endpoint. Blender and MCP use separate runtimes,
so the add-on mode and stdio mode are two entry points in this one source file.
"""

bl_info = {
    "name": "Blender MCP (Single File)",
    "author": "Workspace",
    "version": (1, 2, 0),
    "blender": (3, 1, 0),
    "location": "Edit > Preferences > Add-ons",
    "description": "MCP bridge and local Blender control add-on",
    "category": "System",
}

import contextlib
import io
import json
import math
import os
import socket
import sys
import threading
import traceback
from queue import Empty, Queue

try:
    import bpy
except ImportError:
    bpy = None

HOST = os.environ.get("BLENDER_HOST", "127.0.0.1")
# Use a new default while the previous add-on version may still own 9876.
PORT = int(os.environ.get("BLENDER_PORT", "9877"))
PROTOCOL_VERSION = "2024-11-05"
COMMAND_QUEUE = Queue()
_SERVER_THREAD = None
_SERVER_SOCKET = None
_SERVER_STOP = threading.Event()


# MCP tool declarations are in the same file as the Blender implementation.
TOOLS = [
    {
        "name": "get_addon_status",
        "description": "Check the live Blender connection, Blender version, and supported MCP capabilities.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_scene_info",
        "description": "Inspect the current Blender scene and its objects without modifying anything.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Optional case-insensitive object-name filter."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "bpy_api_lookup",
        "description": "Look up Blender 3.1 RNA types, properties, functions, and bpy operators before using them.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "For example bpy.ops.mesh.primitive_cube_add or Object.location."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "execute_blender_code",
        "description": "Run a short Python 3.9 bpy operation in the connected Blender 3.1 instance. Use bpy, math, and print(...) to return observations. No code file is created. This executes powerful code in the local Blender process.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute in Blender; use bpy for scene changes."}},
            "required": ["code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_house",
        "description": "Build a lightweight low-poly house from primitive cube meshes and gable polygons in the live scene.",
        "inputSchema": {
            "type": "object",
            "properties": {"clear_scene": {"type": "boolean", "description": "Delete current scene objects first (default true)."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "simplify_mesh",
        "description": "Reduce the polygon count of a Blender mesh object.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Object name; defaults to the active object."},
                "ratio": {"type": "number", "description": "Decimation ratio between 0 and 1."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "animate_camera",
        "description": "Set up simple location keyframes for a Blender camera.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Camera object name."},
                "start_frame": {"type": "integer"},
                "end_frame": {"type": "integer"},
                "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "animate_object",
        "description": "Keyframe an object's location, and optionally rotation and scale, between two frames.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string"},
                "start_frame": {"type": "integer"},
                "end_frame": {"type": "integer"},
                "start_location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "end_location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "start_rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Euler angles in radians; provide with end_rotation."},
                "end_rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Euler angles in radians; requires start_rotation."},
                "start_scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Provide with end_scale."},
                "end_scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Requires start_scale."},
                "interpolation": {"type": "string", "enum": ["BEZIER", "LINEAR", "CONSTANT"]},
            },
            "required": ["object_name", "start_frame", "end_frame", "start_location", "end_location"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_material",
        "description": "Create or update a Principled material and optionally assign it to named objects or the current selection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "base_color": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4, "description": "RGBA values in the 0-1 range."},
                "metallic": {"type": "number", "minimum": 0, "maximum": 1},
                "roughness": {"type": "number", "minimum": 0, "maximum": 1},
                "object_names": {"type": "array", "items": {"type": "string"}, "description": "Objects to assign; omit to assign to selected objects."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "setup_compositing",
        "description": "Create a simple Render Layers to Composite graph, optionally inserting Blender 3.1 Fog Glow. Replaces the current compositor node graph by default.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "use_glare": {"type": "boolean", "description": "Insert a Fog Glow node (default true)."},
                "threshold": {"type": "number", "minimum": 0, "description": "Fog Glow brightness threshold (default 1.0)."},
                "mix": {"type": "number", "minimum": -1, "maximum": 1, "description": "Glare mix; 0 blends original and effect equally (default 0)."},
                "clear_existing": {"type": "boolean", "description": "Replace the current compositor graph (default true)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "set_render_settings",
        "description": "Set Blender render engine, output resolution, and engine sample count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "engine": {"type": "string", "enum": ["BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"]},
                "resolution_x": {"type": "integer", "minimum": 1},
                "resolution_y": {"type": "integer", "minimum": 1},
                "resolution_percentage": {"type": "integer", "minimum": 1, "maximum": 100},
                "samples": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "auto_rig_character",
        "description": "Add an automatic armature to a mesh object using Blender's automatic weights.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Mesh object name."}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


# -------------------------- Blender-side implementation ---------------------

def _safe_json(value):
    """Convert common Blender/Python values to JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "to_list"):
        try:
            return value.to_list()
        except Exception:
            pass
    return repr(value)


def _make_material(name, color):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name=name)
    material.diffuse_color = color
    return material


def _make_box(name, location, dimensions, material, rotation=(0.0, 0.0, 0.0)):
    # Direct mesh construction avoids context-sensitive operators and modifiers.
    vertices = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
    ]
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.scale = dimensions
    obj.rotation_euler = rotation
    return obj


def _make_gables(material):
    vertices = [
        (-2.0, -1.5, 3.0), (2.0, -1.5, 3.0), (0.0, -1.5, 4.5),
        (-2.0, 1.5, 3.0), (2.0, 1.5, 3.0), (0.0, 1.5, 4.5),
    ]
    faces = [(0, 1, 2), (5, 4, 3), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)]
    mesh = bpy.data.meshes.new("House Gables Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    obj = bpy.data.objects.new("House Gables", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _build_house(clear_scene=True):
    if clear_scene:
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    walls = _make_material("House Warm Ivory", (0.78, 0.62, 0.40, 1.0))
    roof = _make_material("House Terracotta", (0.38, 0.075, 0.045, 1.0))
    trim = _make_material("House Cream Trim", (0.92, 0.84, 0.68, 1.0))
    glass = _make_material("House Blue Glass", (0.12, 0.42, 0.62, 1.0))
    door = _make_material("House Dark Wood", (0.22, 0.075, 0.035, 1.0))
    brick = _make_material("House Chimney Brick", (0.48, 0.16, 0.10, 1.0))

    _make_box("House Walls", (0.0, 0.0, 1.5), (4.0, 3.0, 3.0), walls)
    _make_gables(walls)
    roof_angle = math.radians(36.87)
    _make_box("Left Roof Slope", (-1.14, 0.0, 3.75), (2.85, 3.5, 0.18), roof,
              (0.0, -roof_angle, 0.0))
    _make_box("Right Roof Slope", (1.14, 0.0, 3.75), (2.85, 3.5, 0.18), roof,
              (0.0, roof_angle, 0.0))
    _make_box("Front Door", (0.0, -1.57, 0.92), (0.9, 0.12, 1.84), door)
    _make_box("Porch Step", (0.0, -1.84, 0.09), (1.45, 0.62, 0.18), trim)
    for window_x in (-1.15, 1.15):
        window_y = -1.585
        window_z = 1.95
        _make_box("Window Glass", (window_x, window_y, window_z), (0.66, 0.06, 0.66), glass)
        _make_box("Window Top Trim", (window_x, window_y - 0.035, window_z + 0.37), (0.84, 0.11, 0.08), trim)
        _make_box("Window Bottom Trim", (window_x, window_y - 0.035, window_z - 0.37), (0.84, 0.11, 0.08), trim)
        _make_box("Window Left Trim", (window_x - 0.38, window_y - 0.035, window_z), (0.08, 0.11, 0.82), trim)
        _make_box("Window Right Trim", (window_x + 0.38, window_y - 0.035, window_z), (0.08, 0.11, 0.82), trim)
        _make_box("Window Center Bar", (window_x, window_y - 0.045, window_z), (0.045, 0.12, 0.66), trim)
    _make_box("Chimney", (1.05, 0.45, 4.25), (0.48, 0.5, 1.25), brick)
    return {"status": "success", "message": "Built a low-poly house from primitive meshes.",
            "object_count": len(bpy.context.scene.objects)}


def _get_scene_info(query=""):
    scene = bpy.context.scene
    needle = (query or "").lower()
    objects = []
    for obj in scene.objects:
        if needle and needle not in obj.name.lower():
            continue
        objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": [round(float(v), 3) for v in obj.location],
            "dimensions": [round(float(v), 3) for v in obj.dimensions],
        })
    return {"scene": scene.name, "blender_version": bpy.app.version_string,
            "frame": scene.frame_current, "object_count": len(scene.objects), "objects": objects[:100]}


def _bpy_api_lookup(query):
    query = (query or "").strip()
    if not query:
        return {"error": "Provide a bpy API query."}
    clean = query[4:] if query.startswith("bpy.") else query
    if clean.startswith("ops."):
        pieces = clean[4:].split(".")
        if len(pieces) != 2:
            return {"error": "Use bpy.ops.<category>.<operator>."}
        group = getattr(bpy.ops, pieces[0], None)
        operator = getattr(group, pieces[1], None) if group else None
        if operator is None:
            return {"error": "Unknown operator: bpy.ops." + ".".join(pieces)}
        rna = operator.get_rna_type()
        properties = []
        for prop in rna.properties:
            if prop.identifier != "rna_type":
                properties.append({"name": prop.identifier, "type": prop.type,
                                   "description": prop.description})
        return {"kind": "operator", "idname": "bpy.ops." + ".".join(pieces),
                "description": rna.description, "parameters": properties}
    parts = clean.split(".")
    cls = getattr(bpy.types, parts[0], None)
    if cls is None:
        suggestions = [name for name in dir(bpy.types) if parts[0].lower() in name.lower()][:12]
        return {"error": "Unknown bpy.types entry: " + parts[0], "suggestions": suggestions}
    if len(parts) > 1:
        prop = cls.bl_rna.properties.get(parts[1])
        if prop is not None:
            return {"kind": "property", "owner_type": parts[0], "name": prop.identifier,
                    "type": prop.type, "description": prop.description}
        func = cls.bl_rna.functions.get(parts[1])
        if func is not None:
            return {"kind": "function", "owner_type": parts[0], "name": func.identifier,
                    "description": func.description,
                    "parameters": [p.identifier for p in func.parameters if not p.is_output]}
        return {"error": "No property or function named " + parts[1]}
    return {"kind": "type", "name": parts[0], "description": cls.bl_rna.description,
            "properties": [p.identifier for p in cls.bl_rna.properties if p.identifier != "rna_type"][:100],
            "functions": [f.identifier for f in cls.bl_rna.functions][:100]}


def _execute_blender_code(code):
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "code must be a non-empty string."}
    output = io.StringIO()
    namespace = {"bpy": bpy, "math": math}
    try:
        with contextlib.redirect_stdout(output):
            exec(compile(code, "<MCP Blender request>", "exec"), namespace, namespace)
        return {"status": "success", "stdout": output.getvalue(),
                "result": _safe_json(namespace.get("result"))}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "traceback": traceback.format_exc(),
                "stdout": output.getvalue()}


def _execute_blender_command(request):
    tool = request.get("tool")
    args = request.get("args") or {}
    if tool == "get_addon_status":
        return {"status": "success", "name": bl_info["name"], "addon_version": list(bl_info["version"]),
                "blender_version": bpy.app.version_string, "host": HOST, "port": PORT,
                "capabilities": [item["name"] for item in TOOLS]}
    if tool == "get_scene_info":
        return {"status": "success", "scene_info": _get_scene_info(args.get("query", ""))}
    if tool in ("execute_blender_code", "execute_code"):
        return _execute_blender_code(args.get("code"))
    if tool == "build_house":
        return _build_house(args.get("clear_scene", True))
    if tool == "simplify_mesh":
        active = bpy.context.active_object
        name = args.get("name") or (active.name if active else None)
        ratio = float(args.get("ratio", 0.5))
        if not name or bpy.data.objects.get(name) is None:
            return {"status": "error", "message": "Target mesh object not found."}
        if not 0.0 < ratio <= 1.0:
            return {"status": "error", "message": "ratio must be greater than 0 and at most 1."}
        obj = bpy.data.objects[name]
        modifier = obj.modifiers.new(name="MCP Decimate", type="DECIMATE")
        modifier.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        except Exception:
            obj.modifiers.remove(modifier)
            raise
        return {"status": "success", "message": "Simplified {} to ratio {}.".format(name, ratio)}
    if tool == "animate_camera":
        name = args.get("name", "Camera")
        cam = bpy.data.objects.get(name)
        if cam is None or cam.type != "CAMERA":
            return {"status": "error", "message": "Camera not found: " + name}
        start = int(args.get("start_frame", 1))
        end = int(args.get("end_frame", 100))
        loc = args.get("location", [0.0, -10.0, 5.0])
        if len(loc) != 3 or end <= start:
            return {"status": "error", "message": "location needs 3 values and end_frame must exceed start_frame."}
        cam.location = loc
        cam.keyframe_insert(data_path="location", frame=start)
        cam.location = [loc[0], loc[1] + 5.0, loc[2]]
        cam.keyframe_insert(data_path="location", frame=end)
        return {"status": "success", "message": "Animated {} from frame {} to {}.".format(name, start, end)}
    if tool == "auto_rig_character":
        name = args.get("name")
        obj = bpy.data.objects.get(name) if name else None
        if obj is None or obj.type != "MESH":
            return {"status": "error", "message": "Mesh object not found: {}".format(name)}
        for item in bpy.context.selected_objects:
            item.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.armature_add(location=obj.location)
        armature = bpy.context.active_object
        armature.name = "AutoRig"
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        armature.select_set(True)
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        return {"status": "success", "message": "Auto-rigged {}.".format(name)}
    if tool == "bpy_api_lookup":
        return _bpy_api_lookup(args.get("query", ""))
    return {"status": "error", "message": "Unknown tool: {}".format(tool)}


def _handle_blender_client(connection):
    with connection:
        connection.settimeout(30.0)
        chunks = []
        size = 0
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                connection.sendall(json.dumps({"status": "error", "message": "Request exceeds 4 MiB limit."}).encode("utf-8"))
                return
            chunks.append(chunk)
        try:
            request = json.loads(b"".join(chunks).decode("utf-8"))
            completed = threading.Event()
            result = {}
            COMMAND_QUEUE.put((request, completed, result))
            if completed.wait(120.0):
                response = result.get("response", {"status": "error", "message": "No response generated."})
            else:
                response = {"status": "error", "message": "Blender did not process the command in time."}
        except Exception as exc:
            response = {"status": "error", "message": str(exc)}
        try:
            connection.sendall(json.dumps(response, default=repr).encode("utf-8"))
        except OSError:
            pass


def _process_command_queue():
    while True:
        try:
            request, completed, result = COMMAND_QUEUE.get_nowait()
        except Empty:
            break
        try:
            result["response"] = _execute_blender_command(request)
        except Exception as exc:
            result["response"] = {"status": "error", "message": str(exc), "traceback": traceback.format_exc()}
        completed.set()
    return 0.05


def _server_loop():
    global _SERVER_SOCKET
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _SERVER_SOCKET = server_socket
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.settimeout(0.5)
    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(8)
        print("Blender MCP listening on {}:{}".format(HOST, PORT))
        while not _SERVER_STOP.is_set():
            try:
                connection = server_socket.accept()[0]
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_handle_blender_client, args=(connection,), daemon=True).start()
    except OSError as exc:
        print("Blender MCP socket error: {}".format(exc))
    finally:
        try:
            server_socket.close()
        except OSError:
            pass
        _SERVER_SOCKET = None


def register():
    global _SERVER_THREAD
    if bpy is None:
        return
    _SERVER_STOP.clear()
    if not bpy.app.timers.is_registered(_process_command_queue):
        bpy.app.timers.register(_process_command_queue, first_interval=0.05, persistent=True)
    if _SERVER_THREAD is None or not _SERVER_THREAD.is_alive():
        _SERVER_THREAD = threading.Thread(target=_server_loop, name="BlenderMCPServer", daemon=True)
        _SERVER_THREAD.start()
    print("Blender MCP add-on registered; local server starting.")


def unregister():
    global _SERVER_THREAD
    if bpy is None:
        return
    _SERVER_STOP.set()
    if _SERVER_SOCKET is not None:
        try:
            _SERVER_SOCKET.close()
        except OSError:
            pass
    if bpy.app.timers.is_registered(_process_command_queue):
        bpy.app.timers.unregister(_process_command_queue)
    _SERVER_THREAD = None


# -------------------------- MCP stdio implementation ------------------------

def _python_literal(value):
    """Format a JSON-derived value as a safe Python literal for Blender code."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Numeric arguments must be finite.")
        return repr(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[{}]".format(", ".join(_python_literal(item) for item in value))
    if isinstance(value, dict):
        return "{{{}}}".format(", ".join(
            "{}: {}".format(_python_literal(key), _python_literal(item))
            for key, item in value.items()
        ))
    raise ValueError("Unsupported argument value type: {}".format(type(value).__name__))


def _validated_vector(arguments, key, required=False, size=3):
    value = arguments.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError("{} must contain exactly {} numbers.".format(key, size))
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError("{} must contain only finite numbers.".format(key))
        result.append(float(item))
    return result


def _tool_code(tool_name, arguments):
    """Build short inline bpy operations for higher-level MCP tools.

    The source is sent over the existing execute_code command; no script file
    is written and the installed Blender add-on need not know each tool name.
    """
    if tool_name == "animate_object":
        name = arguments.get("object_name")
        if not isinstance(name, str) or not name:
            raise ValueError("object_name is required.")
        start_frame = arguments.get("start_frame")
        end_frame = arguments.get("end_frame")
        if (isinstance(start_frame, bool) or not isinstance(start_frame, int) or
                isinstance(end_frame, bool) or not isinstance(end_frame, int) or end_frame <= start_frame):
            raise ValueError("start_frame and end_frame must be integers and end_frame must be later.")
        interpolation = arguments.get("interpolation", "BEZIER")
        if interpolation not in ("BEZIER", "LINEAR", "CONSTANT"):
            raise ValueError("interpolation must be BEZIER, LINEAR, or CONSTANT.")
        pairs = []
        for channel, start_key, end_key in (
                ("location", "start_location", "end_location"),
                ("rotation_euler", "start_rotation", "end_rotation"),
                ("scale", "start_scale", "end_scale")):
            start_value = _validated_vector(arguments, start_key, required=channel == "location")
            end_value = _validated_vector(arguments, end_key, required=channel == "location")
            if (start_value is None) != (end_value is None):
                raise ValueError("Provide both {} and {} to animate that channel.".format(start_key, end_key))
            if start_value is not None:
                pairs.append((channel, start_value, end_value))
        return "\n".join((
            "obj = bpy.data.objects.get({})".format(_python_literal(name)),
            "if obj is None: raise ValueError('Object not found: ' + {})".format(_python_literal(name)),
            "channels = {}".format(_python_literal(pairs)),
            "for path, start_value, end_value in channels:",
            "    setattr(obj, path, start_value)",
            "    obj.keyframe_insert(data_path=path, frame={})".format(start_frame),
            "    setattr(obj, path, end_value)",
            "    obj.keyframe_insert(data_path=path, frame={})".format(end_frame),
            "if obj.animation_data and obj.animation_data.action:",
            "    for curve in obj.animation_data.action.fcurves:",
            "        for key in curve.keyframe_points:",
            "            key.interpolation = {}".format(_python_literal(interpolation)),
            "result = {'object': obj.name, 'start_frame': %d, 'end_frame': %d, 'channels': [c[0] for c in channels]}" % (start_frame, end_frame),
        ))

    if tool_name == "create_material":
        name = arguments.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A non-empty material name is required.")
        color = _validated_vector(arguments, "base_color", size=4) or [0.8, 0.8, 0.8, 1.0]
        if any(component < 0.0 or component > 1.0 for component in color):
            raise ValueError("base_color components must be between 0 and 1.")
        metallic = float(arguments.get("metallic", 0.0))
        roughness = float(arguments.get("roughness", 0.5))
        if not math.isfinite(metallic) or not 0.0 <= metallic <= 1.0:
            raise ValueError("metallic must be between 0 and 1.")
        if not math.isfinite(roughness) or not 0.0 <= roughness <= 1.0:
            raise ValueError("roughness must be between 0 and 1.")
        object_names = arguments.get("object_names")
        if object_names is not None and (not isinstance(object_names, list) or
                                         any(not isinstance(item, str) for item in object_names)):
            raise ValueError("object_names must be an array of object-name strings.")
        return "\n".join((
            "material = bpy.data.materials.get({})".format(_python_literal(name)),
            "if material is None: material = bpy.data.materials.new(name={})".format(_python_literal(name)),
            "material.use_nodes = True",
            "shader = next((node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)",
            "if shader is None: shader = material.node_tree.nodes.new('ShaderNodeBsdfPrincipled')",
            "shader.inputs['Base Color'].default_value = {}".format(_python_literal(color)),
            "shader.inputs['Metallic'].default_value = {}".format(_python_literal(metallic)),
            "shader.inputs['Roughness'].default_value = {}".format(_python_literal(roughness)),
            "requested_names = {}".format(_python_literal(object_names)),
            "targets = list(bpy.context.selected_objects) if requested_names is None else [bpy.data.objects.get(item) for item in requested_names]",
            "missing = [item for item in targets if item is None]",
            "if missing: raise ValueError('One or more requested objects were not found.')",
            "assigned = []",
            "for obj in targets:",
            "    slots = getattr(getattr(obj, 'data', None), 'materials', None)",
            "    if slots is not None:",
            "        if len(slots): slots[0] = material",
            "        else: slots.append(material)",
            "        assigned.append(obj.name)",
            "result = {'material': material.name, 'assigned_objects': assigned}",
        ))

    if tool_name == "setup_compositing":
        use_glare = arguments.get("use_glare", True)
        clear_existing = arguments.get("clear_existing", True)
        if not isinstance(use_glare, bool) or not isinstance(clear_existing, bool):
            raise ValueError("use_glare and clear_existing must be booleans.")
        threshold = float(arguments.get("threshold", 1.0))
        mix = float(arguments.get("mix", 0.0))
        if not math.isfinite(threshold) or threshold < 0.0:
            raise ValueError("threshold must be a finite non-negative number.")
        if not math.isfinite(mix) or not -1.0 <= mix <= 1.0:
            raise ValueError("mix must be between -1 and 1.")
        return "\n".join((
            "scene = bpy.context.scene",
            "scene.use_nodes = True",
            "tree = scene.node_tree",
            "if {}: tree.nodes.clear()".format(_python_literal(clear_existing)),
            "layers = tree.nodes.new('CompositorNodeRLayers')",
            "output = tree.nodes.new('CompositorNodeComposite')",
            "layers.location = (0, 0)",
            "output.location = (420, 0)",
            "if {}:".format(_python_literal(use_glare)),
            "    glare = tree.nodes.new('CompositorNodeGlare')",
            "    glare.glare_type = 'FOG_GLOW'",
            "    glare.quality = 'MEDIUM'",
            "    glare.threshold = {}".format(_python_literal(threshold)),
            "    glare.mix = {}".format(_python_literal(mix)),
            "    glare.location = (210, 0)",
            "    tree.links.new(layers.outputs['Image'], glare.inputs['Image'])",
            "    tree.links.new(glare.outputs['Image'], output.inputs['Image'])",
            "else:",
            "    tree.links.new(layers.outputs['Image'], output.inputs['Image'])",
            "scene.render.use_compositing = True",
            "result = {'compositing': True, 'glare': " + _python_literal(use_glare) + "}",
        ))

    if tool_name == "set_render_settings":
        engine = arguments.get("engine")
        allowed_engines = ("BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH")
        if engine is not None and engine not in allowed_engines:
            raise ValueError("engine must be BLENDER_EEVEE, CYCLES, or BLENDER_WORKBENCH.")
        settings = []
        for key, attribute, minimum, maximum in (
                ("resolution_x", "resolution_x", 1, None),
                ("resolution_y", "resolution_y", 1, None),
                ("resolution_percentage", "resolution_percentage", 1, 100)):
            value = arguments.get(key)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
                    raise ValueError("{} is outside its supported range.".format(key))
                settings.append((attribute, value))
        samples = arguments.get("samples")
        if samples is not None and (isinstance(samples, bool) or not isinstance(samples, int) or samples < 1):
            raise ValueError("samples must be a positive integer.")
        lines = ["scene = bpy.context.scene"]
        if engine is not None:
            lines.append("scene.render.engine = {}".format(_python_literal(engine)))
        for attribute, value in settings:
            lines.append("scene.render.{} = {}".format(attribute, value))
        if samples is not None:
            lines.extend((
                "if scene.render.engine == 'BLENDER_EEVEE': scene.eevee.taa_render_samples = {}".format(samples),
                "elif scene.render.engine == 'CYCLES': scene.cycles.samples = {}".format(samples),
            ))
        lines.append("result = {'engine': scene.render.engine, 'resolution': [scene.render.resolution_x, scene.render.resolution_y], 'percentage': scene.render.resolution_percentage}")
        return "\n".join(lines)

    return None


def _send_to_blender(tool_name, arguments):
    generated_code = _tool_code(tool_name, arguments)
    if tool_name == "execute_blender_code":
        command = "execute_code"
        args = {"code": arguments.get("code", "")}
    elif generated_code is not None:
        command = "execute_code"
        args = {"code": generated_code}
    else:
        command = tool_name
        args = arguments
    payload = json.dumps({"tool": command, "args": args}).encode("utf-8")
    with socket.create_connection((HOST, PORT), timeout=125.0) as connection:
        connection.settimeout(125.0)
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    if not chunks:
        raise RuntimeError("Blender closed the connection without a response.")
    return json.loads(b"".join(chunks).decode("utf-8"))


def _rpc_response(message_id, result):
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _rpc_error(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _handle_mcp_message(message):
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if method == "notifications/initialized" or (method and method.startswith("notifications/")):
        return None
    if method == "initialize":
        return _rpc_response(message_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "blender-mcp-single-file", "version": "1.2.0"},
            "instructions": "Controls Blender 3.1 through a local add-on. Use get_addon_status and get_scene_info before scene edits. execute_blender_code runs in the live Blender process; no code file is written.",
        })
    if method == "ping":
        return _rpc_response(message_id, {})
    if method == "tools/list":
        return _rpc_response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(message_id, -32602, "Tool arguments must be a JSON object.")
        if not any(tool["name"] == tool_name for tool in TOOLS):
            return _rpc_response(message_id, {"content": [{"type": "text", "text": "Unknown tool: {}".format(tool_name)}], "isError": True})
        result = None
        try:
            result = _send_to_blender(tool_name, arguments)
            output = result.get("message") or result.get("stdout") or json.dumps(result, default=repr)
            if "traceback" in result:
                output += "\n" + result["traceback"]
            failed = result.get("status") == "error"
        except Exception as exc:
            output = "Blender tool call failed: {}".format(exc)
            failed = True
        reply = {"content": [{"type": "text", "text": output}], "isError": failed}
        if result is not None:
            reply["structuredContent"] = result
        return _rpc_response(message_id, reply)
    return _rpc_error(message_id, -32601, "Method not found: {}".format(method))


def _stdio_main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            result = _handle_mcp_message(message)
        except Exception as exc:
            result = _rpc_error(None, -32700, "Invalid JSON-RPC message: {}".format(exc))
        if result is not None:
            sys.stdout.write(json.dumps(result, separators=(",", ":"), default=repr) + "\n")
            sys.stdout.flush()


if bpy is not None:
    register()
elif __name__ == "__main__":
    _stdio_main()

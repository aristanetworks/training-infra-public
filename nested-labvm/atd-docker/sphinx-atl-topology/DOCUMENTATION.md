# Sphinx ATL Topology Extension - API Documentation

## Overview

The `sphinx_atl_topology` package is a Sphinx extension that adds a `.. topology-diagram::` directive for embedding interactive network topology diagrams in reStructuredText documentation. It integrates with Sphinx's build process to validate YAML topology data, render HTML containers with embedded JSON configuration, and include client-side JavaScript for interactive visualization.

**Key Features**:
- Native RST directive syntax
- YAML/JSON topology schema validation
- Automatic device type classification
- Zone (compound node) support
- Text annotations
- Live device status via WebSocket
- SSH terminal and console access
- Multiple layout algorithms
- Responsive and accessible

---

## Package Structure

```
sphinx_atl_topology/
├── __init__.py              # Extension setup
├── directive.py             # TopologyDiagramDirective
├── nodes.py                 # TopologyDiagramNode
├── translator.py            # HTML translation
├── schema.py                # Topology validation
├── device_types.py          # Device classification
├── _static/                 # Static assets
│   ├── atl-topology-viewer.css
│   ├── cytoscape.min.js
│   ├── cytoscape-dagre.js
│   └── atl-topology-viewer.js
└── tests/                   # Unit tests
```

---

## Module Reference

### 1. __init__.py

#### setup(app)

```python
def setup(app: Sphinx) -> dict
```

Extension entry point. Registers the directive, node, and static assets.

**Parameters**:
- `app` - Sphinx application instance

**Returns**: Extension metadata dict with version and parallel safety flags

**Registration Actions**:
1. Adds `TopologyDiagramNode` with HTML translator functions
2. Registers `topology-diagram` directive
3. Appends `_static/` to `html_static_path`
4. Adds CSS file: `atl-topology-viewer.css`
5. Adds JS files: `cytoscape.min.js`, `cytoscape-dagre.js`, `atl-topology-viewer.js`

**Usage in conf.py**:
```python
extensions = [
    'sphinx_atl_topology',
    # ... other extensions
]
```

---

### 2. directive.py

**Class**: `TopologyDiagramDirective`

Extends `SphinxDirective` to parse the `.. topology-diagram::` directive.

#### Class Properties

```python
has_content = True
optional_arguments = 0
option_spec = {
    'height': directives.positive_int,
    'layout': lambda x: directives.choice(x, ('preset', 'dagre', 'cose', 'concentric', 'grid')),
    'file': directives.path,
    'no-live-status': directives.flag,
    'no-device-access': directives.flag,
    'title': directives.unchanged,
}
```

#### Methods

##### run()

```python
def run(self) -> List[TopologyDiagramNode]
```

Parses directive content or file, validates topology, and creates document node.

**Algorithm**:
1. Validate options (cannot have both `:file:` and inline content)
2. Load YAML from file or inline content
3. Parse YAML with error handling
4. Validate schema with `validate_topology()`
5. Apply directive options as overrides to YAML settings
6. Convert to Cytoscape elements with `_convert_to_cytoscape()`
7. Create `TopologyDiagramNode` with JSON configuration
8. Return node list

**Raises**:
- `self.error()` - For invalid options, missing files, YAML errors, or validation failures

**Directive Options**:
- `:height: 600` - Override diagram height in pixels
- `:layout: dagre` - Override layout algorithm
- `:file: path/to/topology.yml` - Load from external file
- `:no-live-status:` - Disable WebSocket status updates
- `:no-device-access:` - Disable SSH/console context menu
- `:title: Network Overview` - Override diagram title

##### _convert_to_cytoscape(topo_data)

```python
def _convert_to_cytoscape(self, topo_data: dict) -> List[dict]
```

Converts topology YAML schema to Cytoscape.js elements array.

**Parameters**:
- `topo_data` - Validated topology dictionary

**Returns**: Cytoscape elements array (nodes and edges)

**Conversion Logic**:
1. Create zone parent nodes with compound node data
2. Create device nodes with:
   - Device type classification (via `classify_device()`)
   - Icon mapping (via `DEVICE_TYPE_ICONS`)
   - Zone parent assignment
   - Position (if specified)
3. Create edge elements with port labels

**Example Usage in RST**:

```rst
.. topology-diagram::
   :height: 600
   :layout: dagre
   :title: Lab Topology

   nodes:
     - id: spine1
       label: "Spine 1"
       type: spine
       ip: 192.168.0.11
     - id: leaf1
       label: "Leaf 1"
       type: leaf
       ip: 192.168.0.12

   edges:
     - source: spine1
       target: leaf1
       source_port: Ethernet1
       target_port: Ethernet49
```

---

### 3. nodes.py

**Class**: `TopologyDiagramNode`

Custom docutils node for topology diagrams.

```python
class TopologyDiagramNode(nodes.General, nodes.Element):
    """Custom docutils node for topology diagrams."""
    pass
```

**Attributes** (set by directive):
- `['topology_data']` - JSON string with viewer configuration
- `['height']` - Diagram height in pixels
- `['title']` - Diagram title (optional)

**Purpose**: Serves as a placeholder in the document tree. Translators convert it to HTML during build.

---

### 4. translator.py

#### visit_topology_node(self, node)

```python
def visit_topology_node(self: HTMLTranslator, node: TopologyDiagramNode) -> None
```

HTML translator function that generates the diagram container HTML.

**Parameters**:
- `self` - Sphinx HTML translator instance
- `node` - TopologyDiagramNode instance

**Generated HTML**:
```html
<div class="atl-topology-title">Network Overview</div>
<div class="atl-topology-container"
     style="height: 600px;"
     data-topology='{"title":"...","elements":[...],"layout":"dagre",...}'>
</div>
```

The `data-topology` attribute contains the complete JSON configuration for the client-side viewer.

#### depart_topology_node(self, node)

```python
def depart_topology_node(self: HTMLTranslator, node: TopologyDiagramNode) -> None
```

No-op function (closing tag is self-contained in `visit_topology_node`).

---

### 5. schema.py

#### validate_topology(data)

```python
def validate_topology(data: dict) -> List[str]
```

Validates topology diagram YAML structure.

**Parameters**:
- `data` - Parsed YAML dictionary

**Returns**: List of error strings (empty if valid)

**Validation Rules**:

**Top-Level**:
- Must be a dict

**nodes** (list):
- Each node must be a dict
- Required field: `id`
- No duplicate IDs
- If `position` present, must have `x` and `y` numeric fields

**edges** (list):
- Each edge must be a dict
- Required fields: `source`, `target`

**zones** (list):
- Each zone must be a dict
- Required field: `id`

**annotations** (list):
- Each annotation must be a dict
- Required field: `text`
- If `position` present, must have `x` and `y`

**settings** (dict):
- `layout` must be one of: preset, dagre, cose, concentric, grid

**Example Errors**:
```python
[
    'Node 0 missing required "id" field',
    'Duplicate node id: spine1',
    'Edge 2 missing "source"',
    'Invalid layout: custom. Must be one of: preset, dagre, cose, concentric, grid'
]
```

---

### 6. device_types.py

#### DEVICE_TYPE_ICONS

```python
DEVICE_TYPE_ICONS: dict[str, str]
```

Maps device type strings to icon filenames.

**Icon Mappings**:
- `spine.png`: spine, leaf, pe, ce, p
- `leaf.png`: borderleaf, memleaf
- `router.png`: router, core, dci, rr, gw, internet, isp, oob, firewall, customer, velo_*, other
- `hosts.png`: host, linux_host

**Example**:
```python
>>> DEVICE_TYPE_ICONS['spine']
'spine.png'
>>> DEVICE_TYPE_ICONS['host']
'hosts.png'
```

#### classify_device(name)

```python
def classify_device(name: str) -> str
```

Classifies device type from device name using pattern matching.

**Parameters**:
- `name` - Device name (e.g., 'spine1', 'Leaf-DC1', 'RR')

**Returns**: Device type string

**Classification Algorithm** (in priority order):

1. **Custom exact patterns**:
   - `'RR'` → 'rr'
   - `'RR1'`, `'RR2'`, etc. → 'rr'
   - `'P1'`, `'P2'`, etc. → 'p'
   - `'GW1'`, `'GW2'`, etc. → 'gw'
   - `'A1'`, `'B2'`, `'C3'`, `'D4'` (single letter + digit) → 'customer'

2. **Startswith patterns** (case-sensitive):
   - Starts with `'PE'` → 'pe'
   - Starts with `'CE'` → 'ce'
   - Starts with `'BL'` → 'borderleaf'

3. **Contains patterns** (case-insensitive):
   - Contains 'spine' → 'spine'
   - Contains 'leaf' → 'leaf'
   - Contains 'host' → 'host'
   - Contains 'router' → 'router'
   - Contains 'core' → 'core'
   - Contains 'dci' → 'dci'
   - Contains 'oob' → 'oob'
   - Contains 'internet' → 'internet'
   - Contains 'isp' → 'isp'
   - Contains 'memleaf' → 'memleaf'
   - Contains 'borderleaf' → 'borderleaf'

4. **Default**: 'other'

**Example**:
```python
>>> classify_device('spine1-DC1')
'spine'
>>> classify_device('Leaf2')
'leaf'
>>> classify_device('PE1')
'pe'
>>> classify_device('RR')
'rr'
>>> classify_device('P1')
'p'
>>> classify_device('A1')
'customer'
>>> classify_device('unknown-device')
'other'
```

---

## Client-Side Viewer Components

The extension includes JavaScript modules for the interactive viewer. See separate documentation:

### ATLTopologyViewer (atl-topology-viewer.js)

**Class**: `ATLTopologyViewer`

Self-initializing IIFE that finds all `[data-topology]` containers and renders interactive diagrams.

#### Static Methods

##### init()

```javascript
static init(): void
```

Finds all `[data-topology]` containers and initializes viewer instances.

**Auto-initialization**: Called on `DOMContentLoaded` or immediately if DOM is already ready.

#### Constructor

```javascript
constructor(container: HTMLElement, config: Object, viewerIndex: number)
```

**Parameters**:
- `container` - DOM element with `data-topology` attribute
- `config` - Parsed JSON configuration from `data-topology` attribute
- `viewerIndex` - Index among all viewers on page

**Initialization Flow**:
1. Create Cytoscape container div
2. Initialize `ViewerManager` (Cytoscape rendering)
3. Initialize `ViewerZoneRenderer` (zone helpers)
4. Initialize `ViewerAnnotationRenderer` (HTML overlays)
5. Initialize `ViewerEventHandlers` (if `deviceAccess !== false`)
6. Initialize `ViewerStatusUpdater` (if `liveStatus !== false`)
7. Run layout algorithm

---

### ViewerManager (viewer-manager.js)

**Class**: `ViewerManager`

Manages Cytoscape.js instance for read-only viewing.

#### Constructor

```javascript
constructor(container: HTMLElement, config: Object)
```

#### Methods

##### initCytoscape()

```javascript
initCytoscape(): void
```

Initializes Cytoscape with viewer-specific styles (no editing features).

##### getStyles()

```javascript
getStyles(): Array<Object>
```

Returns Cytoscape stylesheet array with:
- Device type icons and sizes
- Zone compound node styles
- Status indicators (up, down, error, unknown)
- Edge styles with port labels
- Interactive states (selected, highlighted, faded, focused, hover)

##### runLayout(layoutName)

```javascript
runLayout(layoutName: string): void
```

Runs layout algorithm. Falls back to dagre if preset layout has no positions.

**Supported Layouts**: dagre, cose, concentric, grid, preset

---

### ViewerEventHandlers (viewer-event-handlers.js)

**Class**: `ViewerEventHandlers`

Handles user interactions: context menu, SSH/console access, focus mode.

#### Methods

##### bindEvents()

```javascript
bindEvents(): void
```

Binds events:
- Right-click → context menu
- Mouse over/out → hover effects
- Keyboard (Escape) → close menu, exit focus

##### showContextMenu(e)

```javascript
showContextMenu(e: CytoscapeEvent): void
```

Shows context menu with options:
- Open Terminal (SSH to device IP)
- Open Console (virsh console)
- Focus Device (highlight neighborhood)

##### openTerminal(ip, label)

```javascript
openTerminal(ip: string, label: string): void
```

Opens `/terminal?ip={ip}&name={label}` in new window.

##### openConsole(label)

```javascript
openConsole(label: string): void
```

Opens `/console?name={label}` in new window.

##### enterFocusMode(node)

```javascript
enterFocusMode(node: CytoscapeNode): void
```

Highlights node and its neighborhood, fades everything else.

##### exitFocusMode()

```javascript
exitFocusMode(): void
```

Removes focus mode styling.

---

### ViewerStatusUpdater (viewer-status-updater.js)

**Class**: `ViewerStatusUpdater`

WebSocket client for live device status updates.

#### Constructor

```javascript
constructor(cy: Cytoscape, container: HTMLElement)
```

**Initialization**:
1. Creates status indicator div
2. Connects to `/td-ws` WebSocket
3. Requests status on connection

#### Methods

##### connect()

```javascript
connect(): void
```

Establishes WebSocket connection with error handling and graceful degradation.

##### attemptReconnect()

```javascript
attemptReconnect(): void
```

Retries connection up to 3 times, then fails silently.

##### requestStatus()

```javascript
requestStatus(): void
```

Sends `{type: 'status_request'}` message to WebSocket.

##### handleStatusUpdate(data)

```javascript
handleStatusUpdate(data: Object): void
```

Applies status classes to nodes based on WebSocket message.

**Status Mapping**:
- `'up'` or `'reachable'` → `.status-up` (green underlay)
- `'down'` or `'unreachable'` → `.status-down` (red underlay)
- `'error'` → `.status-error` (orange underlay)
- Other → `.status-unknown` (gray underlay)

##### disconnect()

```javascript
disconnect(): void
```

Closes WebSocket connection.

---

### ViewerZoneRenderer (viewer-zone-renderer.js)

**Class**: `ViewerZoneRenderer`

Helper methods for zone (compound node) operations.

#### Methods

##### getZones()

```javascript
getZones(): CytoscapeCollection
```

Returns all zone parent nodes.

##### getZoneChildren(zoneId)

```javascript
getZoneChildren(zoneId: string): CytoscapeCollection
```

Returns child nodes of a zone.

##### hasZone(nodeId)

```javascript
hasZone(nodeId: string): boolean
```

Checks if node belongs to any zone.

##### getNodeZone(nodeId)

```javascript
getNodeZone(nodeId: string): string | null
```

Returns zone ID of a node, or null.

---

### ViewerAnnotationRenderer (viewer-annotation-renderer.js)

**Class**: `ViewerAnnotationRenderer`

Renders HTML text overlays that track Cytoscape pan/zoom.

#### Constructor

```javascript
constructor(cy: Cytoscape, container: HTMLElement, annotationsData: Array)
```

**Initialization**:
1. Creates `.annotation-overlay` container
2. Renders all annotations from data
3. Binds pan/zoom events to update positions

#### Methods

##### addAnnotation(annData)

```javascript
addAnnotation(annData: Object): void
```

Creates annotation DOM element with styling.

**Parameters**:
- `annData` - Object: {text, position: {x, y}, color?, font_size?, background?}

##### updatePositions()

```javascript
updatePositions(): void
```

Updates annotation positions based on current pan/zoom. Called automatically on pan/zoom/layoutstop events.

---

## Configuration Schema

### Directive YAML Schema

```yaml
# Optional title (can also use :title: option)
title: "Lab Network Topology"

# Settings (all optional, can be overridden by directive options)
settings:
  layout: dagre              # preset | dagre | cose | concentric | grid
  height: 600                # pixels
  live_status: true          # enable WebSocket status
  device_access: true        # enable SSH/console context menu
  show_port_labels: true     # show port labels on edges

# Device nodes (required)
nodes:
  - id: spine1               # required, unique
    label: "Spine 1"         # optional, defaults to id
    type: spine              # optional, auto-classified from id if missing
    ip: 192.168.0.11         # optional, required for SSH/console
    zone: dc1                # optional, assigns to zone parent
    position:                # optional, required for layout: preset
      x: 100
      y: 50

# Connections (optional)
edges:
  - source: spine1           # required, node id
    target: leaf1            # required, node id
    source_port: Ethernet1   # optional
    target_port: Ethernet49  # optional

# Background zones/groups (optional)
zones:
  - id: dc1                  # required, unique
    label: "Data Center 1"   # optional
    color: "#071c35"         # optional, border color
    background: "rgba(7, 28, 53, 0.05)"  # optional, fill color
    border_style: solid      # optional: solid | dashed

# Text annotations (optional)
annotations:
  - text: "Core Layer"       # required
    position: {x: 200, y: 30}  # required
    color: "#4c5cae"         # optional
    font_size: 14            # optional, default 12
    background: true         # optional, default true
```

### Viewer JSON Configuration

The `data-topology` attribute contains JSON with this structure:

```json
{
  "title": "Lab Network Topology",
  "height": 600,
  "layout": "dagre",
  "liveStatus": true,
  "deviceAccess": true,
  "showPortLabels": true,
  "elements": [
    {
      "group": "nodes",
      "data": {
        "id": "spine1",
        "label": "Spine 1",
        "device_type": "spine",
        "ip": "192.168.0.11",
        "icon": "spine.png",
        "parent": "dc1"
      },
      "classes": "device-type-spine",
      "position": {"x": 100, "y": 50}
    },
    {
      "group": "nodes",
      "data": {
        "id": "dc1",
        "label": "Data Center 1",
        "isZone": true,
        "zoneColor": "#071c35",
        "zoneBackground": "rgba(7, 28, 53, 0.05)",
        "zoneBorderStyle": "solid"
      },
      "classes": "zone-parent"
    },
    {
      "group": "edges",
      "data": {
        "id": "spine1|leaf1",
        "source": "spine1",
        "target": "leaf1",
        "source_port": "Ethernet1",
        "target_port": "Ethernet49"
      }
    }
  ],
  "annotations": [
    {
      "text": "Core Layer",
      "position": {"x": 200, "y": 30},
      "color": "#4c5cae",
      "font_size": 14,
      "background": true
    }
  ]
}
```

---

## Usage Examples

### Basic Inline Topology

```rst
.. topology-diagram::

   nodes:
     - id: spine1
       label: "Spine 1"
       type: spine
       ip: 192.168.0.11
     - id: leaf1
       label: "Leaf 1"
       type: leaf
       ip: 192.168.0.12

   edges:
     - source: spine1
       target: leaf1
       source_port: Ethernet1
       target_port: Ethernet49
```

### External File with Options

```rst
.. topology-diagram::
   :file: topologies/lab1-topology.yml
   :height: 700
   :layout: preset
   :title: Lab 1 Network
   :no-live-status:
```

### With Zones and Annotations

```rst
.. topology-diagram::
   :layout: dagre
   :height: 800

   nodes:
     - id: spine1
       type: spine
       ip: 192.168.0.11
       zone: dc1
     - id: spine2
       type: spine
       ip: 192.168.0.12
       zone: dc2
     - id: leaf1
       type: leaf
       ip: 192.168.0.21
       zone: dc1
     - id: leaf2
       type: leaf
       ip: 192.168.0.22
       zone: dc2

   edges:
     - source: spine1
       target: leaf1
       source_port: Ethernet1
       target_port: Ethernet49
     - source: spine2
       target: leaf2
       source_port: Ethernet1
       target_port: Ethernet49

   zones:
     - id: dc1
       label: "Data Center 1"
       color: "#071c35"
       background: "rgba(7, 28, 53, 0.05)"
     - id: dc2
       label: "Data Center 2"
       color: "#4c5cae"
       background: "rgba(76, 92, 174, 0.05)"

   annotations:
     - text: "Primary Site"
       position: {x: 100, y: 30}
       color: "#071c35"
     - text: "DR Site"
       position: {x: 400, y: 30}
       color: "#4c5cae"
```

### Preset Layout with Exact Positions

```rst
.. topology-diagram::
   :layout: preset
   :height: 500

   nodes:
     - id: spine1
       label: "Spine 1"
       type: spine
       position: {x: 200, y: 100}
     - id: leaf1
       label: "Leaf 1"
       type: leaf
       position: {x: 100, y: 300}
     - id: leaf2
       label: "Leaf 2"
       type: leaf
       position: {x: 300, y: 300}

   edges:
     - source: spine1
       target: leaf1
     - source: spine1
       target: leaf2
```

---

## Testing

The package includes pytest tests in `tests/`:

- `test_directive.py` - Directive parsing and validation
- `test_schema.py` - Schema validation
- `test_build.py` - Full Sphinx build integration

**Run tests**:
```bash
cd nested-labvm/atd-docker/sphinx-atl-topology
pip install -r requirements-dev.txt
pytest tests/
```

---

## Integration with Sphinx Build

### conf.py Configuration

```python
# Minimal configuration
extensions = ['sphinx_atl_topology']

# No additional configuration required!
```

### Build Process Flow

1. Sphinx reads RST files
2. Encounters `.. topology-diagram::` directive
3. `TopologyDiagramDirective.run()` executes:
   - Loads and parses YAML
   - Validates with `validate_topology()`
   - Converts to Cytoscape elements
   - Creates `TopologyDiagramNode`
4. During HTML translation:
   - `visit_topology_node()` generates HTML div
   - JSON config embedded in `data-topology` attribute
5. Static assets (CSS, JS) included in `<head>`
6. On page load in browser:
   - `ATLTopologyViewer.init()` finds all containers
   - Initializes viewer instances
   - Renders interactive diagrams

---

## Error Handling

### Build-Time Errors

**YAML Syntax Error**:
```
WARNING: Invalid YAML in topology-diagram: mapping values are not allowed here
```

**Validation Error**:
```
WARNING: Topology validation errors: Node 0 missing required "id" field; Duplicate node id: spine1
```

**Missing File**:
```
WARNING: Topology file not found: topologies/missing.yml
```

### Runtime Errors

**WebSocket Unavailable**: Status indicators gracefully degrade (no errors shown to user)

**Invalid JSON**: Container shows "Failed to load topology diagram"

**Missing Dependencies**: Console error if Cytoscape.js not loaded

---

## Browser Compatibility

- Modern browsers with ES6 module support
- WebSocket support for live status (graceful fallback)
- HTML5 Drag and Drop API (builder only)
- CSS Grid and Flexbox

**Tested**: Chrome 90+, Firefox 88+, Safari 14+, Edge 90+

---

## Performance Considerations

- Large topologies (100+ nodes): Use `preset` layout with pre-calculated positions
- Disable live status for static diagrams: `:no-live-status:`
- Multiple diagrams per page: Each creates separate Cytoscape instance
- Layout algorithms: `dagre` is fastest for hierarchical, `cose` for general graphs

---

## Related Documentation

- [Diagram Builder Documentation](../uilanding/src/html/js/diagram-builder/DOCUMENTATION.md)
- [Cytoscape.js Documentation](https://js.cytoscape.org/)
- [Sphinx Extension Development](https://www.sphinx-doc.org/en/master/development/index.html)

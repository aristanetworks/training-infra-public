# Interactive Topology Diagram Builder - API Documentation

## Overview

The Diagram Builder is a modular, web-based interactive topology diagram editor for creating network topology diagrams. It provides drag-and-drop device placement, connection management, zone grouping, text annotations, and multiple import/export formats.

**Architecture**: ES6 modules with a central orchestrator pattern. The DiagramBuilder class coordinates 9 specialized manager classes, each responsible for a specific feature domain.

**Key Technologies**:
- Cytoscape.js for graph rendering and manipulation
- ES6 modules for code organization
- YAML/JSON for data interchange
- WebSocket for live status updates (in viewer)
- HTML5 Drag and Drop API

---

## Module Reference

### 1. diagram-builder.js

**Class**: `DiagramBuilder`

The main orchestrator that initializes and coordinates all builder components.

#### Constructor

```javascript
constructor()
```

Initializes all manager instances, undo/redo stacks, and binds toolbar/keyboard events.

**State Properties**:
- `canvas: BuilderCanvas` - Cytoscape editing canvas
- `palette: DevicePalette` - Device type palette
- `properties: PropertiesPanel` - Context-sensitive property editor
- `connections: ConnectionManager` - Edge creation manager
- `zones: ZoneManager` - Zone/group manager
- `annotations: AnnotationManager` - Text annotation manager
- `exportMgr: ExportManager` - YAML/RST export
- `importMgr: ImportManager` - Import from various sources
- `preview: PreviewManager` - Preview modal
- `undoStack: Array<Object>` - Undo history (max 50 items)
- `redoStack: Array<Object>` - Redo history
- `mode: string` - Current editing mode: 'select', 'connection', 'annotation'

#### Methods

##### init()
```javascript
init(): void
```
Initializes all components in correct dependency order. Called automatically by constructor.

##### bindToolbar()
```javascript
bindToolbar(): void
```
Binds click event handlers to all toolbar buttons (connection, zone, annotation, delete, undo, redo, fit, preview, export, import).

##### bindKeyboard()
```javascript
bindKeyboard(): void
```
Binds keyboard shortcuts:
- `Delete/Backspace` - Delete selected elements
- `c` - Toggle connection mode
- `z` - Create zone from selection
- `a` - Toggle annotation mode
- `f` - Fit to viewport
- `p` - Preview
- `Escape` - Exit mode, deselect
- `Ctrl+Z` - Undo
- `Ctrl+Y / Ctrl+Shift+Z` - Redo

##### setMode(mode)
```javascript
setMode(mode: 'select' | 'connection' | 'annotation'): void
```
Changes the editing mode and updates UI state (toolbar buttons, cursor, mode indicator).

**Parameters**:
- `mode` - Target mode

##### toggleConnectionMode()
```javascript
toggleConnectionMode(): void
```
Toggles connection creation mode on/off.

##### toggleAnnotationMode()
```javascript
toggleAnnotationMode(): void
```
Toggles annotation creation mode on/off.

##### addDevice(type, position)
```javascript
addDevice(type: string, position: {x: number, y: number}): void
```
Adds a new device node to the canvas at the specified position.

**Parameters**:
- `type` - Device type ID (e.g., 'spine', 'leaf', 'host')
- `position` - Canvas model coordinates {x, y}

**Side Effects**: Saves state, updates status bar, shows toast notification.

##### generateId(type)
```javascript
generateId(type: string): string
```
Generates a unique ID for a new device by appending a counter (e.g., 'spine1', 'spine2').

**Parameters**:
- `type` - Device type

**Returns**: Unique ID string

##### generateLabel(type, id)
```javascript
generateLabel(type: string, id: string): string
```
Generates a human-readable label from an ID (e.g., 'spine1' → 'Spine 1').

**Parameters**:
- `type` - Device type
- `id` - Generated ID

**Returns**: Display label

##### updateNode(id, changes)
```javascript
updateNode(id: string, changes: {label?, ip?, device_type?, zone?}): void
```
Updates node properties. Handles zone parent assignment.

**Parameters**:
- `id` - Node ID
- `changes` - Object with properties to update

**Side Effects**: Saves state.

##### updateEdge(id, changes)
```javascript
updateEdge(id: string, changes: {source_port?, target_port?}): void
```
Updates edge properties.

**Parameters**:
- `id` - Edge ID
- `changes` - Object with properties to update

**Side Effects**: Saves state.

##### deleteSelected()
```javascript
deleteSelected(): void
```
Deletes selected elements. For zone nodes, moves children out first.

**Side Effects**: Clears properties panel, saves state, updates status bar.

##### addZone()
```javascript
addZone(): void
```
Creates a new zone containing currently selected nodes.

**Side Effects**: Saves state, updates status bar.

##### onSelectionChange(elements)
```javascript
onSelectionChange(elements: Array): void
```
Callback invoked when Cytoscape selection changes. Updates properties panel to show selected element.

**Parameters**:
- `elements` - Array of selected Cytoscape elements

##### getFullState()
```javascript
getFullState(): Object
```
Serializes the complete diagram state to a plain object.

**Returns**: Object with structure:
```javascript
{
  settings: {
    title: string,
    height: number,
    layout: string,
    live_status: boolean,
    device_access: boolean,
    show_port_labels: boolean
  },
  nodes: [{id, label, type, position?, ip?, zone?}, ...],
  edges: [{source, target, source_port?, target_port?}, ...],
  zones: [{id, label, color, background, border_style}, ...],
  annotations: [{text, position, color, font_size, background}, ...]
}
```

##### loadState(data)
```javascript
loadState(data: Object): void
```
Loads a complete diagram state, replacing current content.

**Parameters**:
- `data` - State object (same structure as getFullState())

**Side Effects**: Clears canvas, applies settings, adds all elements, runs layout, saves state.

##### saveState()
```javascript
saveState(): void
```
Pushes current state to undo stack. Called automatically after most operations.

##### undo()
```javascript
undo(): void
```
Restores previous state from undo stack.

##### redo()
```javascript
redo(): void
```
Restores next state from redo stack.

##### restoreState(state)
```javascript
restoreState(state: Object): void
```
Internal method to restore a saved state.

**Parameters**:
- `state` - State snapshot

##### updateStatusBar()
```javascript
updateStatusBar(): void
```
Updates status bar with node/edge/zone counts.

##### showToast(message, type)
```javascript
showToast(message: string, type?: 'info' | 'success' | 'error'): void
```
Displays a temporary notification toast.

**Parameters**:
- `message` - Message text
- `type` - Toast style (default: 'info')

---

### 2. builder-canvas.js

**Class**: `BuilderCanvas`

Manages the Cytoscape.js editing canvas with drag-and-drop support.

#### Constructor

```javascript
constructor(containerId: string, options: Object)
```

**Parameters**:
- `containerId` - DOM element ID for canvas container
- `options` - Configuration object:
  - `onSelect?: (elements) => void` - Selection change callback
  - `onPositionChange?: () => void` - Node drag end callback
  - `onDrop?: (type, position) => void` - Device drop callback

#### Methods

##### initCytoscape()
```javascript
initCytoscape(): void
```
Initializes Cytoscape instance with builder-specific styles (zone support, port labels).

##### bindEvents()
```javascript
bindEvents(): void
```
Binds Cytoscape events for selection and drag handling.

##### bindDragDrop()
```javascript
bindDragDrop(): void
```
Binds HTML5 drag-and-drop events for adding devices from palette.

##### startDrag(type, event)
```javascript
startDrag(type: string, event: DragEvent): void
```
Stores drag type for drop handling.

**Parameters**:
- `type` - Device type being dragged
- `event` - Drag event

##### runLayout(layoutName)
```javascript
runLayout(layoutName: string): void
```
Runs a Cytoscape layout algorithm.

**Parameters**:
- `layoutName` - Layout name: 'dagre', 'cose', 'concentric', 'grid', 'preset'

##### fit()
```javascript
fit(): void
```
Fits viewport to all elements with 30px padding.

##### clear()
```javascript
clear(): void
```
Removes all elements from canvas.

---

### 3. device-palette.js

**Class**: `DevicePalette`

Renders a categorized, searchable palette of draggable device types.

#### Constructor

```javascript
constructor(containerId: string, searchId: string, options: Object)
```

**Parameters**:
- `containerId` - DOM element ID for palette body
- `searchId` - DOM element ID for search input
- `options` - Configuration object:
  - `onDragStart?: (type, event) => void` - Drag start callback

#### Methods

##### render()
```javascript
render(): void
```
Renders device palette with categories: Provider, Core, Edge, Fabric, Endpoint.

##### bindSearch()
```javascript
bindSearch(): void
```
Binds search input to filter devices by name/type. Expands/collapses categories based on matches.

##### getDeviceTypes()
```javascript
getDeviceTypes(): Object
```
Returns the complete device type definition object.

**Returns**: Object with category structure

---

### 4. properties-panel.js

**Class**: `PropertiesPanel`

Context-sensitive property editor that adapts to the selected element type.

#### Constructor

```javascript
constructor(options: Object)
```

**Parameters**:
- `options` - Configuration object:
  - `onNodeChange?: (id, changes) => void` - Node property change callback
  - `onEdgeChange?: (id, changes) => void` - Edge property change callback
  - `onZoneChange?: (id, changes) => void` - Zone property change callback
  - `onAnnotationChange?: (index, changes) => void` - Annotation change callback
  - `zones?: ZoneManager` - Zone manager reference for dropdown

#### Methods

##### clear()
```javascript
clear(): void
```
Clears panel and shows empty state message.

##### showNode(node)
```javascript
showNode(node: CytoscapeNode): void
```
Displays node property form: ID (readonly), Label, Type, IP, Zone, Position (X, Y).

**Parameters**:
- `node` - Cytoscape node element

##### bindNodeInputs(nodeId)
```javascript
bindNodeInputs(nodeId: string): void
```
Internal method to bind input change events to node update callbacks.

**Parameters**:
- `nodeId` - Node ID

##### showEdge(edge)
```javascript
showEdge(edge: CytoscapeEdge): void
```
Displays edge property form: Source (readonly), Target (readonly), Source Port, Target Port.

**Parameters**:
- `edge` - Cytoscape edge element

##### showZone(node)
```javascript
showZone(node: CytoscapeNode): void
```
Displays zone property form: ID (readonly), Label, Border Color, Background, Border Style.

**Parameters**:
- `node` - Cytoscape zone parent node

##### showAnnotation(annotation)
```javascript
showAnnotation(annotation: Object): void
```
Displays annotation property form: Text, Color, Font Size, Show Background checkbox.

**Parameters**:
- `annotation` - Annotation object with index property

##### extractHexFromRgba(rgba)
```javascript
extractHexFromRgba(rgba: string): string
```
Converts rgba() string to hex color for color input.

**Parameters**:
- `rgba` - RGBA color string

**Returns**: Hex color string

##### escapeHtml(str)
```javascript
escapeHtml(str: string): string
```
Escapes HTML special characters for safe display.

**Parameters**:
- `str` - Input string

**Returns**: Escaped string

---

### 5. connection-manager.js

**Class**: `ConnectionManager`

Manages edge creation via two-click interaction.

#### Constructor

```javascript
constructor(cy: Cytoscape, options: Object)
```

**Parameters**:
- `cy` - Cytoscape instance
- `options` - Configuration object:
  - `onConnectionCreated?: (edge) => void` - Edge creation callback
  - `onModeChange?: (active) => void` - Mode activation callback

#### Methods

##### activate()
```javascript
activate(): void
```
Activates connection mode. Highlights clickable nodes.

##### deactivate()
```javascript
deactivate(): void
```
Deactivates connection mode. Removes highlights and temp edges.

##### handleNodeClick(e)
```javascript
handleNodeClick(e: CytoscapeEvent): void
```
Handles node click during connection mode:
1. First click: Select source node
2. Second click: Create edge (checks for duplicates)

**Parameters**:
- `e` - Cytoscape tap event

---

### 6. zone-manager.js

**Class**: `ZoneManager`

Manages zone (compound node) creation and editing.

#### Constructor

```javascript
constructor(cy: Cytoscape, options: Object)
```

**Parameters**:
- `cy` - Cytoscape instance
- `options` - Configuration object:
  - `onChange?: () => void` - Change callback

#### Methods

##### createZone(selectedNodes)
```javascript
createZone(selectedNodes: CytoscapeCollection): string
```
Creates a new zone containing the selected nodes.

**Parameters**:
- `selectedNodes` - Cytoscape collection of nodes to include

**Returns**: Zone ID

**Side Effects**: Calls onChange callback.

##### addZone(zoneData)
```javascript
addZone(zoneData: Object): void
```
Adds a zone from serialized data (used during import).

**Parameters**:
- `zoneData` - Object: {id, label, color, background, border_style}

##### updateZone(id, changes)
```javascript
updateZone(id: string, changes: Object): void
```
Updates zone properties.

**Parameters**:
- `id` - Zone ID
- `changes` - Properties to update: {label?, color?, background?, border_style?}

**Side Effects**: Calls onChange callback.

##### getZones()
```javascript
getZones(): Array<Object>
```
Returns all zones as serializable objects.

**Returns**: Array of zone objects

##### getZoneList()
```javascript
getZoneList(): Array<{id, label}>
```
Returns simplified zone list for dropdown population.

**Returns**: Array of {id, label} objects

##### removeZone(id)
```javascript
removeZone(id: string): void
```
Removes a zone after moving children out.

**Parameters**:
- `id` - Zone ID

**Side Effects**: Calls onChange callback.

---

### 7. annotation-manager.js

**Class**: `AnnotationManager`

Manages HTML overlay text annotations that track canvas pan/zoom.

#### Constructor

```javascript
constructor(cy: Cytoscape, container: HTMLElement, options: Object)
```

**Parameters**:
- `cy` - Cytoscape instance
- `container` - Overlay container element
- `options` - Configuration object:
  - `onChange?: () => void` - Change callback
  - `onSelect?: (annotation) => void` - Selection callback

#### Methods

##### activateCreateMode()
```javascript
activateCreateMode(): void
```
Activates annotation creation mode. Disables canvas panning.

##### deactivateCreateMode()
```javascript
deactivateCreateMode(): void
```
Deactivates annotation creation mode. Re-enables panning.

##### handleCanvasClick(e)
```javascript
handleCanvasClick(e: MouseEvent): void
```
Handles canvas click during create mode to place annotation.

**Parameters**:
- `e` - Mouse event

##### createAnnotation(text, modelPosition, opts)
```javascript
createAnnotation(text: string, modelPosition: {x, y}, opts?: Object): Object
```
Creates a new annotation at the specified position.

**Parameters**:
- `text` - Annotation text
- `modelPosition` - Canvas model coordinates {x, y}
- `opts` - Optional settings:
  - `color?: string` - Text color (default: '#4c5cae')
  - `fontSize?: number` - Font size in px (default: 12)
  - `background?: boolean` - Show background (default: true)

**Returns**: Created annotation object

**Side Effects**: Calls onChange callback.

##### makeDraggable(el, annotation)
```javascript
makeDraggable(el: HTMLElement, annotation: Object): void
```
Makes annotation element draggable.

**Parameters**:
- `el` - DOM element
- `annotation` - Annotation object to update

##### selectAnnotation(index)
```javascript
selectAnnotation(index: number): void
```
Selects an annotation and deselects others.

**Parameters**:
- `index` - Annotation index

**Side Effects**: Calls onSelect callback.

##### updateAnnotation(index, changes)
```javascript
updateAnnotation(index: number, changes: Object): void
```
Updates annotation properties.

**Parameters**:
- `index` - Annotation index
- `changes` - Properties to update: {text?, color?, fontSize?, background?}

**Side Effects**: Calls onChange callback.

##### updatePositions()
```javascript
updatePositions(): void
```
Updates annotation DOM positions based on current pan/zoom. Called automatically on pan/zoom events.

##### getAnnotations()
```javascript
getAnnotations(): Array<Object>
```
Returns all annotations as serializable objects.

**Returns**: Array of annotation objects

##### removeAnnotation(index)
```javascript
removeAnnotation(index: number): void
```
Removes an annotation.

**Parameters**:
- `index` - Annotation index

**Side Effects**: Calls onChange callback.

##### clear()
```javascript
clear(): void
```
Removes all annotations.

---

### 8. export-manager.js

**Class**: `ExportManager`

Generates YAML and RST exports of the diagram.

#### Constructor

```javascript
constructor(options: Object)
```

**Parameters**:
- `options` - Configuration object:
  - `getState: () => Object` - Function to get current state
  - `cy: Cytoscape` - Cytoscape instance
  - `annotations: AnnotationManager` - Annotation manager
  - `zones: ZoneManager` - Zone manager

#### Methods

##### toYAML()
```javascript
toYAML(): string
```
Generates YAML string from current state.

**Returns**: YAML string

##### toRST()
```javascript
toRST(): string
```
Generates RST `.. topology-diagram::` directive with inline YAML.

**Returns**: RST string

##### copyYAML()
```javascript
async copyYAML(): Promise<void>
```
Copies YAML to clipboard.

##### copyRST()
```javascript
async copyRST(): Promise<void>
```
Copies RST directive to clipboard.

##### downloadYML()
```javascript
downloadYML(): void
```
Downloads YAML as .yml file.

##### copyToClipboard(text, message)
```javascript
async copyToClipboard(text: string, message: string): Promise<void>
```
Copies text to clipboard with fallback for older browsers.

**Parameters**:
- `text` - Text to copy
- `message` - Success toast message

##### escapeYaml(str)
```javascript
escapeYaml(str: string): string
```
Escapes double quotes for YAML strings.

**Parameters**:
- `str` - Input string

**Returns**: Escaped string

---

### 9. import-manager.js

**Class**: `ImportManager`

Handles importing topologies from multiple sources: YAML/JSON files, topo_build.yml, and live API.

#### Constructor

```javascript
constructor(options: Object)
```

**Parameters**:
- `options` - Configuration object:
  - `onImport: (data) => void` - Import callback
  - `canvas: BuilderCanvas` - Canvas reference
  - `zones: ZoneManager` - Zone manager
  - `annotations: AnnotationManager` - Annotation manager

#### Methods

##### bindModalEvents()
```javascript
bindModalEvents(): void
```
Binds events for import modal (tabs, file upload, drag-and-drop).

##### readFile(file)
```javascript
readFile(file: File): void
```
Reads a file and populates import textarea.

**Parameters**:
- `file` - File object

##### showImportModal(type)
```javascript
showImportModal(type: 'topology' | 'yaml'): void
```
Shows import modal with appropriate title/placeholder.

**Parameters**:
- `type` - Import type: 'topology' (topo_build.yml) or 'yaml' (diagram YAML)

##### hideModal()
```javascript
hideModal(): void
```
Hides import modal.

##### confirmImport()
```javascript
confirmImport(): void
```
Processes import from modal textarea.

##### importFromAPI()
```javascript
async importFromAPI(): Promise<void>
```
Fetches topology from `/td-api/topology` and imports it.

##### parseDiagramData(content)
```javascript
parseDiagramData(content: string): Object
```
Parses YAML or JSON content to object.

**Parameters**:
- `content` - YAML or JSON string

**Returns**: Parsed object

**Throws**: Error if parsing fails

##### convertAPIData(apiData)
```javascript
convertAPIData(apiData: Object): Object
```
Converts API response format to diagram schema.

**Parameters**:
- `apiData` - API response object

**Returns**: Diagram state object

---

### 10. topo-build-converter.js

**Class**: `TopoBuildConverter`

Converts topo_build.yml format (Arista Training Labs topology definition) to diagram schema.

#### Methods

##### convert(yamlContent)
```javascript
convert(yamlContent: string): Object
```
Parses topo_build.yml and converts to diagram schema.

**Parameters**:
- `yamlContent` - YAML string

**Returns**: Diagram state object

**Algorithm**:
1. Parse YAML
2. Extract nodes with IP addresses
3. Classify device types from names
4. Extract and deduplicate edges from neighbor relationships
5. Auto-detect zones from DC suffix patterns (e.g., '-DC1')

##### classifyDevice(name)
```javascript
classifyDevice(name: string): string
```
Classifies device type from device name using pattern matching.

**Parameters**:
- `name` - Device name

**Returns**: Device type string

**Classification Rules**:
- Custom patterns: 'RR', 'RR1', 'P1', 'GW1', 'A1', 'B1', etc.
- Startswith: 'PE', 'CE', 'BL'
- Contains (case-insensitive): 'spine', 'leaf', 'host', 'router', etc.

##### makeEdgeKey(device1, port1, device2, port2)
```javascript
makeEdgeKey(device1: string, port1: string, device2: string, port2: string): string
```
Creates normalized edge key for deduplication (A-B and B-A produce same key).

**Parameters**:
- `device1`, `port1` - First endpoint
- `device2`, `port2` - Second endpoint

**Returns**: Normalized edge key string

##### detectZones(nodes)
```javascript
detectZones(nodes: Array): Array
```
Auto-detects zones from device naming patterns (DC suffix: '-DC1', '_DC2').

**Parameters**:
- `nodes` - Array of node objects (mutated to add zone property)

**Returns**: Array of zone definitions

##### hexToRgb(hex)
```javascript
hexToRgb(hex: string): string
```
Converts hex color to RGB component string.

**Parameters**:
- `hex` - Hex color (e.g., '#071c35')

**Returns**: RGB string (e.g., '7, 28, 53')

---

### 11. preview-manager.js

**Class**: `PreviewManager`

Displays a read-only preview of the topology in a modal.

#### Constructor

```javascript
constructor(options: Object)
```

**Parameters**:
- `options` - Configuration object:
  - `getState: () => Object` - Function to get current state

#### Methods

##### show()
```javascript
show(): void
```
Opens preview modal and renders topology with current layout settings.

##### hide()
```javascript
hide(): void
```
Closes preview modal and destroys Cytoscape instance.

##### buildElements(state)
```javascript
buildElements(state: Object): Array
```
Converts state object to Cytoscape elements array.

**Parameters**:
- `state` - Diagram state object

**Returns**: Cytoscape elements array

---

## Data Schemas

### Diagram State Schema

```yaml
settings:
  title: "Network Topology"
  layout: dagre  # preset | dagre | cose | concentric | grid
  height: 500
  live_status: true
  device_access: true
  show_port_labels: true

nodes:
  - id: spine1
    label: "Spine 1"
    type: spine
    ip: 192.168.0.11
    zone: dc1  # optional
    position:  # optional (required for layout: preset)
      x: 100
      y: 50

edges:
  - source: spine1
    target: leaf1
    source_port: Ethernet1
    target_port: Ethernet49

zones:
  - id: dc1
    label: "Data Center 1"
    color: "#071c35"
    background: "rgba(7, 28, 53, 0.05)"
    border_style: solid  # solid | dashed

annotations:
  - text: "Core Layer"
    position: {x: 200, y: 30}
    color: "#4c5cae"
    font_size: 14
    background: true
```

### Device Types

**Provider**: internet, isp
**Core**: core, dci, p, rr
**Edge**: borderleaf, pe, ce, gw, router, firewall, velo_orchestrator, velo_gateway, velo_edge
**Fabric**: spine, leaf, memleaf
**Endpoint**: host, linux_host, customer, oob, other

---

## Event Flow Examples

### Adding a Device

1. User drags device from palette
2. `DevicePalette` fires `onDragStart(type, event)`
3. `BuilderCanvas.startDrag()` stores type
4. User drops on canvas
5. `BuilderCanvas` calculates model position and fires `onDrop(type, position)`
6. `DiagramBuilder.addDevice()` generates ID/label, adds node to Cytoscape
7. `DiagramBuilder.saveState()` pushes to undo stack
8. `DiagramBuilder.updateStatusBar()` updates counts

### Creating a Connection

1. User clicks connection button
2. `DiagramBuilder.toggleConnectionMode()` calls `ConnectionManager.activate()`
3. `ConnectionManager` highlights clickable nodes
4. User clicks source node → stored
5. User clicks target node
6. `ConnectionManager` creates edge, fires `onConnectionCreated(edge)`
7. `DiagramBuilder.saveState()` saves state
8. Mode resets to 'select'

### Undo/Redo

1. User modifies diagram (add/delete/move)
2. `DiagramBuilder.saveState()` captures Cytoscape JSON + annotations
3. State pushed to `undoStack` (max 50)
4. User presses Ctrl+Z
5. `DiagramBuilder.undo()` pops current state to `redoStack`
6. Previous state restored via `DiagramBuilder.restoreState()`

---

## File Structure

```
diagram-builder/
├── diagram-builder.js          # Main orchestrator
├── builder-canvas.js           # Cytoscape editor
├── device-palette.js           # Device library
├── properties-panel.js         # Property editor
├── connection-manager.js       # Edge creation
├── zone-manager.js             # Zone management
├── annotation-manager.js       # Text annotations
├── export-manager.js           # YAML/RST export
├── import-manager.js           # Multi-source import
├── topo-build-converter.js     # topo_build.yml parser
├── preview-manager.js          # Preview modal
├── diagram-builder.mmd         # Architecture diagram
└── DOCUMENTATION.md            # This file
```

---

## Dependencies

**External**:
- Cytoscape.js 3.x
- Cytoscape-dagre layout extension
- js-yaml (CDN)

**Internal**:
- `../topology/cytoscape-styles.js` - Shared Cytoscape styles
- `../topology/layout-config.js` - Layout algorithm configurations

---

## Usage Example

```javascript
// The builder auto-initializes on DOMContentLoaded
// Access via global: window.diagramBuilder

// Programmatic access
const builder = window.diagramBuilder;

// Get current state
const state = builder.getFullState();
console.log(state);

// Import from API
await builder.importMgr.importFromAPI();

// Export YAML
const yaml = builder.exportMgr.toYAML();
console.log(yaml);

// Programmatic device addition
builder.addDevice('spine', {x: 100, y: 100});

// Create zone from selected nodes
const selected = builder.canvas.cy.nodes(':selected');
builder.zones.createZone(selected);
```

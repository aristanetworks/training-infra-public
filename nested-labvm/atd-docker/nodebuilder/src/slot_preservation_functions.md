# Orphaned Port Slot Preservation - Function Reference

Bug fix: Re-enable orphaned port slot preservation across the nodebuilder codebase.

Root cause: With `ENABLE_SLOT_PRESERVATION=true`, bridges were being deleted unconditionally
when a device was removed. Preserved interfaces on target VMs then pointed at non-existent
bridges, causing those VMs to fail to boot with "Cannot get interface MTU" errors.

Fix: Keep bridges alive whenever a slot is preserved. Handle missing bridges at startup
by recreating them from the orphaned slot registry.

---

## config.py

### `ENABLE_SLOT_PRESERVATION`

```
ENABLE_SLOT_PRESERVATION = os.getenv('ENABLE_SLOT_PRESERVATION', 'true').lower() == 'true'
```

| Detail | Value |
|--------|-------|
| Type | `bool` (module-level constant) |
| Default (before fix) | `'false'` |
| Default (after fix) | `'true'` |
| Env override | `ENABLE_SLOT_PRESERVATION` |

Controls whether interface slots are preserved on target VMs when a user device is deleted.
When `True`, the interface remains attached on the target VM and the OVS bridge is kept alive
so the VM can continue to boot. The slot is recorded in `orphaned_interfaces.yaml` for reuse.

---

## resource_manager.py

### `ResourceManager.cleanup_node_bridges()`

```python
def cleanup_node_bridges(
    self,
    node_name: str,
    node_info: Dict,
    skip_bridges: Optional[set] = None
) -> List[str]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `node_name` | `str` | Name of the node whose bridges are being cleaned up |
| `node_info` | `Dict` | Node info dict containing `neighbors` list |
| `skip_bridges` | `Optional[set]` | Bridge names to leave intact (preserved for orphaned slots) |

**Returns:** `List[str]` - bridge names that were actually deleted.

**Change:** Added `skip_bridges` parameter. When a bridge name is present in this set the
method logs that the bridge is being kept and skips its deletion. Previously all bridges
were deleted unconditionally.

---

### `ResourceManager.delete_node_completely()`

```python
def delete_node_completely(self, vm_name: str, node_info: Dict) -> Dict
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_name` | `str` | Name of the VM/node to delete |
| `node_info` | `Dict` | Node info dict (neighbors, metadata) |

**Returns:** `Dict` with keys: `name`, `steps`, `errors`, `status`, `slots_preserved`,
`slots_detached_fallback`, `interfaces_detached`.

**Change:** After calling `detach_all_node_interfaces()`, collects the set of bridges that
were preserved (`status == 'slot_preserved'`) and passes them as `skip_bridges` to
`cleanup_node_bridges()`. This prevents the bridge from being deleted immediately after
the slot is recorded, fixing the boot failure.

```python
preserved_bridges = {
    item.get('bridge') for item in detached
    if item.get('status') == 'slot_preserved' and item.get('bridge')
}
deleted_bridges = self.cleanup_node_bridges(
    vm_name, node_info, skip_bridges=preserved_bridges or None
)
```

---

### `ResourceManager.cleanup_connection()`

```python
def cleanup_connection(
    self,
    connection: Optional[Dict],
    connection_name: str = ''
) -> Dict
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `connection` | `Optional[Dict]` | Connection dict: `bridge`, `target_device`, `target_port` |
| `connection_name` | `str` | Label for log messages (e.g. `'inside'`, `'outside'`) |

**Returns:** `Dict` with keys: `slot_preserved`, `interface_detached`, `bridge_deleted`,
`target_device`, `reason`, `errors`.

**Change:** Bridge deletion is now conditional on whether the slot was actually preserved.
Previously the bridge was always deleted at the end of this method.

```python
# Delete OVS bridge only when slot was NOT preserved
if bridge_name and not result['slot_preserved']:
    delete_ovs_bridge(bridge_name)
    result['bridge_deleted'] = True
elif bridge_name and result['slot_preserved']:
    # Keep bridge -- target VM needs it to boot
    logger.info(f"Keeping bridge {bridge_name} (preserved for orphaned slot)")
```

---

### `ResourceManager.cleanup_all_orphaned_bridges()`

```python
def cleanup_all_orphaned_bridges(self) -> Dict
```

**Returns:** `Dict` with keys: `scanned`, `orphaned_found`, `deleted`, `failed`,
`skipped_system`, `skipped_healthy`, `skipped_preserved`.

**Change:** Now cross-references the orphaned slot registry before deleting a bridge.
Bridges recorded in `orphaned_interfaces.yaml` are skipped with `skipped_preserved`
counter incremented. The `preserved_slot_bridges` set is built by loading all orphaned
slots and collecting their `old_bridge` values.

```python
from orphaned_interfaces import list_all_orphaned_slots
all_orphaned = list_all_orphaned_slots()
for device_name, slots in all_orphaned.items():
    for slot in slots:
        old_bridge = slot.get('old_bridge')
        if old_bridge:
            preserved_slot_bridges.add(old_bridge)

if bridge in preserved_slot_bridges:
    results['skipped_preserved'] += 1
    continue
```

---

## connection_manager.py

### `ConnectionManager.delete_connection()`

```python
def delete_connection(
    self,
    conn: Connection,
    detach_from_source: bool = True,
    detach_from_target: bool = True,
    preserve_target_slot: bool = None
) -> Dict
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `conn` | `Connection` | Dataclass: `source_device`, `source_port`, `target_device`, `target_port`, `bridge_name` |
| `detach_from_source` | `bool` | Whether to detach the interface from the source device |
| `detach_from_target` | `bool` | Whether to process the target device interface |
| `preserve_target_slot` | `bool | None` | If `None` uses `ENABLE_SLOT_PRESERVATION`; if `True` records orphaned slot instead of detaching |

**Returns:** `Dict` with keys: `connection`, `steps`, `errors`, `status`, `slot_preserved`.

**Change:** Bridge deletion is now gated on `slot_actually_preserved`. The check inspects
the `steps` list to confirm the `preserve_target_slot` step recorded status `'orphaned'`
before deciding to keep or delete the bridge.

```python
slot_actually_preserved = any(
    step.get('step') == 'preserve_target_slot' and step.get('status') == 'orphaned'
    for step in result['steps']
)

if slot_actually_preserved:
    # Keep bridge -- target VM needs it to remain bootable
    result['steps'].append({'step': 'keep_bridge', 'status': 'preserved', ...})
else:
    delete_ovs_bridge(conn.bridge_name)
```

---

### `ConnectionManager.create_connection()`

```python
def create_connection(self, conn: Connection) -> Dict
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `conn` | `Connection` | Connection dataclass |

**Returns:** `Dict` with keys: `connection`, `steps`, `status`, `reused_orphaned_slot`.

**Change:** After successfully reusing an orphaned slot and claiming it from the registry,
the old bridge that was kept alive is now explicitly deleted.

```python
old_bridge = orphaned_slot.get('old_bridge')
if old_bridge and old_bridge != conn.bridge_name:
    try:
        delete_ovs_bridge(old_bridge)
        logger.info(f"Cleaned up old bridge {old_bridge} after slot reuse")
    except Exception as e:
        logger.debug(f"Old bridge {old_bridge} cleanup skipped: {e}")
```

---

## orphaned_interfaces.py

### `cleanup_stale_orphaned_interfaces()`

```python
def cleanup_stale_orphaned_interfaces() -> Dict
```

**Returns:** `Dict` with keys: `detached_count`, `bridges_recreated`,
`devices_cleaned`, `devices_bridges_recreated`, `vms_restarted`, `errors`.

**Change:** Completely reworked. Previously the function only detached interfaces that
pointed to missing bridges. Now it first checks whether the interface MAC is recorded in
the orphaned slot registry.

- If the MAC matches a recorded orphaned slot: the bridge is **recreated** via
  `create_ovs_bridge()` so the VM can boot without losing its slot record. The slot
  remains in the registry for future reuse.
- If the MAC is not in the registry: the interface is **detached** as before (stale).

Both paths then attempt to start any affected VMs that are in `shut off` state.

```python
orphaned_lookup = {}
for device_name, slots in all_orphaned.items():
    for slot in slots:
        mac = slot.get('mac_address', '').lower()
        if mac:
            orphaned_lookup[(device_name.lower(), mac)] = slot

lookup_key = (vm_name.lower(), mac.lower())
orphaned_slot = orphaned_lookup.get(lookup_key)

if orphaned_slot:
    create_ovs_bridge(bridge_name)    # Recreate missing bridge
    result['bridges_recreated'] += 1
else:
    detach_interface_from_vm(vm_name, mac)   # Remove stale interface
    result['detached_count'] += 1
```

---

## slot_reuse.py

### `attach_interface_with_slot_reuse()`

```python
def attach_interface_with_slot_reuse(
    target_device: str,
    target_port: str,
    bridge_name: str,
    connection_dict: Optional[Dict] = None
) -> SlotReuseResult
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `target_device` | `str` | Name of the target VM |
| `target_port` | `str` | Port on target device, e.g. `"Ethernet5"` |
| `bridge_name` | `str` | New OVS bridge to connect the interface to |
| `connection_dict` | `Optional[Dict]` | If provided, `reused_orphaned_slot` key is set |

**Returns:** `SlotReuseResult(reused_slot, needs_reboot, target_device, error)`.

**Change:** After successfully reusing an orphaned slot (`status in ('updated', 'configured')`
and `claim_orphaned_slot()` succeeds), the old bridge that was kept alive for the slot is
now explicitly deleted.

```python
old_bridge = orphaned_slot.get('old_bridge')
if old_bridge and old_bridge != bridge_name:
    try:
        from interface_manager import delete_ovs_bridge
        delete_ovs_bridge(old_bridge)
        logger.info(f"Cleaned up old bridge {old_bridge} after slot reuse")
    except Exception as e:
        logger.debug(f"Old bridge {old_bridge} cleanup skipped: {e}")
```

---

## nodebuilder_service.py

### `on_startup()`

```python
async def on_startup(app)
```

**Change:** Startup logging updated to report the new `bridges_recreated` metric returned
by `cleanup_stale_orphaned_interfaces()`.

```python
orphan_result = cleanup_stale_orphaned_interfaces()
if orphan_result.get('bridges_recreated', 0) > 0:
    logger.info(
        f"Nodebuilder startup: Recreated {orphan_result['bridges_recreated']} bridge(s) "
        f"for recorded orphaned slots"
    )
if orphan_result['detached_count'] > 0:
    logger.info(...)
if orphan_result.get('vms_restarted'):
    logger.info(...)
```

---

## Diagram

See `slot_preservation_flow.mmd` in this directory for a flowchart of the full
delete → preserve → reuse lifecycle and the startup reconciliation path.

---

## Invariants After This Fix

1. If `ENABLE_SLOT_PRESERVATION=true` and a slot is recorded, the OVS bridge with the
   name stored in `old_bridge` **must remain alive** until the slot is claimed.
2. `cleanup_all_orphaned_bridges()` will never delete a bridge listed in the orphaned
   slot registry, regardless of port count.
3. At service startup, if a VM has an interface pointing at a missing bridge that matches
   a recorded orphaned slot, the bridge is recreated rather than the interface detached.
4. When a slot is claimed (reused), the old bridge is explicitly deleted by the reuse code.

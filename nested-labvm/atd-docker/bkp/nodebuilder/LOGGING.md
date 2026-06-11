# Nodebuilder Cloud Logging Reference

Service name: `nodebuilder`

All log events emitted by the Nodebuilder service for analytics and debugging. Logs are sent to GCP Cloud Logging with structured labels. The nodebuilder uses Python's standard `logging` module extensively — all `logger.info()`, `logger.error()`, `logger.warning()` calls are automatically routed to GCP Cloud Logging via `cloud_logging_utils.py`.

## Log Labels Schema

| Label | Description | Example |
|-------|-------------|---------|
| `lab_hostname` | Lab instance hostname | `training-level-x-cl-veos-abc123` |
| `service` | Service identifier | `nodebuilder` |
| `environment` | Container identifier | `nodebuilder-container` |

---

## VM Node Operations (vEOS)

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Creating vEOS node: {name} with IP {ip}, MAC {mac}` | INFO | vEOS VM creation started. Includes the device name, assigned management IP, and MAC address. |
| `Successfully created node: {name}` | INFO | vEOS VM created, started, and all connections attached successfully. |
| `VM creation failed for {name}: {e}` | ERROR | vEOS VM creation failed during image copy, XML generation, define, or start steps. |
| `Cleaning up partially created VM: {name}` | INFO | Automatic rollback triggered after a failed creation — destroying and undefining the partial VM. |
| `Failed to clean up VM {name}: {cleanup_error}` | WARNING | Rollback cleanup itself failed — may leave orphaned resources. |
| `Concurrent creation in progress, request queued timeout` | WARNING | Another node creation is in progress and the lock timed out. Client should retry. |
| `Deleting user-added node: {name}` | INFO | Node deletion initiated — will destroy VM, delete disk, detach interfaces, cleanup bridges. |
| `Successfully deleted node: {name}` | INFO | Node fully deleted including all associated resources. |
| `Editing connections for node: {name}` | INFO | Connection modification started — adding/removing connections to an existing node. |
| `Successfully edited node: {name}` | INFO | Connection edit completed successfully. |

---

## Linux Host Operations

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Creating Linux host: {name} (IP: {ip})` | INFO | Linux host VM creation started with cloud-init ISO generation. |
| `Successfully created Linux host: {name}` | INFO | Host VM created, started, and connected to target devices. |
| `Host VM creation failed for {name}: {e}` | ERROR | Host creation failed — image copy, ISO generation, or VM startup issue. |
| `Host {name} connection missing target_device` | WARNING | A host connection spec is missing the target device name — connection skipped. |
| `Deleting Linux host: {name}` | INFO | Host deletion initiated. |
| `Successfully deleted Linux host: {name}` | INFO | Host fully deleted. |
| `Cleaned up {removed_count} stale host(s): {removed_names}` | INFO | Stale host entries removed from persistence (VMs no longer exist). |

---

## Firewall Operations (VyOS)

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Creating VyOS firewall: {name}` | INFO | VyOS firewall VM creation started (in firewall_manager.py). |
| `Successfully created firewall: {name}` | INFO | Firewall VM created with cloud-init configuration applied. |
| `Deleting firewall: {name}` | INFO | Firewall deletion initiated. |
| `Cleaned up {removed_count} stale firewall(s): {removed_names}` | INFO | Stale firewall entries removed from persistence. |

---

## VeloCloud Device Operations

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Creating VeloCloud {device_type}: {name} (Mgmt IP: {mgmt_ip})` | INFO | VeloCloud device creation started — type is Edge, Gateway, or Orchestrator. |
| `Copying base image(s) for {name}` | INFO | Base disk image being copied from prestaged source. |
| `Generating cloud-init ISO for {name}` | INFO | Cloud-init ISO being created with activation key and network config. |
| `Generating VM XML for {name}` | INFO | Libvirt XML being generated with PCI slot assignments. |
| `Defining VM {name}` | INFO | VM being registered with libvirt. |
| `Starting VM {name}` | INFO | VM being started. |
| `Successfully created VeloCloud {device_type}: {name}` | INFO | VeloCloud device fully created and running. |
| `Deleting VeloCloud device: {name}` | INFO | VeloCloud device deletion started. |
| `Found {len(connections)} connections to clean up` | INFO | Number of OVS bridges to remove during deletion. |
| `Deleted VeloCloud device: {name}` | INFO | VeloCloud device fully deleted. |
| `Edge GE3 configured with mgmt_ip {mgmt_ip} for VCO connectivity` | INFO | Edge management interface configured for orchestrator access. |

---

## Cluster Operations

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Creating cluster from template: {template_id}` | INFO | Multi-node cluster creation initiated from a predefined template. |
| `Phase 1: Creating {N} VMs` | INFO | Cluster creation phase 1 — creating individual VMs with external connections. |
| `Created VM: {full_name}` | INFO | Individual cluster VM created successfully. |
| `Failed to create {full_name}: {e}` | ERROR | Individual cluster VM creation failed. |
| `Phase 2: Creating {N} internal connections` | INFO | Cluster creation phase 2 — wiring internal connections between cluster nodes. |
| `Connected: {from_name} <-> {to_name} (bridge: {bridge_name})` | INFO | Internal cluster connection created with bridge name. |
| `Failed to create internal connection {from_name} <-> {to_name}: {e}` | ERROR | Internal connection failed — may trigger rollback. |
| `Phase 3: Applying impairments to {N} internal bridges` | INFO | Cluster creation phase 3 — applying default latency to internal links. |
| `Successfully created cluster: {template_id} ({N} nodes)` | INFO | Cluster fully created with all phases complete. |
| `Auto-adjusted prefix from '{original}' to '{unique}' to avoid conflicts` | INFO | Cluster name prefix auto-modified to avoid duplicate device names. |

---

## Device Config & Reboot

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Saved config on {device} ({ip})` | INFO | Device running config saved via pyeAPI `copy running startup`. |
| `Error saving config on {device}: {e}` | ERROR | pyeAPI config save failed — device may be unreachable or auth failed. |
| `Rebooted device: {device}` | INFO | VM rebooted via `virsh reboot`. |
| `Error rebooting device {device}: {e}` | ERROR | virsh reboot command failed. |

---

## Restore & Reset

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Initiating full reset of user-added nodes` | INFO | Full reset of all user-added resources (VMs, hosts, firewalls, VeloCloud devices). |
| `Reset complete: deleted {N} nodes, {N} hosts, {N} firewalls, {N} velo devices, {N} bridges` | INFO | Reset completed with counts of each resource type removed. |

---

## Persistence & Security

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Saved user nodes to {path}` | INFO | User nodes YAML file written (atomic write via temp + rename). |
| `Saved user hosts to {path}` | INFO | User hosts YAML file written. |
| `Saved user firewalls to {path}` | INFO | User firewalls YAML file written. |
| `Saved user VeloCloud devices to {path}` | INFO | User VeloCloud devices YAML file written. |
| `Security: Attempted to load from disallowed path: {path}` | ERROR | Path traversal attack detected — file access outside allowed directory. |
| `Updated node {name} status to {status}` | INFO | Node status changed (e.g., `pending` → `running`, `running` → `deleting`). |
| `Removed node {name} from user nodes` | INFO | Node entry removed from persistence file. |

---

## Image Pre-staging

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Pre-staging: Starting host base image download...` | INFO | Background download of Linux host base image from GCS. |
| `Pre-staging: Host base image ready` | INFO | Host base image download complete. |
| `Pre-staging: Starting firewall base image download...` | INFO | Background download of VyOS firewall base image. |
| `Pre-staging: Firewall base image ready` | INFO | Firewall base image download complete. |
| `Pre-staging: Starting VeloCloud image downloads...` | INFO | Background download of VeloCloud Edge/Gateway/Orchestrator images. |
| `Pre-staging: All base images ready` | INFO | All base images downloaded and ready for VM creation. |
| `Pre-staging error: {e}` | ERROR | Image download failed — VMs of that type cannot be created. |
| `Downloading base image from {gcp_url}` | INFO | GCS download started with source URL and destination path. |

---

## Interface & Bridge Operations

| Log Message | Level | Description |
|-------------|-------|-------------|
| `Bridge name truncated: {full_name} -> {bridge_name}` | DEBUG | OVS bridge name exceeded 15-char limit and was truncated. |
| `Parse error for bridge {bridge_name}: {e}` | WARNING | Could not parse bridge name back to device/port — may be a manually created bridge. |

---

## GCP Log Explorer Queries

### All nodebuilder logs
```
logName="projects/<PROJECT>/logs/nodebuilder"
```

### Node creation events
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Creating vEOS node|Successfully created node"
```

### Host creation events
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Creating Linux host|Successfully created Linux host"
```

### Firewall creation events
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Creating VyOS firewall|Successfully created firewall"
```

### VeloCloud device events
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Creating VeloCloud|Deleting VeloCloud"
```

### Cluster creation events
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Creating cluster|Phase [123]|Successfully created cluster"
```

### All deletions
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Deleting|deleted"
```

### All errors
```
logName="projects/<PROJECT>/logs/nodebuilder"
severity="ERROR"
```

### Security alerts (path traversal attempts)
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Security:"
```

### Config save operations
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Saved config on"
```

### Device reboots
```
logName="projects/<PROJECT>/logs/nodebuilder"
jsonPayload.message=~"Rebooted device"
```

### Specific lab instance
```
logName="projects/<PROJECT>/logs/nodebuilder"
labels.lab_hostname="<LAB_HOSTNAME>"
```

---

## Architecture Notes

- **Logging approach:** The nodebuilder already uses Python's `logging` module extensively across all 20 source files. The `setup_cloud_logging('nodebuilder')` call replaces the root logger with a GCP-enabled handler, so all existing `logger.info()`, `logger.error()`, `logger.warning()`, and `logger.debug()` calls automatically route to GCP Cloud Logging.
- **Module-specific loggers:** Sub-modules use `logging.getLogger('nodebuilder.resource_manager')`, `logging.getLogger('nodebuilder.interface_manager')`, etc. These inherit from the root `nodebuilder` logger and also route to GCP.
- **Fallback:** If GCP Cloud Logging SDK is unavailable, falls back to the standard `logging.basicConfig()` console output.
- **Log level:** Configurable via `NODEBUILDER_LOG_LEVEL` environment variable (default: `INFO`). Set to `DEBUG` for bridge name parsing and port allocation details.
- **Sensitive data:** Error messages are sanitized via `sanitize_error()` before being sent to clients. Passwords and tokens are redacted via `redact_sensitive()` and `redact_dict()`.
- **No `safe_log()` needed:** Since the nodebuilder already uses the standard `logging` module pattern, there's no risk of logging crashes — the logging module handles errors internally.

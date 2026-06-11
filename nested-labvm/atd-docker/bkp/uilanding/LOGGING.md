# UILanding Cloud Logging Reference

All log events emitted by the UILanding service for analytics and debugging. Logs are sent to GCP Cloud Logging under service name `uilanding` with structured labels for filtering.

## Log Labels Schema

Every log entry includes these base labels:

| Label | Description | Example |
|-------|-------------|---------|
| `lab_hostname` | Lab instance hostname from ACCESS_INFO.yaml | `training-level-x-cl-veos-abc123` |
| `service` | Service identifier | `uilanding` |
| `environment` | Container identifier | `uilanding-container` |

---

## Server Lifecycle

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Cloud logging initialized for uilanding` | INFO | `status=initialized` | GCP Cloud Logging SDK connected. If this is missing, logging fell back to stdout-only mode. |
| `UILanding server started` | INFO | `port=80`, `topology=<topo_name>` | Tornado web server started listening. The `topology` label contains the topology name (e.g., `training-level-x-cl-veos`) which identifies the lab type for this instance. |

---

## Authentication & Login

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Login page accessed` | INFO | `event=page_view`, `page=login` | User loaded the `/login` page. This fires on every login page render, including redirects from unauthenticated access attempts. High frequency of this log without corresponding `login_success` may indicate auth redirect loops. |
| `Login successful` | INFO | `event=auth`, `action=login_success`, `username=<user>` | User authenticated with valid credentials. The `username` label contains the submitted username (e.g., `arista`). Use this to track per-user session starts. |
| `Login failed` | WARNING | `event=auth`, `action=login_failure`, `username=<user>` | Invalid username or password submitted. The `username` label shows what was attempted. Multiple failures from the same lab may indicate a misconfigured password or brute-force attempt. |

Passwords are never logged.

---

## Page Views

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Topology page accessed` | INFO | `event=page_view`, `page=topology`, `lab_type=<Lab\|Exam>` | User accessed the main home/topology page (`/`). The `lab_type` label distinguishes regular labs from exam sessions. This is the primary engagement metric — every authenticated visit generates this log. |
| `Terminal page accessed` | INFO | `event=page_view`, `page=terminal` | User navigated to the Switch Access terminal page (`/terminal`). This is the multi-tab SSH terminal interface. Track this to measure terminal feature adoption vs. legacy SSH. |
| `Console page accessed` | INFO | `event=page_view`, `page=console` | User navigated to the serial console page (`/console`). This provides virsh console access to devices. Generally lower usage than terminal. |

---

## WebSocket Events

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `WebSocket connection opened` | INFO | `event=websocket`, `action=connect` | Browser established a WebSocket connection for live topology status updates (uptime, CVP status, exam timer). Each open browser tab creates one WebSocket. Use connect/disconnect pairs to calculate session duration. |
| `WebSocket connection closed` | INFO | `event=websocket`, `action=disconnect` | WebSocket connection terminated. Happens when user closes the tab, navigates away, or network disconnects. A close without a prior open may indicate connection failures. |

---

## Connectivity Monitoring Events

Session lifecycle and connectivity diagnostics are logged with `event=connectivity`.

### Session Lifecycle
| Action | Level | Labels | Description |
|--------|-------|--------|-------------|
| `session_start` | info | `session_id`, `client_ip`, `reconnect_count` | WebSocket session opened |
| `session_end` | info | `session_id`, `client_ip`, `duration_seconds`, `missed_pongs`, `reconnect_count` | WebSocket session closed |
| `reconnect` | info | `session_id`, `client_ip`, `reconnect_gap_seconds`, `reconnect_count` | Client reconnected within 5 min of disconnect |
| `session_summary` | info | `session_id`, `client_ip`, `duration_seconds`, `missed_pongs`, `last_rtt_ms`, `reconnect_count`, `debug_mode` | Periodic summary every 5 minutes |

### Client Reports (via WebSocket)
| Action | Level | Labels | Description |
|--------|-------|--------|-------------|
| `periodic_summary` | info | `session_id`, `client_ip`, `ws_latency_ms`, `grpc_status`, `grpc_failures`, `event_count`, `session_uptime_s` | Client-side summary every 5 minutes |
| `reconnect_report` | warning | `session_id`, `client_ip`, `offline_duration_ms`, `offline_from`, `offline_to`, `buffered_event_count` | Client reconnect with offline event data |
| `state_change` | info | `session_id`, `client_ip`, `change_type`, `detail` | Client connectivity state change |

### Heartbeat
| Action | Level | Labels | Description |
|--------|-------|--------|-------------|
| `pong` | debug | `session_id`, `rtt_ms` | Pong received (debug mode only) |
| `missed_pongs` | warning | `session_id`, `missed_pongs`, `last_pong_age_seconds` | 3+ missed pong responses |

### Debug Mode
| Action | Level | Labels | Description |
|--------|-------|--------|-------------|
| `debug_toggle` | info | `session_id`, `debug_mode` | Debug mode toggled on/off |
| `buffered_event` | debug | `session_id`, `event_type`, `event_ts`, `event_data` | Individual buffered event (debug mode only) |

### GCP Log Explorer Queries for Connectivity

```
# All connectivity events for a session
resource.type="global"
labels.event="connectivity"
labels.session_id="<session-id>"

# All reconnect events (find problem clients)
resource.type="global"
labels.event="connectivity"
labels.action="reconnect"

# Sessions with missed pongs (flaky connections)
resource.type="global"
labels.event="connectivity"
labels.action="missed_pongs"

# Client reconnect reports with offline data
resource.type="global"
labels.event="connectivity"
labels.action="reconnect_report"
```

---

## Lab Operations

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Lab configuration started` | INFO | `event=lab`, `action=start`, `lab_value=<lab_name>` | User selected a lab from the Lab Menu and clicked "Start Lab". The `lab_value` label contains the lab configuration name (e.g., `YOURLAB`, `IP_Centric`, `reset`). This triggers `callConfigTopo.py` in the login container to apply the selected topology configuration. Track by `lab_value` to see which labs are most popular. |
| `Lab status queried` | INFO | `event=lab`, `action=status_check` | Lab status endpoint polled to check device readiness. The UI auto-polls this after starting a lab. High frequency is normal — the frontend polls until all devices show "Configured". |
| `Lab reset initiated` | INFO | `event=lab`, `action=reset` | User triggered a full lab reset via `resetVMs.py`. This reverts all device configurations to their default state. High reset rates per lab may indicate students are struggling or hitting bugs. |

---

## Exam Events

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Exam started` | INFO | `event=exam`, `action=start`, `duration_minutes=<N>` | Exam timer started. The `duration_minutes` label shows the allocated exam time (e.g., `120` for a 2-hour exam). This sets `EXAM_START_TIME` and `EXAM_END_TIME` globally and triggers a HubSpot CRM update. |
| `Exam submitted` | INFO | `event=exam`, `action=submit` | User clicked "Submit Exam". This triggers `upload_exam_unattended.py` in the login container which collects device configs, grades the exam, and uploads results to GCS. |
| `Exam instructions requested` | INFO | `event=exam`, `action=get_instructions` | Honorlock exam instructions fetched via the proctoring API. This is called when loading the exam authentication page. Failures here block exam start. |
| `User session ID requested` | INFO | `event=exam`, `action=create_session` | New Honorlock proctoring session created via API. Returns a session ID used for the rest of the exam. A `201` response means new session; `200` means existing session found. |
| `Exam begin requested` | INFO | `event=exam`, `action=begin` | Exam proctoring session activated via Honorlock API. A `409` response means the exam is already running (not an error). This is the point where proctoring actually starts. |
| `Exam end requested` | INFO | `event=exam`, `action=end` | Exam session ended via Honorlock API. This also triggers `upload_exam_unattended.py` for final grading and upload. Track time between `start` and `end` for actual exam duration. |

---

## API Endpoints

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Topology API requested` | INFO | `event=api`, `endpoint=topology` | `GET /td-api/topology` — Returns the interactive topology graph data (Cytoscape.js nodes and edges). Response is cached for 30 seconds. Each page load triggers this. Track request frequency to gauge interactive topology usage and cache efficiency. |
| `Devices API requested` | INFO | `event=api`, `endpoint=devices` | `GET /td-api/devices` — Returns all devices grouped by type (spines, leafs, hosts, etc.). Used by the terminal page to populate the device tree sidebar. Includes user-added nodes from the node builder. |
| `Device status check requested` | INFO | `event=api`, `endpoint=device_status` | `GET /td-api/device-status` — Checks reachability of all devices via eAPI (EOS devices) or ping (hosts/firewalls). Response cached for 30 seconds. The WebSocket keepalive triggers this every 30s. High error rates indicate network or device issues. |
| `Interface stats requested` | INFO | `event=api`, `endpoint=interface_stats`, `device=<name>` | `GET /td-api/interface-stats?device=Spine1&interface=Ethernet1` — Fetches interface counters (in/out octets, errors, discards) from a specific device via eAPI. The `device` label shows which device was queried (e.g., `Spine1`). Cached for 10 seconds. Auth failures indicate unconfigured management access on the device. |
| `Running config requested` | INFO | `event=api`, `endpoint=running_config`, `device=<name>` | `GET /td-api/running-config?device=Spine1` — Fetches the full running configuration from a specific EOS device via eAPI. The `device` label shows which device (e.g., `Spine1`). Used when users click a device in the topology to view its config. Track by device name to see which devices users inspect most. |

---

## Packet Capture

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Capture WebSocket opened` | INFO | `event=capture`, `action=ws_connect`, `client_id=<id>`, `user=<username>` | Browser established a WebSocket connection to the packet capture proxy. The `client_id` is a unique 8-char ID for this capture session. The `user` label identifies the authenticated user. This WebSocket proxies to the captureservice container running on the host network. |
| `Capture WebSocket closed` | INFO | `event=capture`, `action=ws_disconnect`, `client_id=<id>` | Packet capture WebSocket disconnected. The `client_id` matches the corresponding `ws_connect` event. Calculate capture session duration from the time between connect and disconnect. |
| `Packet capture started` | INFO | `event=capture`, `action=start` | User initiated a packet capture via the REST API (placeholder — actual captures use WebSocket). The capture runs tcpdump on OVS bridges connecting topology devices. |
| `Packet capture stopped` | INFO | `event=capture`, `action=stop` | User stopped the packet capture via REST API. |

---

## Network Impairments

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Network impairment configured` | INFO | `event=impairment`, `action=configure`, `bridge=<bridge_name>`, `latency=<ms>`, `loss=<percent>` | User applied network impairment to a specific OVS bridge. The `bridge` label contains the bridge name (e.g., `br-Spine1-Eth1-Leaf1-Eth3`). The `latency` label shows the delay value (e.g., `50ms`). The `loss` label shows packet loss percentage (e.g., `1%`). Track which bridges users impair most and typical latency/loss values. |
| `Network impairment cleared` | INFO | `event=impairment`, `action=clear`, `bridge=<bridge_name>` | User removed impairment from a specific bridge. The `bridge` label identifies which link was restored to normal. |
| `All network impairments cleared` | INFO | `event=impairment`, `action=clear_all` | User cleared all active network impairments at once. This resets all bridges to normal operation. |

---

## Node Builder

| Log Message | Level | Labels | Description |
|-------------|-------|--------|-------------|
| `Node builder GET: <path>` | INFO | `event=nodebuilder`, `method=GET`, `path=<api_path>` | Proxied GET request to the nodebuilder service (KVM/libvirt management). The `path` label shows the API path (e.g., `api/bridges` for listing OVS bridges, `api/nodes` for listing VMs, `api/status` for service health). Used to populate the "Add Node" wizard in the UI. |
| `Node builder POST: <path>` | INFO | `event=nodebuilder`, `method=POST`, `path=<api_path>`, `action=<action>`, `node_name=<name>` | Proxied POST request to create/modify/delete nodes. The `path` shows the API path (e.g., `api/nodes` for creating a VM). The `action` label shows the operation type (e.g., `create`, `delete`, `reboot`). The `node_name` label shows the device name being acted on (e.g., `Host5`, `Firewall1`). Track to measure dynamic topology modification usage. |

---

## Errors

All errors follow this pattern:

```
Level: ERROR
Message: "Error in <HandlerName>: <error_details>"
Labels: event=error, handler=<HandlerName>
```

### Error Reference

| Handler | Route | When errors occur | Impact |
|---------|-------|-------------------|--------|
| `get_metadata_extract` | (startup) | Failed to fetch GCP metadata for Honorlock credentials | Exam features unavailable |
| `ExamSubmittedRedirectHandler` | `/exam-submitted` | `exam-submitted.html` template missing | Student can't see exam confirmation |
| `ExamAlreadyRunningHandler` | `/exam-already-running` | `exam-already-running.html` template missing | Student sees 404 instead of error page |
| `ExamAuthenticationHandler` | `/exam-auth` | `honorlock-index.html` template missing | Exam proctoring page broken |
| `topoRequestHandler` | `/` | Error rendering main topology page | Student sees blank/error page |
| `topoDataHandler` | WebSocket `/ws` | Message processing or 30s keepalive failure | Live status updates stop |
| `getAPI` | (internal) | HTTP call to `atd-conftopo:50010` failed | Lab configuration/status unavailable |
| `getEventStatus` | (internal) | GCP Cloud Function state query failed (ValueError, ConnectionError) | Uptime/status data unavailable |
| `_get_topo_build_data` | (internal) | `topo_build.yml` read/parse error | Topology rendering may use stale data |
| `update_hubspot_handler` | (internal) | HubSpot API timeout or error | Exam CRM tracking fails (non-blocking) |
| `GetClientIdHandler` | `/td-api/honorlock/client-id` | GCP metadata fetch for Honorlock client ID failed | Exam proctoring unavailable |
| `GetExamInstructionsHandler` | `/td-api/honorlock/instructions` | Honorlock API error | Exam instructions not displayed |
| `GetUserSessionIdHandler` | `/td-api/honorlock/session` | Honorlock session creation API error | Can't start proctored exam |
| `ExamStatusHandler` | `/td-api/exam-status` | Error reading/updating exam state or HubSpot failure | Exam timer broken or CRM not updated |
| `ExamSubmitHandler` | `/td-api/exam-submit` | `upload_exam_unattended.py` execution failed | Exam results not uploaded |
| `ToolsHandler` | `/td-api/tools` | Invalid JSON or latency tool execution error | Network tools non-functional |
| `ViewConfigHandler` | `/td-api/view-config` | Invalid JSON or config read error | View config feature broken |
| `ExamRedoRedirectHandler` | `/exam-redo` | Template rendering error | Exam redo page broken |
| `BeginExamHandler` | `/td-api/honorlock/begin` | Honorlock API error starting exam | Proctoring won't start |
| `BaseUrlHandler` | `/td-api/base-url` | Base64 encoding error | Encoded credentials unavailable |
| `UptimeWithRuntimeHandler` | `/td-api/uptime-runtime` | `atd-uptime` service unreachable | Uptime display broken |
| `GetAccessInfoHandler` | `/td-api/get-access-info` | ACCESS_INFO.yaml read error | User details unavailable |
| `TopologyAPIHandler` | `/td-api/topology` | Topology layout computation error | Interactive topology won't render |
| `DevicesAPIHandler` | `/td-api/devices` | `topo_build.yml` missing, YAML parse error, or unexpected error | Device list empty in terminal page |
| `DeviceTypesAPIHandler` | `/td-api/device-types` | Device type metadata export error | Device type filtering broken |
| `RunningConfigAPIHandler` | `/td-api/running-config` | eAPI connection failed or auth error on target device | Can't view device config |
| `EndExamHandler` | `/td-api/honorlock/end` | Honorlock API error or upload script failure | Exam end not recorded properly |
| `CaptureBridgesAPIHandler` | `/td-api/capture/bridges` | Capture service unreachable | Bridge list empty in capture UI |
| `LatencyBridgesAPIHandler` | `/td-api/latency/bridges` | Capture service unreachable | Latency bridge list empty |
| `LatencyEnableAPIHandler` | `/td-api/latency/enable` | Capture service unreachable | Can't enable latency |
| `LatencyDisableAPIHandler` | `/td-api/latency/disable` | Capture service unreachable | Can't disable latency |
| `LatencyDisableAllAPIHandler` | `/td-api/latency/disable-all` | Capture service unreachable | Can't clear all latency |
| `ImpairmentsBridgesAPIHandler` | `/td-api/impairments/bridges` | Capture service unreachable | Impairment bridge list empty |
| `ImpairmentsConfigureAPIHandler` | `/td-api/impairments/configure` | Invalid JSON body or capture service unreachable | Can't apply impairment |
| `ImpairmentsClearAPIHandler` | `/td-api/impairments/clear` | Invalid JSON body or capture service unreachable | Can't clear impairment |
| `ImpairmentsClearAllAPIHandler` | `/td-api/impairments/clear-all` | Capture service unreachable | Can't clear all impairments |
| `NodeBuilderProxyHandler` | `/nodebuilder/*` | Nodebuilder service unreachable (both primary and fallback URLs) | Can't add/remove/reboot nodes |

---

## GCP Log Explorer Queries

### All uilanding logs
```
logName="projects/<PROJECT>/logs/uilanding"
```

### Page views — which pages are users visiting?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="page_view"
```

### Login failures — potential credential issues
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="auth"
jsonPayload.labels.action="login_failure"
```

### All exam lifecycle events
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="exam"
```

### Labs started — which labs are popular?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="lab"
jsonPayload.labels.action="start"
```

### Lab resets — which labs cause frustration?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="lab"
jsonPayload.labels.action="reset"
```

### Packet capture usage
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="capture"
```

### Network impairment usage with bridge details
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="impairment"
```

### Node builder operations — who's adding/removing nodes?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="nodebuilder"
jsonPayload.labels.method="POST"
```

### Running config views — which devices do users inspect?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.endpoint="running_config"
```

### Interface stats — which devices/interfaces are monitored?
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.endpoint="interface_stats"
```

### All errors
```
logName="projects/<PROJECT>/logs/uilanding"
severity="ERROR"
```

### Errors by handler
```
logName="projects/<PROJECT>/logs/uilanding"
jsonPayload.labels.event="error"
jsonPayload.labels.handler="TopologyAPIHandler"
```

### Specific lab instance (cross-reference with other containers)
```
logName="projects/<PROJECT>/logs/uilanding"
labels.lab_hostname="<LAB_HOSTNAME>"
```

Replace `<PROJECT>` with your GCP project ID (e.g., `atd-testdrivetraining-dev`).

---

## Architecture Notes

- **Logging library:** `cloud_logging_utils.py` (shared across all ATD containers)
- **Transport:** Synchronous (`SyncTransport`) — logs sent immediately, no buffering
- **Fallback:** If GCP Cloud Logging SDK is unavailable, all logs fall back to stdout via Python's `logging` module
- **Safety:** Every log call goes through `safe_log()` which wraps in `try/except Exception: pass` — logging errors never crash the application
- **No sensitive data:** Passwords, auth tokens, cookies, and API keys are never logged. Only usernames are logged for auth events.
- **Request body parsing:** For impairment and node builder POST endpoints, the request body is parsed inside a separate try/except to extract bridge names, latency values, and node names without risking a crash if the body is malformed.

# ... existing topology settings ...
host_cpu: 8
cvp_cpu: 4
cvp_nodes: 1
veos_cpu: 2
server_cpu: 2    # New: CPUs for server nodes
server_ram: 4096 # New: RAM for server nodes (4GB)

nodes:
  # ... existing vEOS/CloudEOS nodes ...
  server1:
    ip_addr: 192.168.1.100/24 # NEW: IP address for the server
    platform: server        # NEW: Designate this as a server
    neighbors:
      - port: Ethernet1
        neighborDevice: Leaf1
        neighborPort: Ethernet49
  server2:
    ip_addr: 192.168.1.101/24
    platform: server
    neighbors:
      - port: Ethernet1
        neighborDevice: Leaf2
        neighborPort: Ethernet50
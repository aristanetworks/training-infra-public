"""
Packet Parser for ATL Platform

Parses tcpdump output into structured JSON for the web UI.
Supports basic protocols: Ethernet, IPv4/IPv6, TCP, UDP, ICMP, ARP.
Also decodes VXLAN-encapsulated traffic for EVPN labs.
"""

import re
from typing import Dict, Optional
from datetime import datetime


class PacketParser:
    """
    Parse tcpdump verbose output into structured packet data.

    Expected tcpdump format (with -tttt -e -nn -v):
    2025-01-15 10:30:45.123456 00:1c:73:00:00:01 > 00:1c:73:00:00:02, ethertype IPv4 (0x0800), length 98: 192.168.1.1.443 > 192.168.1.2.52341: Flags [.], ack 1, win 65535, length 0
    """

    # Regex patterns for parsing tcpdump output
    TIMESTAMP_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+'
    )

    # Ethernet header: src > dst, ethertype TYPE (0xNNNN), length N:
    ETHERNET_PATTERN = re.compile(
        r'([0-9a-f:]+)\s+>\s+([0-9a-f:]+),\s+ethertype\s+(\w+)\s+\(0x([0-9a-f]+)\),\s+length\s+(\d+):'
    )

    # IPv4: src.port > dst.port: or src > dst:
    IPV4_PATTERN = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?\s+>\s+(\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?:'
    )

    # IPv6: src.port > dst.port: or src > dst:
    IPV6_PATTERN = re.compile(
        r'([0-9a-f:]+)(?:\.(\d+))?\s+>\s+([0-9a-f:]+)(?:\.(\d+))?:'
    )

    # TCP Flags
    TCP_FLAGS_PATTERN = re.compile(r'Flags\s+\[([^\]]+)\]')

    # ARP: who-has X tell Y / reply X is-at MAC
    ARP_WHO_HAS_PATTERN = re.compile(r'who-has\s+([\d.]+)\s+tell\s+([\d.]+)')
    ARP_REPLY_PATTERN = re.compile(r'reply\s+([\d.]+)\s+is-at\s+([0-9a-f:]+)')

    # ICMP: echo request/reply
    ICMP_PATTERN = re.compile(r'ICMP\s+(\w+)')

    # VXLAN: UDP port 4789, vni NNNN
    VXLAN_PATTERN = re.compile(r'VXLAN.*vni\s+(\d+)', re.IGNORECASE)
    VXLAN_PORT = 4789

    # Protocol name mapping
    ETHERTYPE_MAP = {
        '0800': 'IPv4',
        '0806': 'ARP',
        '86dd': 'IPv6',
        '8100': 'VLAN',
        '88cc': 'LLDP',
        '8847': 'MPLS',
        '8848': 'MPLS',
    }

    def __init__(self):
        self.packet_count = 0

    def parse_line(self, line: str, packet_number: int = 0) -> Optional[Dict]:
        """
        Parse a single tcpdump output line into structured data.

        Args:
            line: Raw tcpdump output line
            packet_number: Sequence number for this packet

        Returns:
            Dict with parsed packet fields, or None if parse failed
        """
        if not line or line.startswith('tcpdump:') or line.startswith('listening'):
            return None

        packet = {
            'number': packet_number,
            'timestamp': '',
            'src_mac': '',
            'dst_mac': '',
            'ethertype': '',
            'ethertype_name': '',
            'length': 0,
            'src_ip': '',
            'dst_ip': '',
            'src_port': None,
            'dst_port': None,
            'protocol': '',
            'info': '',
            'raw': line,
            # VXLAN fields (if applicable)
            'is_vxlan': False,
            'vxlan_vni': None,
            'inner_src_mac': '',
            'inner_dst_mac': '',
            'inner_src_ip': '',
            'inner_dst_ip': '',
            'inner_protocol': '',
        }

        try:
            # Parse timestamp
            ts_match = self.TIMESTAMP_PATTERN.match(line)
            if ts_match:
                packet['timestamp'] = ts_match.group(1)
                line = line[ts_match.end():]

            # Parse ethernet header
            eth_match = self.ETHERNET_PATTERN.search(line)
            if eth_match:
                packet['src_mac'] = eth_match.group(1)
                packet['dst_mac'] = eth_match.group(2)
                packet['ethertype_name'] = eth_match.group(3)
                packet['ethertype'] = eth_match.group(4)
                packet['length'] = int(eth_match.group(5))

            # Determine protocol from ethertype
            ethertype = packet['ethertype'].lower()
            if ethertype in self.ETHERTYPE_MAP:
                packet['protocol'] = self.ETHERTYPE_MAP[ethertype]
            else:
                packet['protocol'] = packet['ethertype_name'] or 'Unknown'

            # Parse based on protocol
            if packet['protocol'] == 'ARP':
                self._parse_arp(line, packet)
            elif packet['protocol'] in ('IPv4', 'IPv6'):
                self._parse_ip(line, packet)
            elif packet['protocol'] == 'LLDP':
                packet['info'] = 'LLDP Advertisement'

            # Check for VXLAN encapsulation
            if packet.get('dst_port') == self.VXLAN_PORT:
                self._parse_vxlan(line, packet)

        except Exception as e:
            # On parse error, still return basic info
            packet['info'] = f'Parse error: {str(e)}'

        return packet

    def _parse_arp(self, line: str, packet: Dict):
        """Parse ARP-specific fields."""
        packet['protocol'] = 'ARP'

        who_has = self.ARP_WHO_HAS_PATTERN.search(line)
        if who_has:
            packet['dst_ip'] = who_has.group(1)
            packet['src_ip'] = who_has.group(2)
            packet['info'] = f'Who has {who_has.group(1)}? Tell {who_has.group(2)}'
            return

        reply = self.ARP_REPLY_PATTERN.search(line)
        if reply:
            packet['src_ip'] = reply.group(1)
            packet['info'] = f'{reply.group(1)} is at {reply.group(2)}'
            return

        packet['info'] = 'ARP'

    def _parse_ip(self, line: str, packet: Dict):
        """Parse IP-specific fields (IPv4 or IPv6)."""
        # Try IPv4 first
        ip_match = self.IPV4_PATTERN.search(line)
        if ip_match:
            packet['src_ip'] = ip_match.group(1)
            packet['src_port'] = int(ip_match.group(2)) if ip_match.group(2) else None
            packet['dst_ip'] = ip_match.group(3)
            packet['dst_port'] = int(ip_match.group(4)) if ip_match.group(4) else None
        else:
            # Try IPv6
            ip_match = self.IPV6_PATTERN.search(line)
            if ip_match:
                packet['src_ip'] = ip_match.group(1)
                packet['src_port'] = int(ip_match.group(2)) if ip_match.group(2) else None
                packet['dst_ip'] = ip_match.group(3)
                packet['dst_port'] = int(ip_match.group(4)) if ip_match.group(4) else None

        # Determine transport protocol
        if packet['src_port'] or packet['dst_port']:
            # Check for well-known ports
            if 'Flags [' in line:
                packet['protocol'] = 'TCP'
                self._parse_tcp(line, packet)
            else:
                packet['protocol'] = 'UDP'
                self._parse_udp(line, packet)
        elif 'ICMP' in line:
            packet['protocol'] = 'ICMP'
            self._parse_icmp(line, packet)
        else:
            # Plain IP packet
            if packet['src_ip'] and packet['dst_ip']:
                packet['info'] = f'{packet["src_ip"]} > {packet["dst_ip"]}'

    def _parse_tcp(self, line: str, packet: Dict):
        """Parse TCP-specific fields."""
        info_parts = []

        # Parse TCP flags
        flags_match = self.TCP_FLAGS_PATTERN.search(line)
        if flags_match:
            flags = flags_match.group(1)
            info_parts.append(f'[{flags}]')

            # Extract sequence/ack numbers if present
            if 'seq' in line.lower():
                seq_match = re.search(r'seq\s+(\d+)', line)
                if seq_match:
                    info_parts.append(f'Seq={seq_match.group(1)}')

            if 'ack' in line.lower():
                ack_match = re.search(r'ack\s+(\d+)', line)
                if ack_match:
                    info_parts.append(f'Ack={ack_match.group(1)}')

            if 'win' in line.lower():
                win_match = re.search(r'win\s+(\d+)', line)
                if win_match:
                    info_parts.append(f'Win={win_match.group(1)}')

        # Add port info
        if packet['src_port'] and packet['dst_port']:
            port_info = f"{packet['src_port']} > {packet['dst_port']}"
            info_parts.insert(0, port_info)

        packet['info'] = ' '.join(info_parts)

    def _parse_udp(self, line: str, packet: Dict):
        """Parse UDP-specific fields."""
        info_parts = []

        # Add port info
        if packet['src_port'] and packet['dst_port']:
            info_parts.append(f"{packet['src_port']} > {packet['dst_port']}")

        # Check for known UDP protocols
        if packet['dst_port'] == 53 or packet['src_port'] == 53:
            packet['protocol'] = 'DNS'
            # Try to extract query/response info
            if 'A?' in line:
                query_match = re.search(r'A\?\s+(\S+)', line)
                if query_match:
                    info_parts.append(f'Query: {query_match.group(1)}')
        elif packet['dst_port'] == 67 or packet['dst_port'] == 68:
            packet['protocol'] = 'DHCP'
        elif packet['dst_port'] == 123:
            packet['protocol'] = 'NTP'
        elif packet['dst_port'] == 514:
            packet['protocol'] = 'Syslog'
        elif packet['dst_port'] == self.VXLAN_PORT:
            packet['protocol'] = 'VXLAN'
            info_parts.append('VXLAN')

        packet['info'] = ' '.join(info_parts) if info_parts else 'UDP'

    def _parse_icmp(self, line: str, packet: Dict):
        """Parse ICMP-specific fields."""
        icmp_match = self.ICMP_PATTERN.search(line)
        if icmp_match:
            icmp_type = icmp_match.group(1)
            packet['info'] = f'ICMP {icmp_type}'

            # Extract echo request/reply details
            if 'echo' in line.lower():
                id_match = re.search(r'id\s+(\d+)', line)
                seq_match = re.search(r'seq\s+(\d+)', line)
                details = []
                if id_match:
                    details.append(f'id={id_match.group(1)}')
                if seq_match:
                    details.append(f'seq={seq_match.group(1)}')
                if details:
                    packet['info'] += f' ({", ".join(details)})'
        else:
            packet['info'] = 'ICMP'

    def _parse_vxlan(self, line: str, packet: Dict):
        """Parse VXLAN encapsulated traffic."""
        packet['is_vxlan'] = True
        packet['protocol'] = 'VXLAN'

        # Extract VNI
        vni_match = self.VXLAN_PATTERN.search(line)
        if vni_match:
            packet['vxlan_vni'] = int(vni_match.group(1))

        # Try to parse inner frame
        # Inner frame appears after "vni NNNN:" in the output
        vni_pos = line.lower().find('vni')
        if vni_pos > 0:
            inner_part = line[vni_pos:]
            colon_pos = inner_part.find(':')
            if colon_pos > 0:
                inner_frame = inner_part[colon_pos + 1:].strip()

                # Parse inner ethernet
                inner_eth = self.ETHERNET_PATTERN.search(inner_frame)
                if inner_eth:
                    packet['inner_src_mac'] = inner_eth.group(1)
                    packet['inner_dst_mac'] = inner_eth.group(2)

                # Parse inner IP
                inner_ip = self.IPV4_PATTERN.search(inner_frame)
                if inner_ip:
                    packet['inner_src_ip'] = inner_ip.group(1)
                    packet['inner_dst_ip'] = inner_ip.group(3)

                    # Determine inner protocol
                    if 'Flags [' in inner_frame:
                        packet['inner_protocol'] = 'TCP'
                    elif inner_ip.group(2):  # Has port
                        packet['inner_protocol'] = 'UDP'
                    else:
                        packet['inner_protocol'] = 'IP'

        # Build info string
        info_parts = [f'VNI={packet["vxlan_vni"]}' if packet['vxlan_vni'] else 'VXLAN']
        if packet['inner_src_ip'] and packet['inner_dst_ip']:
            info_parts.append(f'Inner: {packet["inner_src_ip"]} > {packet["inner_dst_ip"]}')
            if packet['inner_protocol']:
                info_parts.append(f'({packet["inner_protocol"]})')

        packet['info'] = ' '.join(info_parts)

    def format_packet_summary(self, packet: Dict) -> str:
        """
        Format a packet as a one-line summary (Wireshark-style).

        Example: "1  0.000000  192.168.1.1  192.168.1.2  TCP  [SYN] Seq=0"
        """
        parts = [
            str(packet.get('number', 0)).rjust(5),
            packet.get('timestamp', '')[-15:],  # Just time portion
            packet.get('src_ip', packet.get('src_mac', '')).ljust(15),
            packet.get('dst_ip', packet.get('dst_mac', '')).ljust(15),
            packet.get('protocol', 'Unknown').ljust(8),
            packet.get('info', '')[:50]
        ]
        return '  '.join(parts)

"""Local interface discovery without hostname DNS or Internet requests."""
import ipaddress
import os
import socket
import struct


def interface_addresses():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Row(ctypes.Structure):
            _fields_ = [(name, wintypes.DWORD) for name in ("addr", "index", "mask", "broadcast", "reassembly")] + [("unused", wintypes.WORD), ("kind", wintypes.WORD)]
        size = wintypes.ULONG(0)
        get_table = ctypes.windll.iphlpapi.GetIpAddrTable
        result = get_table(None, ctypes.byref(size), False)
        if result != 122 or size.value > 1024 * 1024:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if get_table(buffer, ctypes.byref(size), False) != 0:
            return []
        count = wintypes.DWORD.from_buffer(buffer).value
        offset = ctypes.sizeof(wintypes.DWORD)
        if offset + count * ctypes.sizeof(Row) > len(buffer):
            return []
        return [socket.inet_ntoa(struct.pack("=I", Row.from_buffer(buffer, offset+i*ctypes.sizeof(Row)).addr)) for i in range(count)]
    import fcntl
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            try:
                info = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))
                addresses.append(socket.inet_ntoa(info[20:24]))
            except OSError:
                continue
    return addresses


def discover_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.3)
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
            if not ipaddress.ip_address(address).is_loopback:
                return address
    except OSError:
        pass
    try:
        for address in interface_addresses():
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback and not ip.is_unspecified:
                return address
    except (OSError, ImportError, AttributeError, ValueError):
        pass
    return "127.0.0.1"

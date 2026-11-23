"""One spelling for each IP address, so equal addresses compare equal."""

import ipaddress

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def canonical_address(address: IPAddress) -> IPAddress:
    """An IPv4-mapped IPv6 address as IPv4 (::ffff:172.30.250.10 -> 172.30.250.10), and
    IPv6 without a zone (fe80::1%eth0 -> fe80::1).

    A dual-stack socket reports IPv4 peers in the mapped form, and the zone only says
    which interface a link-local address was seen on.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped
        if address.scope_id is not None:
            return ipaddress.IPv6Address(str(address).split("%", 1)[0])
    return address


def parse_address(value: str | None) -> IPAddress | None:
    """The canonical address in `value`, or None when it is missing or not an address."""
    if not value:
        return None
    try:
        return canonical_address(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None

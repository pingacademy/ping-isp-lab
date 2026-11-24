#!/usr/bin/env python3
"""Generate the Ping ISP course lab: Containerlab topology + FRR configs for every stage.

The network is defined once here (nodes, links, addressing). Each course stage adds
one layer of config on top of the previous one, so every episode has a known
"start" and "end" state.

Run:  python3 build_lab.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/frrouting/frr:10.2.1"

# Loopback last octet doubles as the router number (used for the IS-IS NET too).
NODES = {
    "P1": 1, "P2": 2, "P3": 3, "P4": 4,
    "PE1": 11, "PE2": 12, "PE3": 13, "PE4": 14,
    "RR": 100,
    "CE1": 101, "CE2": 102, "CE3": 103, "CE4": 104,
}
CORE = ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR"]

# Core links: (link id, a, b). Addressed 10.1.<id>.0/31, a = .0, b = .1
CORE_LINKS = [
    (1, "PE1", "P1"), (2, "PE1", "P3"), (3, "PE1", "PE2"), (4, "PE2", "P3"),
    (5, "P1", "P2"), (6, "P1", "P3"), (7, "P2", "P4"), (8, "P3", "P4"),
    (9, "P2", "PE3"), (10, "P4", "PE3"), (11, "PE3", "PE4"), (12, "P4", "PE4"),
    (13, "RR", "P3"), (14, "RR", "P4"),
]
# Customer attachment links (addressed later, in the L3VPN section).
CE_LINKS = [("CE1", "PE1"), ("CE2", "PE2"), ("CE3", "PE3"), ("CE4", "PE4")]


def loopback(node):
    return f"10.255.0.{NODES[node]}"


def assign_interfaces():
    """Return {node: [(ifname, peer, address_or_None, link_id_or_None)]} in eth order."""
    ifaces = {n: [] for n in NODES}
    for lid, a, b in CORE_LINKS:
        for node, peer, host in ((a, b, 0), (b, a, 1)):
            ifaces[node].append((f"eth{len(ifaces[node]) + 1}", peer, f"10.1.{lid}.{host}/31", lid))
    for ce, pe in CE_LINKS:
        for node, peer in ((ce, pe), (pe, ce)):
            ifaces[node].append((f"eth{len(ifaces[node]) + 1}", peer, None, None))
    return ifaces


IFACES = assign_interfaces()


def header(node):
    return [
        "frr version 10.2",
        "frr defaults traditional",
        f"hostname {node}",
        "log syslog informational",
        "service integrated-vtysh-config",
        "ip forwarding",
        "!",
    ]


def stage_00_base(node):
    """Start of E06: hostnames and interface descriptions only. No IP addresses."""
    lines = header(node)
    for ifname, peer, _, _ in IFACES[node]:
        lines += [f"interface {ifname}", f" description to-{peer}", "exit", "!"]
    return lines


def stage_06_addressing(node):
    """End of E06: loopbacks and /31 core links addressed. No routing protocol yet."""
    lines = header(node)
    if node in CORE:
        lines += ["interface lo", f" ip address {loopback(node)}/32", "exit", "!"]
    for ifname, peer, addr, _ in IFACES[node]:
        lines += [f"interface {ifname}", f" description to-{peer}"]
        if addr:
            lines.append(f" ip address {addr}")
        lines += ["exit", "!"]
    return lines


def isis_net(node):
    """49.0001.<loopback as 12 digits>.00 — e.g. 10.255.0.11 -> 49.0001.0102.5500.0011.00"""
    digits = "".join(f"{int(o):03d}" for o in loopback(node).split("."))
    return f"49.0001.{digits[0:4]}.{digits[4:8]}.{digits[8:12]}.00"


def stage_11_isis(node):
    """End of Section 2: IS-IS level-2 core, wide metrics, passive loopbacks."""
    if node not in CORE:
        return stage_06_addressing(node)
    lines = header(node)
    lines += ["interface lo", f" ip address {loopback(node)}/32",
              " ip router isis PING", " isis passive", "exit", "!"]
    for ifname, peer, addr, _ in IFACES[node]:
        lines += [f"interface {ifname}", f" description to-{peer}"]
        if addr:
            lines += [f" ip address {addr}", " ip router isis PING",
                      " isis network point-to-point"]
        lines += ["exit", "!"]
    lines += ["router isis PING", f" net {isis_net(node)}", " is-type level-2-only",
              " metric-style wide", " log-adjacency-changes", "exit", "!"]
    return lines


STAGES = {
    "00-base": stage_00_base,
    "06-addressing": stage_06_addressing,
    "11-isis": stage_11_isis,
}

DAEMONS = """# FRR daemons for the Ping ISP lab
zebra=yes
bgpd=yes
ospfd=no
ospf6d=no
ripd=no
ripngd=no
isisd=yes
pimd=no
ldpd=yes
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=yes
fabricd=no
vrrpd=no
pathd=no

vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
bgpd_options="   -A 127.0.0.1"
isisd_options="  -A 127.0.0.1"
ldpd_options="   -A 127.0.0.1"
bfdd_options="   -A 127.0.0.1"
staticd_options="-A 127.0.0.1"
"""


def topology():
    out = ["name: ping-isp", "", "topology:", "  defaults:", "    kind: linux",
           f"    image: {IMAGE}", "", "  nodes:"]
    for node in NODES:
        out += [f"    {node}:",
                 "      binds:",
                 "        - configs/daemons:/etc/frr/daemons",
                 f"        - configs/active/{node}.conf:/etc/frr/frr.conf",
                 "      exec:",
                 "        - sysctl -w net.mpls.platform_labels=1048575"]
        for ifname, _, _, _ in IFACES[node]:
            out.append(f"        - sysctl -w net.mpls.conf.{ifname}.input=1")
    out += ["", "  links:"]
    # Links in a stable order, with the interface each side got.
    seen = set()
    for node in NODES:
        for ifname, peer, _, _ in IFACES[node]:
            key = tuple(sorted((node, peer)))
            if key in seen:
                continue
            seen.add(key)
            peer_if = next(i for i, p, _, _ in IFACES[peer] if p == node)
            out.append(f'    - endpoints: ["{node}:{ifname}", "{peer}:{peer_if}"]')
    return "\n".join(out) + "\n"


def addressing_table():
    rows = ["| Link | A side | B side | Subnet |", "|---|---|---|---|"]
    for lid, a, b in CORE_LINKS:
        a_if = next(i for i, p, _, _ in IFACES[a] if p == b)
        b_if = next(i for i, p, _, _ in IFACES[b] if p == a)
        rows.append(f"| {lid} | {a} {a_if} — 10.1.{lid}.0 | {b} {b_if} — 10.1.{lid}.1 | 10.1.{lid}.0/31 |")
    rows += ["", "| Router | Loopback0 |", "|---|---|"]
    rows += [f"| {n} | {loopback(n)}/32 |" for n in CORE]
    rows += ["", "| CE link | CE side | PE side |", "|---|---|---|"]
    for ce, pe in CE_LINKS:
        ce_if = next(i for i, p, _, _ in IFACES[ce] if p == pe)
        pe_if = next(i for i, p, _, _ in IFACES[pe] if p == ce)
        rows.append(f"| {ce}–{pe} | {ce} {ce_if} | {pe} {pe_if} |")
    return "\n".join(rows) + "\n"


def main():
    cfg = os.path.join(HERE, "configs")
    for stage, fn in STAGES.items():
        d = os.path.join(cfg, "stages", stage)
        os.makedirs(d, exist_ok=True)
        for node in NODES:
            with open(os.path.join(d, f"{node}.conf"), "w") as f:
                f.write("\n".join(fn(node)) + "\n")
    os.makedirs(os.path.join(cfg, "active"), exist_ok=True)
    for node in NODES:  # active = start of the course
        with open(os.path.join(cfg, "active", f"{node}.conf"), "w") as f:
            f.write("\n".join(stage_00_base(node)) + "\n")
    with open(os.path.join(cfg, "daemons"), "w") as f:
        f.write(DAEMONS)
    with open(os.path.join(HERE, "ping-isp.clab.yml"), "w") as f:
        f.write(topology())
    with open(os.path.join(HERE, "ADDRESSING.md"), "w") as f:
        f.write("# Ping ISP — Addressing Plan\n\n_Generated by build_lab.py — do not edit by hand._\n\n")
        f.write(addressing_table())
    print("Stages:", ", ".join(STAGES))


if __name__ == "__main__":
    main()

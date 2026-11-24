#!/usr/bin/env bash
# End-to-end test of every lab stage on real FRR routers (run by GitHub Actions, or on any
# Linux box with Docker + Containerlab):   .github/test-lab.sh
set -euo pipefail
cd "$(dirname "$0")/.."

FAILS=0
pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; FAILS=$((FAILS + 1)); }
vt()   { sudo docker exec "clab-ping-isp-$1" vtysh -c "$2" 2>/dev/null; }
sh_in(){ local n=$1; shift; sudo docker exec "clab-ping-isp-$n" "$@" 2>/dev/null; }

# wait_until <seconds> <description> <command...>: retry until the command succeeds.
wait_until() {
  local secs=$1 desc=$2; shift 2
  for _ in $(seq 1 "$secs"); do
    if "$@" >/dev/null 2>&1; then pass "$desc"; return 0; fi
    sleep 1
  done
  fail "$desc (gave up after ${secs}s)"; return 0
}

load() {
  echo "── stage $1"
  ./stage.sh "$1" >/dev/null
  local running
  running=$(sudo docker ps --filter name=clab-ping-isp- --format '{{.Names}}' | wc -l)
  [ "$running" -eq 13 ] && pass "13 routers running" || fail "expected 13 routers, found $running"
}

# ---------- 00-base: hostnames + descriptions, no addresses
load 00-base
vt PE1 "show running-config" | grep -q "^hostname PE1" && pass "PE1 has its hostname" || fail "PE1 hostname"
vt P3 "show interface eth4" | grep -q "to-P4" && pass "P3 eth4 is described to-P4" || fail "P3 eth4 description"

# ---------- 06-addressing: loopbacks + /31 links, connected reachability only
load 06-addressing
wait_until 20 "PE1 pings P1 across link 1 (10.1.1.1)" sh_in PE1 ping -c1 -W1 10.1.1.1
wait_until 20 "P2 pings PE3 across link 9 (10.1.9.1)" sh_in P2 ping -c1 -W1 10.1.9.1
sh_in PE1 ping -c1 -W1 10.255.0.13 >/dev/null && fail "PE1 should NOT reach PE3's loopback yet (no IGP)" \
  || pass "PE1 can't reach PE3's loopback yet (no IGP), as E06 expects"

# ---------- 11-isis: IS-IS core, overload bit on the RR
load 11-isis
p3_up() { [ "$(vt P3 'show isis neighbor' | grep -c ' Up ')" -ge 5 ]; }
wait_until 90 "P3 has 5 IS-IS neighbours up (PE1, PE2, P1, P4, RR)" p3_up
wait_until 60 "PE1 pings PE3's loopback from its own loopback" sh_in PE1 ping -c1 -W1 -I 10.255.0.11 10.255.0.13
wait_until 60 "PE1 reaches the RR's loopback (RR stays reachable)" sh_in PE1 ping -c1 -W1 -I 10.255.0.11 10.255.0.100
rr_ol() { vt PE1 "show isis database" | grep -E "RR\.00-00" | grep -q "0/0/1"; }
wait_until 30 "RR's LSP carries the overload bit (ATT/P/OL = 0/0/1)" rr_ol

echo "  … failing the P3–P4 link (P3 eth4)"
sh_in P3 ip link set eth4 down
via_p1() { vt P3 "show ip route 10.255.0.4/32" | grep -q "via 10.1.6.0"; }
wait_until 60 "with P3–P4 down, P3 reaches P4 via P1 (10.1.6.0)" via_p1
vt P3 "show ip route 10.255.0.4/32" | grep -q "via 10.1.13.0" \
  && fail "P3 is routing to P4 THROUGH the RR (overload bit not respected)" \
  || pass "P3 does not route through the RR"
sh_in P3 ip link set eth4 up

# ---------- 16-bgp: iBGP to the RR over loopbacks, RR reflects, P routers stay BGP-free
load 16-bgp
established() {
  [ "$(vt RR 'show bgp ipv4 unicast summary json' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for p in d.get("peers",{}).values() if p.get("state")=="Established"))')" -eq 4 ]
}
wait_until 120 "RR has 4 established iBGP sessions (PE1–PE4)" established
pe1_has_pe3() { vt PE1 "show ip route 203.0.113.128/26" | grep -q 'Known via "bgp"'; }
wait_until 60 "PE1 learns PE3's prefix 203.0.113.128/26 via BGP (reflected by the RR)" pe1_has_pe3
vt PE1 "show bgp ipv4 unicast 203.0.113.128/26" | grep -q "10.255.0.13" \
  && pass "next hop is PE3's loopback 10.255.0.13" || fail "next hop should be 10.255.0.13"
vt PE1 "show bgp ipv4 unicast 203.0.113.128/26" | grep -q "Originator: 10.255.0.13" \
  && pass "route carries ORIGINATOR_ID 10.255.0.13 (reflected)" || fail "expected an ORIGINATOR_ID from reflection"
vt P1 "show ip route 203.0.113.128/26" | grep -q "Known via" \
  && fail "P1 should be BGP-free" || pass "P1 has no BGP routes (BGP-free core, E17)"

echo "── cleanup"
sudo containerlab destroy -t ping-isp.clab.yml --cleanup >/dev/null 2>&1 || true

echo
if [ "$FAILS" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "$FAILS CHECK(S) FAILED"; exit 1; fi

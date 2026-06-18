#!/usr/bin/env bash
# End-to-end peer/partition test for desmata, using podman.
#
# Two containers from the desmata-e2e image play peers on a partitioned network:
#   - peerA: on the internet AND an --internal network.
#   - peerB: on ONLY the internal network (reaches A, NOT the internet).
#
# Stages (each prints a STAGE marker so failures localize):
#   1. build image + partitioned network + start peers
#   2. the partition is real: B reaches A but not the internet
#   3. dsm runs in-container: `dsm check` passes in both
#   4. peer A bootstraps over the internet -> probe CID_A (+ its ipfs store path)
#   5. peer B's bootstrap FAILS offline (no ipfs, no internet) -> it needs a peer
#   6. B pulls ipfs's closure from A over ssh (`nix copy --from ssh://A`) — the
#      trusted-tools bootstrap transport, the chicken-and-egg breaker
#   7. B reproduces the probe hash with the A-sourced ipfs: CID_B == CID_A
set -uo pipefail

IMAGE=desmata-e2e
NET=desmata-net
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=""

say() { printf '\n=== STAGE %s ===\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
reset() {
  podman rm -f peerA peerB >/dev/null 2>&1 || true
  podman network rm "$NET" >/dev/null 2>&1 || true
}
cleanup() { reset; [ -n "$TMP" ] && rm -rf "$TMP"; }
trap cleanup EXIT
reset
TMP="$(mktemp -d)"

# --- 1. image + network + peers -------------------------------------------
# One normal (internet) network for both peers; A keeps full internet, and B is
# partitioned by deleting its default route -- it can still reach A on the local
# subnet but cannot route to the internet. (Cleaner than an --internal network,
# which pollutes A's DNS.)
say "1: build image + network + peers, then partition B"
podman image exists "$IMAGE" || podman build -t "$IMAGE" -f "$REPO_ROOT/e2e/Containerfile" "$REPO_ROOT"
podman network create "$NET" >/dev/null
podman run -d --name peerA --network "$NET" "$IMAGE" >/dev/null
podman run -d --name peerB --network "$NET" --cap-add NET_ADMIN "$IMAGE" >/dev/null
A_IP="$(podman inspect peerA -f '{{(index .NetworkSettings.Networks "'"$NET"'").IPAddress}}')"
[ -n "$A_IP" ] || fail "could not determine peerA IP"
echo "peerA IP: $A_IP"
podman exec peerB ip route del default || fail "could not partition B (NET_ADMIN?)"

# --- 2. the partition is real ---------------------------------------------
say "2: partition is real (A on the internet, B reaches A but not the internet)"
podman exec peerA curl -sS --connect-timeout 8 https://cache.nixos.org/ >/dev/null 2>&1 \
  && echo "A has internet: ok" || fail "A has no internet"
podman exec peerB sh -c "curl -sS --connect-timeout 3 http://$A_IP:1/ >/dev/null 2>&1; [ \$? -ne 6 ]" \
  && echo "B reaches A: ok" || fail "B cannot reach A"
if podman exec peerB curl -sS --connect-timeout 5 https://cache.nixos.org/ >/dev/null 2>&1; then
  fail "B reached the internet (partition not in effect)"
else
  echo "B cannot reach the internet: ok"
fi

# --- 3. dsm runs in-container ----------------------------------------------
say "3: dsm check in both peers"
podman exec peerA dsm check >/dev/null || fail "dsm check failed on A"
podman exec peerB dsm check >/dev/null || fail "dsm check failed on B"
echo "dsm check ok on both peers"

# --- 4. peer A bootstraps over the internet --------------------------------
say "4: peer A bootstrap (internet) -> CID_A"
A_OUT="$(podman exec peerA dsm bootstrap 2>&1)" || { echo "$A_OUT"; fail "A bootstrap failed"; }
# the probe CID specifically (the output also prints the cell hash, another Qm...)
CID_A="$(printf '%s\n' "$A_OUT" | grep 'probe' | grep -oE 'Qm[1-9A-HJ-NP-Za-km-z]{44}' | head -1)"
IPFS_ID="$(printf '%s\n' "$A_OUT" | sed -n 's/.*ipfs dependency *: *\([a-z0-9]*-[^ ]*\).*/\1/p' | head -1)"
IPFS_PATH="/nix/store/$IPFS_ID"
[ -n "$CID_A" ] || { echo "$A_OUT"; fail "no probe CID from A"; }
[ -n "$IPFS_ID" ] || { echo "$A_OUT"; fail "no ipfs store path from A"; }
echo "CID_A      = $CID_A"
echo "A ipfs path= $IPFS_PATH"

# --- 5. peer B cannot bootstrap offline ------------------------------------
say "5: peer B bootstrap should FAIL offline (no ipfs, no internet)"
if podman exec peerB dsm bootstrap >/dev/null 2>&1; then
  fail "B bootstrapped without a peer (unexpected)"
fi
echo "B bootstrap failed offline as expected (it needs a peer)"

# --- 6. set up ssh on A and pull ipfs's closure to B -----------------------
# A serves over ssh as an unprivileged `peer` user (nixpkgs' sshd denies root
# login; the privsep `sshd` user and an /etc/nsswitch.conf are also needed for
# the nix-built sshd inside a foreign-glibc container). `remote-program` gives
# the absolute nix-store so the non-login ssh session can find it.
say "6: B pulls ipfs from A over ssh (nix copy --from ssh://peer@A)"
ssh-keygen -t ed25519 -N '' -f "$TMP/id" -q
podman exec peerA sh -c '
  printf "passwd: files\ngroup: files\nhosts: files dns\n" > /etc/nsswitch.conf
  mkdir -p /etc/ssh /run/sshd /var/empty /home/peer/.ssh && chmod 700 /home/peer/.ssh
  grep -q "^peer:" /etc/passwd || printf "\npeer:x:1000:1000:peer:/home/peer:/bin/sh\n" >> /etc/passwd
  grep -q "^sshd:" /etc/passwd || printf "\nsshd:x:74:74:privsep:/var/empty:/sbin/nologin\n" >> /etc/passwd
  ssh-keygen -A >/dev/null 2>&1
  chmod -R a+rwX /nix/var/nix/db   # let the unprivileged peer serve the store
'
podman cp "$TMP/id.pub" peerA:/home/peer/.ssh/authorized_keys
podman exec peerA sh -c '
  chmod 600 /home/peer/.ssh/authorized_keys && chown -R 1000:1000 /home/peer
  printf "PubkeyAuthentication yes\nStrictModes no\nUsePAM no\n" > /etc/ssh/sshd_config.desmata
  $(command -v sshd) -f /etc/ssh/sshd_config.desmata
'
podman cp "$TMP/id" peerB:/root/id
podman exec peerB chmod 600 /root/id
NIXSTORE="$(podman exec peerA sh -c 'command -v nix-store')"
podman exec peerB sh -c '
  export NIX_SSHOPTS="-i /root/id -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  nix copy --from "ssh://peer@'"$A_IP"'?remote-program='"$NIXSTORE"'" --no-check-sigs '"$IPFS_PATH"'
' || fail "nix copy from A failed"
podman exec peerB test -e "$IPFS_PATH/bin/ipfs" || fail "ipfs not present in B after copy"
echo "B acquired ipfs from A (no internet): $IPFS_PATH"

# --- 7. B reproduces the probe hash with the A-sourced ipfs ----------------
say "7: B reproduces CID_A using the ipfs it got from A"
CID_B="$(podman exec peerB sh -c '
  export IPFS_PATH=/tmp/b-ipfs
  '"$IPFS_PATH"'/bin/ipfs init >/dev/null 2>&1
  printf "desmata" > /tmp/probe
  '"$IPFS_PATH"'/bin/ipfs add -rHQ --only-hash /tmp/probe
')"
echo "CID_B      = $CID_B"
[ "$CID_A" = "$CID_B" ] || fail "hash mismatch: A=$CID_A B=$CID_B"

echo
echo "PASS: peer B, partitioned from the internet, reproduced peer A's hash"
echo "      ($CID_B) using ipfs it received from A over the trusted tools."

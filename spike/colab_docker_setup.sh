#!/usr/bin/env bash
# Rootless Docker inside a Colab runtime, for spike steps 6-7 (no workstation needed).
#
# The Colab runtime is itself a container: no CAP_NET_ADMIN on the host netns, nested overlayfs
# unavailable, cgroupfs read-only, and its sandbox denies mounting procfs/sysfs/mqueue/cgroup even
# to root. Rootless Docker (RootlessKit + slirp4netns + fuse-overlayfs) handles the first three;
# colab_runc_wrapper.py handles the last by binding the VM's /proc and /sys into containers instead
# of mounting new ones (and drops the pid namespace, so the bound /proc stays consistent).
# Containers therefore see the VM's process list. Acceptable on a disposable VM; never use this
# wrapper anywhere else. Network namespaces, bridges and Docker's internal networks are unaffected,
# which is what step 6 measures.
#
# Usage (as root, inside the Colab VM, after uploading the repo to /content/mesocosm):
#   bash /content/mesocosm/spike/colab_docker_setup.sh
#   dock docker run --rm alpine echo ok                 # `dock` runs a command as the docker user
#   export DOCKER_HOST=unix:///run/user/$(id -u dock)/docker.sock   # for root-side tools
# The gateway reaches the host's vLLM at 10.0.2.2 (slirp4netns host address), not host-gateway:
#   mesocosm env compose examples/docker-world.yaml --vllm-host 10.0.2.2 --out /content/compose.yml
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq uidmap dbus-user-session fuse-overlayfs slirp4netns iptables curl ca-certificates >/dev/null
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh
docker --version; docker compose version
id dock 2>/dev/null || useradd -m -s /bin/bash dock
grep -q '^dock:' /etc/subuid || echo 'dock:100000:65536' >> /etc/subuid
grep -q '^dock:' /etc/subgid || echo 'dock:100000:65536' >> /etc/subgid
U=$(id -u dock)
mkdir -p /run/user/$U && chown dock:dock /run/user/$U && chmod 700 /run/user/$U
mkdir -p /dev/net; [ -e /dev/net/tun ] || mknod /dev/net/tun c 10 200; chmod 666 /dev/net/tun
# The Colab runtime masks parts of /proc (read-only /proc/sys, tmpfs over kcore etc.). libnetwork
# writes /proc/sys/net/* when it wires a container, and runc treats a /proc with locked
# submounts as unsafe, so the masks come off before the daemon starts (disposable VM only).
for m in $(awk '$5 ~ "^/proc/." {print $5}' /proc/self/mountinfo | sort -r); do umount "$m" && echo "unmasked $m"; done
# runc wrapper first on the daemon's PATH: containerd shims and BuildKit both look up `runc` there.
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p /home/dock/bin && install -m 755 "$HERE/colab_runc_wrapper.py" /home/dock/bin/runc && chown -R dock:dock /home/dock/bin
cat > /usr/local/bin/dock <<EOS
#!/usr/bin/env bash
exec su dock -c 'export XDG_RUNTIME_DIR=/run/user/$U HOME=/home/dock PATH=/home/dock/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin DOCKER_HOST=unix:///run/user/$U/docker.sock; exec "\$0" "\$@"' -- "\$@"
EOS
chmod +x /usr/local/bin/dock
dock env DOCKERD_ROOTLESS_ROOTLESSKIT_NET=slirp4netns DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns \
    DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=false \
    nohup dockerd-rootless.sh --storage-driver fuse-overlayfs > /content/dockerd.log 2>&1 &
for i in $(seq 1 30); do sleep 2; dock docker info >/dev/null 2>&1 && break; done
dock docker info 2>&1 | grep -E 'Server Version|Storage Driver|rootless'
dock docker run --rm alpine sh -c 'echo HELLO_FROM_CONTAINER; ip -4 addr show eth0 | grep inet'
echo COLAB_DOCKER_READY

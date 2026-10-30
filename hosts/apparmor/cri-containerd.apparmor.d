# Managed by infrastructure/hosts/apparmor.
# Derived from containerd v2.3.4-k3s1.36, contrib/apparmor/template.go.
# Copyright The Docker, Moby and containerd Authors. Apache-2.0.
# https://www.apache.org/licenses/LICENSE-2.0
# Temporary mitigation: https://github.com/containerd/containerd/issues/14485
# Review this pinned profile when updating K3s/containerd.
abi <abi/3.0>,
#include <tunables/global>

profile cri-containerd.apparmor.d flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>

  network,
  capability,
  file,
  umount,
  # Host and runtime processes may signal container processes.
  signal (receive) peer=unconfined,
  signal (receive) peer=runc,
  signal (receive) peer=crun,
  # The containerd daemon runs unconfined on these three nodes.
  signal (receive) peer=unconfined,
  signal (send,receive) peer=cri-containerd.apparmor.d,
  # Ubuntu can stack the unconfined label onto this same profile.
  signal (send,receive) peer=cri-containerd.apparmor.d//&unconfined,

  deny @{PROC}/* w,
  deny @{PROC}/{[^1-9/],[^1-9/][^0-9/],[^1-9s/][^0-9y/][^0-9s/],[^1-9/][^0-9/][^0-9/][^0-9/]*}/** w,
  deny @{PROC}/sys/[^k]** w,
  deny @{PROC}/sys/kernel/{?,??,[^s][^h][^m]**} w,
  deny @{PROC}/sysrq-trigger rwklx,
  deny @{PROC}/kcore rwklx,
  deny mount,
  deny /sys/[^f]*/** wklx,
  deny /sys/f[^s]*/** wklx,
  deny /sys/fs/[^c]*/** wklx,
  deny /sys/fs/c[^g]*/** wklx,
  deny /sys/fs/cg[^r]*/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/devices/virtual/powercap/** rwklx,
  deny /sys/kernel/security/** rwklx,

  ptrace (trace,tracedby,read,readby) peer=cri-containerd.apparmor.d,
  ptrace (trace,tracedby,read,readby) peer=cri-containerd.apparmor.d//&unconfined,
}

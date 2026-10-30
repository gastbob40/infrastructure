#!/usr/bin/env python3
"""Install the reviewed AppArmor mitigation on one K3s host over SSH."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import base64, datetime, hashlib, json, os, pathlib, re, shutil, subprocess, tempfile

def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()

assert os.geteuid() == 0, "Root privileges required"
expected = "v2.3.4-k3s1.36"
versions = re.findall(r"Version:\s+(\S+)", run("k3s", "ctr", "version"))
assert versions == [expected, expected], "Re-review the profile for this containerd version"
assert run("sysctl", "-n", "kernel.apparmor_restrict_unprivileged_unconfined") == "1"
assert run("systemctl", "is-active", "apparmor") == "active"
assert not shutil.which("rootlesskit"), "Review optional rootlesskit profile permissions"
for pid in run("pgrep", "-x", "containerd").split():
    assert pathlib.Path("/proc", pid, "attr/current").read_text().strip() == "unconfined"
for include in ("abi/3.0", "tunables/global", "abstractions/base"):
    assert pathlib.Path("/etc/apparmor.d", include).is_file()
services = [s for s in ("k3s.service", "k3s-agent.service")
            if run("systemctl", "show", "-p", "LoadState", "--value", s) == "loaded"]
assert len(services) == 1, "Expected exactly one K3s service"
service = services[0]
unit = "k3s-apparmor-profile.service"
assert run("systemctl", "is-active", service) == "active"
assert "cri-containerd.apparmor.d (enforce)" in pathlib.Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()
enabled_before = subprocess.run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0
files = {
    pathlib.Path("/etc/apparmor.d/cri-containerd.apparmor.d"): bundle["cri-containerd.apparmor.d"],
    pathlib.Path("/etc/systemd/system", unit): bundle[unit],
    pathlib.Path("/etc/systemd/system", service + ".d", "25-apparmor-profile.conf"): bundle["k3s-apparmor-profile.conf"],
}
for path, content in files.items():
    assert not path.is_symlink(), "Refusing to replace a symlink: " + str(path)
    if path.exists():
        assert "Managed by infrastructure/hosts/apparmor." in path.read_text(), "Existing unmanaged file: " + str(path)

# Compile without loading, then verify the systemd unit before touching persistent files.
with tempfile.TemporaryDirectory(prefix="k3s-apparmor-check-") as stage:
    candidate = pathlib.Path(stage, "cri-containerd.apparmor.d")
    candidate.write_text(bundle["cri-containerd.apparmor.d"])
    run("apparmor_parser", "-Q", "-K", str(candidate))
    candidate_unit = pathlib.Path(stage, unit)
    candidate_unit.write_text(bundle[unit])
    run("systemd-analyze", "verify", str(candidate_unit))

unchanged = all(p.exists() and p.read_text() == content for p, content in files.items())
backup = None
if not unchanged:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = pathlib.Path("/root/backups", "apparmor-stacked-peers-" + stamp)
    backup.mkdir(mode=0o700, parents=True)
    previous = {}
    for index, path in enumerate(files):
        if path.exists():
            saved = backup / (str(index) + "-" + path.name)
            shutil.copy2(path, saved)
            previous[str(path)] = str(saved)
        else:
            previous[str(path)] = None
    (backup / "previous-files.json").write_text(json.dumps(previous, indent=2) + "\n")
    original = "\n".join(line for line in bundle["cri-containerd.apparmor.d"].splitlines()
                         if "peer=cri-containerd.apparmor.d//&unconfined," not in line) + "\n"
    # Reconstructed pinned runtime default, with only the two workaround rules removed.
    (backup / "runtime-default-before.profile").write_text(original)
    (backup / "previous-unit-state.json").write_text(json.dumps({"enabled": enabled_before}) + "\n")

try:
    if not unchanged:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                os.chmod(tmp.name, 0o644)
            os.replace(tmp.name, path)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", unit)
    active = subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
    run("systemctl", "reload" if active else "start", unit)
    assert run("systemctl", "is-active", unit) == "active"
    assert run("systemctl", "is-active", service) == "active"
    assert "cri-containerd.apparmor.d (enforce)" in pathlib.Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()
    assert unit in run("systemctl", "show", "-p", "Requires", "--value", service).split()
    assert unit in run("systemctl", "show", "-p", "After", "--value", service).split()
except Exception:
    if backup:
        # Preserve an earlier managed policy on reapplication; otherwise restore
        # the pinned runtime default used before the first installation.
        prior_profile = previous["/etc/apparmor.d/cri-containerd.apparmor.d"]
        subprocess.run(["apparmor_parser", "-Kr", prior_profile or str(backup / "runtime-default-before.profile")], check=False)
        if not enabled_before:
            subprocess.run(["systemctl", "disable", unit], check=False)
        for destination, saved in previous.items():
            path = pathlib.Path(destination)
            if saved:
                shutil.copy2(saved, path)
            else:
                path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        # Do not stop the loader: it is a required dependency of the running K3s.
    raise

print(json.dumps({"host":run("hostname"), "service":service, "profile":"enforce",
                  "persistent_loader":"active", "backup":str(backup) if backup else None,
                  "sha256":hashlib.sha256(bundle["cri-containerd.apparmor.d"].encode()).hexdigest(),
                  "applied_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=("k8s-s2", "k8s-s1", "k8s"))
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    names = ("cri-containerd.apparmor.d", "k3s-apparmor-profile.service", "k3s-apparmor-profile.conf")
    payload = base64.b64encode(json.dumps({n: (directory / n).read_text() for n in names}).encode()).decode()
    code = "import base64,json\nbundle=json.loads(base64.b64decode(" + repr(payload) + "))\n" + REMOTE
    command = "python3 -" if args.host == "k8s" else "sudo -n python3 -"
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.host, command],
                   input=code, text=True, check=True)


if __name__ == "__main__":
    main()

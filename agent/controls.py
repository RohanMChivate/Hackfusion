"""Kill a named process, refusing anything that looks like part of the system."""
import psutil

# Windows processes the session depends on, even when they run as the logged-in user.
PROTECTED = {
    "system", "registry", "idle", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe",
    "explorer.exe", "sihost.exe", "ctfmon.exe", "taskhostw.exe", "runtimebroker.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
    "textinputhost.exe", "conhost.exe", "dllhost.exe", "audiodg.exe", "spoolsv.exe",
    "securityhealthsystray.exe", "msmpeng.exe", "lockapp.exe", "userinit.exe",
}
TERMINATE_WAIT = 3  # seconds before falling back to kill()


def _own_pids():
    """This agent, its launcher parent (venv python.exe) and its children (banner/lock/message windows)."""
    me = psutil.Process()
    pids = {me.pid}
    try:
        parent = me.parent()
        if parent is not None:
            pids.add(parent.pid)
    except psutil.Error:
        pass
    pids.update(c.pid for c in me.children(recursive=True))
    return pids


def kill_by_name(name):
    """Terminate every process with this name (case-insensitive, '.exe' optional) owned by the
    logged-in user. Returns {ok, killed, refused, detail}."""
    wanted = name.strip().lower()
    if not wanted:
        return {"ok": False, "killed": 0, "refused": 0, "detail": "no process name given"}
    names = {wanted, wanted if wanted.endswith(".exe") else wanted + ".exe"}
    if names & PROTECTED:
        return {"ok": False, "killed": 0, "refused": 0, "detail": f"{name} is a system process; refused"}

    user = psutil.Process().username()
    own = _own_pids()
    targets, refused = [], []
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() not in names:
            continue
        try:
            owner = p.username()
        except psutil.AccessDenied:
            owner = None  # only system/elevated processes hide their owner from a normal user
        except psutil.NoSuchProcess:
            continue
        if p.pid in own or p.pid <= 4 or owner != user:
            refused.append(p.pid)
        else:
            targets.append(p)

    for p in targets:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            refused.append(p.pid)
    gone, alive = psutil.wait_procs(targets, timeout=TERMINATE_WAIT)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    killed = len(targets) - len([p for p in targets if p.pid in refused])

    if not targets and not refused:
        detail = f"no process named {name}"
    elif not targets:
        detail = f"{name}: {len(refused)} process(es) belong to the system or this agent; refused"
    else:
        detail = f"killed {killed} {name} process(es)" + (f"; refused {len(refused)}" if refused else "")
    return {"ok": killed > 0, "killed": killed, "refused": len(refused), "detail": detail}

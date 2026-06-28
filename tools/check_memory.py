#!/usr/bin/env python3
# tools/check_memory.py
# Adapted from EFIO's check_memory.py - run this manually (or on a cron)
# on the target hardware to catch a slow memory leak in the daemon/API
# process before it OOMs in the field. Not wired into the watchdog -
# this is a diagnostic tool, not a runtime check.
#
# Usage: python3 tools/check_memory.py

import psutil


def bytes_to_mb(bytes_val):
    return round(bytes_val / (1024 * 1024), 2)


def check_flemingo_memory():
    print("=" * 60)
    print("Flemingo Application Memory Analysis")
    print("=" * 60)

    mem = psutil.virtual_memory()
    print("\nSystem Memory Overview:")
    print(f"   Total:     {bytes_to_mb(mem.total)} MB")
    print(f"   Used:      {bytes_to_mb(mem.used)} MB ({mem.percent}%)")
    print(f"   Available: {bytes_to_mb(mem.available)} MB")
    print(f"   Free:      {bytes_to_mb(mem.free)} MB")

    flemingo_processes = []
    other_python = []

    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
        try:
            pinfo = proc.info
            if not pinfo['name'] or 'python' not in pinfo['name'].lower():
                continue

            cmdline = ' '.join(pinfo['cmdline']) if pinfo['cmdline'] else ''
            mem_mb = bytes_to_mb(pinfo['memory_info'].rss)
            entry = {
                'pid': pinfo['pid'],
                'name': pinfo['name'],
                'cmd': cmdline[:80],
                'memory_mb': mem_mb,
            }

            # Matches `python3 api/app.py`, `flemingo/daemon`, etc. -
            # adjust this filter if you rename the entrypoint or run it
            # via a systemd ExecStart with a different invocation.
            if 'flemingo' in cmdline.lower() or 'app.py' in cmdline.lower():
                flemingo_processes.append(entry)
            else:
                other_python.append(entry)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    print("\nFlemingo Application Processes:")
    if flemingo_processes:
        total_mem = 0
        for proc in flemingo_processes:
            print(f"   PID {proc['pid']}: {proc['memory_mb']} MB")
            print(f"      {proc['cmd']}")
            total_mem += proc['memory_mb']
        print(f"\n   Total Flemingo Memory: {total_mem} MB")
        print(f"   Percentage of Total:   {round(total_mem / bytes_to_mb(mem.total) * 100, 2)}%")
    else:
        print("   No Flemingo processes found (is it running?)")

    if other_python:
        print("\nOther Python Processes:")
        for proc in other_python:
            print(f"   PID {proc['pid']}: {proc['memory_mb']} MB - {proc['cmd']}")

    print("\nTop 10 Memory Consumers:")
    all_procs = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            pinfo = proc.info
            all_procs.append({
                'pid': pinfo['pid'],
                'name': pinfo['name'],
                'memory_mb': bytes_to_mb(pinfo['memory_info'].rss),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    all_procs.sort(key=lambda x: x['memory_mb'], reverse=True)
    for i, proc in enumerate(all_procs[:10], 1):
        print(f"   {i}. {proc['name']:<20} - {proc['memory_mb']:>8} MB (PID {proc['pid']})")

    print("\nAnalysis:")
    if flemingo_processes:
        total_mem = sum(p['memory_mb'] for p in flemingo_processes)
        if total_mem < 100:
            print(f"   {total_mem} MB - normal for a Flask+SocketIO process on this hardware")
        elif total_mem < 200:
            print(f"   {total_mem} MB - slightly high but not alarming on its own")
        else:
            print(f"   {total_mem} MB - high. Worth a long soak test with this "
                  f"script on a timer to confirm whether it's still climbing "
                  f"(a real leak) or just settled at a higher baseline.")

    if mem.percent > 80:
        print(f"   Overall system memory at {mem.percent}% - tight on a Purple Pi OH2. Consider:")
        print("      - disabling unused services")
        print("      - not running a desktop environment on the unit")
        print("      - adding swap if this becomes a recurring pattern")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    check_flemingo_memory()

"""MTDX entry point.

  MTDX.exe                          window: pick the dump once, convert, play (gui.py)
  MTDX.exe convert [DUMP] [--workers N] [--root DIR]
                                    console conversion (no DUMP = the dump of the last conversion; after an update it makes
                                    only what changed)
  MTDX.exe status [--root DIR]      ready / refresh needed / not converted
  MTDX.exe check DUMP               checks a dump without converting it
  MTDX.exe play [--root DIR]        starts the game
  MTDX.exe update [--check] [--root DIR]
                                    looks for a new MTDX; without --check installs it: MTDX downloads it, closes, and the
                                    new files are copied in a few seconds later (then start MTDX.exe once: it makes what the
                                    new version needs from your dump)
Without --root the MTDX folder is the one holding the executable (frozen) or the current folder.
"""
import os
import subprocess
import sys

from . import __version__


def default_root():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def attach_console(root):
    """MTDX.exe is built windowed (no console for the window); the console commands print to the console they ran from,
    or - started without one - to mtdx/state/console.log."""
    if sys.stdout is not None:
        return
    import ctypes
    if ctypes.windll.kernel32.AttachConsole(-1):
        sys.stdout = open('CONOUT$', 'w', buffering=1, encoding='utf-8', errors='replace')
    else:
        d = os.path.join(root, 'mtdx', 'state')
        os.makedirs(d, exist_ok=True)
        sys.stdout = open(os.path.join(d, 'console.log'), 'a', buffering=1, encoding='utf-8', errors='replace')
    sys.stderr = sys.stdout


def play(root):
    """starts RTDX.exe from the MTDX folder, detached from this process"""
    exe = os.path.join(root, 'RTDX.exe')
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], cwd=root, creationflags=flags, close_fds=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    root = opt(args, '--root', default_root())
    workers = opt(args, '--workers')
    if not args:
        from . import gui
        return gui.main(root)
    attach_console(root)
    cmd = args.pop(0)
    from . import install
    p = install.Paths(root)
    if cmd in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    if cmd == 'convert':
        last = {}

        def progress(stage, done, total):
            k = (stage, done * 100 // max(total, 1))
            if last.get(stage) != k:
                last[stage] = k
                print(f'{stage}: {done}/{total}', flush=True)
        dump = args[0] if args else install.load_config(p).get('dump')
        if not dump:
            print('PROBLEM: no dump given and none converted before: MTDX.exe convert DUMP_FOLDER')
            return 2
        try:
            install.convert(p, dump, int(workers) if workers else None, log=lambda s: print(s, flush=True),
                            progress=progress)
        except (install.DumpError, install.ConvertError) as e:
            print(f'PROBLEM: {e}')
            return 1
        return 0
    if cmd == 'status':
        st = install.status(p)
        print(f'MTDX {__version__}: {st["state"]}' + (f' ({st["reason"]})' if st['reason'] else ''))
        cfg = install.load_config(p)
        if cfg.get('dump'):
            print(f'dump: {cfg["dump"]}')
        return 0 if st['state'] == 'ready' else 1
    if cmd == 'check':
        if not args:
            print('MTDX.exe check DUMP_FOLDER')
            return 2
        try:
            dump = install.find_dump(args[0])
        except install.DumpError as e:
            print(f'PROBLEM: {e}')
            return 1
        problems = install.verify_dump(dump, p.bundle_list())
        for x in problems:
            print(f'PROBLEM: {x}')
        if not problems:
            print(f'{dump}: a complete dump of Rescue Team DX version 1.0.2')
        return 1 if problems else 0
    if cmd == 'play':
        st = install.status(p)
        if st['state'] != 'ready':
            print(f'PROBLEM: the game data is not ready ({st["reason"]}): run MTDX.exe (or MTDX.exe convert) first')
            return 1
        play(root)
        return 0
    if cmd == 'update':
        from . import update
        only_check = '--check' in args
        try:
            r = update.check(p)
        except update.UpdateError as e:
            print(f'PROBLEM: updates could not be checked: {e}')
            return 1
        e = r['entry']
        if not e:
            print(f'MTDX {__version__} is up to date (build {update.current_build(p) or "unknown"})')
            return 0
        print(f'{update.summary(e)} is out (build {e["build"]}){": " + e["page"] if e.get("page") else ""}')
        if only_check:
            return 0
        shown = {}

        def progress(done, total):
            k = done * 10 // max(total, 1)
            if shown.get('k') != k:
                shown['k'] = k
                print(f'downloading: {done / 2 ** 20:.1f} / {total / 2 ** 20:.1f} MiB', flush=True)
        try:
            cmd_file = update.prepare_self_update(p, e, log=lambda s: print(s, flush=True), progress=progress,
                                                  restart=False)
        except update.UpdateError as x:
            print(f'PROBLEM: {x}')
            return 1
        update.run_self_update(cmd_file)
        print('MTDX closes now; the new files are copied in a few seconds. Then start MTDX.exe once: it makes what the new '
              'version needs from your dump.')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())

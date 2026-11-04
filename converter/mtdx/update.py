"""MTDX's own updates.

Every MTDX release on GitHub carries, next to MTDX-<version>-win64.zip, a small file mtdx-update.json (release/
update_feed.py) = {"format", "index": <JSON text>, "sig": <base64>}: the index is signed with the key whose public half is
PUBLIC_KEY, so only the holder of the private key can publish an update. The index = {"format", "mtdx": {"version",
"build" (= the release's mtdx/release.json "made"), "notes", "page", "asset": {"name", "url", "size", "sha256"}}}.
MTDX reads the feed of the LATEST release (FEED; the MTDX_UPDATE_FEED environment variable or release.json "update_feed"
override it) and compares that build with its own release.json "made".

An update: the zip is downloaded into mtdx/state/update (resumed after a stop, its SHA-256 checked) and unpacked there; a
small command (apply_update.cmd) waits until MTDX has closed, copies the new release over the MTDX folder - like unzipping
it over the folder by hand: the converted game data, saves and settings are not part of a release, so they stay - and
starts MTDX again, which then makes the files the new version needs from the dump (install.status 'refresh').
"""
import base64
import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

from . import __version__, ed25519, install

FORMAT = 1
REPO = 'u2-bot/MTDX'
FEED = f'https://github.com/{REPO}/releases/latest/download/mtdx-update.json'
PUBLIC_KEY = bytes.fromhex('c37f5040cd80427924436fa89b68f9f1ec544141f468d7353c259b9b9140e77d')
USER_AGENT = f'MTDX/{__version__}'
CHUNK = 1 << 20


class UpdateError(RuntimeError):
    pass


def describe(e):
    """an exception -> one text for the window / console"""
    return str(e) if isinstance(e, UpdateError) else f'{type(e).__name__}: {e}'


def feed_url(p):
    return os.environ.get('MTDX_UPDATE_FEED') or install.release_info(p).get('update_feed') or FEED


def enabled(p):
    """automatic updates: on unless config.json says "update_auto": false (the window's tick)"""
    return bool(install.load_config(p).get('update_auto', True))


def set_enabled(p, on):
    cfg = install.load_config(p)
    cfg['update_auto'] = bool(on)
    install.save_config(p, cfg)


def can_self_update():
    """only the built MTDX.exe replaces itself; from the source tree it is updated with git"""
    return bool(getattr(sys, 'frozen', False))


def size_text(n):
    return f'{n / 2 ** 20:.1f} MiB' if n >= 1 << 20 else f'{max(1, round(n / 1024))} KiB'


def _dir(p, *sub):
    d = os.path.join(p.state, 'update', *sub)
    os.makedirs(d, exist_ok=True)
    return d


# --- HTTP -------------------------------------------------------------------------------------------------------------

def _open(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=dict({'User-Agent': USER_AGENT}, **(headers or {})))
    return urllib.request.urlopen(req, timeout=timeout)


def get_bytes(url, limit=1 << 20):
    try:
        with _open(url, timeout=20) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f'no update information at {url} (HTTP 404)') from None
        raise UpdateError(f'{url}: HTTP {e.code}') from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f'no connection to {url.split("/")[2] if "//" in url else url} '
                          f'({getattr(e, "reason", e)})') from None
    if len(data) > limit:
        raise UpdateError(f'{url}: larger than expected')
    return data


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for buf in iter(lambda: f.read(CHUNK), b''):
            h.update(buf)
    return h.hexdigest()


def download(url, dest, size, sha256, progress=None, stop=None):
    """-> dest, a file of `size` bytes whose SHA-256 is `sha256`. Resumes a .part left by an earlier try (HTTP Range)."""
    if os.path.isfile(dest) and os.path.getsize(dest) == size and _sha256(dest) == sha256:
        return dest
    part = dest + '.part'
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    tries = 5
    for attempt in range(tries):
        try:
            headers = {'Range': f'bytes={have}-'} if have else {}
            with _open(url, headers, timeout=60) as r:
                if have and r.status != 206:        # the server sent the whole file: start over
                    have = 0
                with open(part, 'ab' if have else 'wb') as f:
                    while True:
                        if stop and stop():
                            raise UpdateError('stopped')
                        buf = r.read(CHUNK)
                        if not buf:
                            break
                        f.write(buf)
                        have += len(buf)
                        if progress:
                            progress(have, size)
                        if have > size:
                            break
        except UpdateError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:              # the .part is already whole (or the range is wrong): check it below
                break
            if attempt == tries - 1 or e.code in (403, 404):
                raise UpdateError(f'download failed: HTTP {e.code} ({url})') from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            if attempt == tries - 1:
                raise UpdateError(f'download failed: {getattr(e, "reason", e)}') from None
        # a connection that ended early (an error, or a short read without one) goes on from where it stopped
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have >= size:
            break
        time.sleep(min(2 * (attempt + 1), 6))
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if have != size:
        if have > size:
            os.remove(part)
        raise UpdateError(f'download incomplete: {have:,} of {size:,} bytes - it goes on at the next try')
    if _sha256(part) != sha256:
        os.remove(part)
        raise UpdateError('download damaged (its SHA-256 differs from the update information) - it will be fetched again')
    os.replace(part, dest)
    return dest


# --- the feed ---------------------------------------------------------------------------------------------------------

def parse_feed(raw, key=None):
    """mtdx-update.json bytes -> the index (dict), only when its signature is good"""
    try:
        env = json.loads(raw.decode('utf-8'))
        text, sig = env['index'], base64.b64decode(env['sig'])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise UpdateError('the update information cannot be read') from None
    if not ed25519.verify(key or PUBLIC_KEY, text.encode('utf-8'), sig):
        raise UpdateError('the update information is not signed by the MTDX key - nothing is downloaded')
    index = json.loads(text)
    if index.get('format') != FORMAT:
        raise UpdateError(f'the update information has format {index.get("format")}; this MTDX reads {FORMAT} - '
                          f'download the new release by hand')
    m = index.get('mtdx') or {}
    a = m.get('asset') or {}
    if not (m.get('build') and a.get('url') and isinstance(a.get('size'), int) and len(a.get('sha256', '')) == 64):
        raise UpdateError('the update information is incomplete')
    return index


def current_build(p):
    return install.release_info(p).get('made', '')


def check(p):
    """-> {'entry': the feed's MTDX entry when its build is newer than this folder's, else None, 'index': the index}"""
    index = parse_feed(get_bytes(feed_url(p)))
    m = index['mtdx']
    newer = m['build'] > current_build(p)           # 'YYYY-MM-DD HH:MM:SS' compares as text
    return {'entry': m if newer else None, 'index': index}


def summary(entry):
    return f'MTDX {entry.get("version", "")} ({size_text(entry["asset"]["size"])})'


# --- installing -------------------------------------------------------------------------------------------------------

def game_running(p):
    """True while RTDX.exe of this folder runs: Windows refuses to open a running program for writing"""
    exe = os.path.join(p.root, 'RTDX.exe')
    if not os.path.isfile(exe):
        return False
    try:
        with open(exe, 'r+b'):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _processes():
    """-> [(pid, parent pid, exe name)] of the running processes (Toolhelp snapshot)"""
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32

    class PE(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                    ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k.CreateToolhelp32Snapshot(2, 0)        # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    out = []
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((e.th32ProcessID, e.th32ParentProcessID, e.szExeFile))
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


def _image(pid):
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ''
    finally:
        k.CloseHandle(h)


def other_instances(p):
    """-> pids of other MTDX.exe processes running from this folder (a second window, a console command); this process
    and its own helper processes do not count. They would keep MTDX's files in use while the update copies them."""
    try:
        me = os.getpid()
        exe = os.path.normcase(os.path.join(p.root, 'MTDX.exe'))
        return [pid for pid, ppid, name in _processes() if name.lower() == 'mtdx.exe' and pid != me and ppid != me
                and os.path.normcase(_image(pid)) == exe]
    except (OSError, AttributeError, ValueError):
        return []


def prepare_self_update(p, entry, log=print, progress=None, stop=None, restart=True, frozen_only=True):
    """Download + unpack the new MTDX next to this one; -> the command that copies it in after this process ends
    (restart = it starts MTDX.exe again at the end)."""
    if frozen_only and not can_self_update():
        raise UpdateError('MTDX runs from its source here: update it with git, not online')
    if game_running(p):
        raise UpdateError('the game (RTDX.exe) is running from this folder: close it, then start MTDX again to update')
    if other_instances(p):
        raise UpdateError('another MTDX of this folder is open: close it, then start MTDX again to update')
    a = entry['asset']
    d = _dir(p)
    try:
        free = shutil.disk_usage(d).free
    except OSError:
        free = None
    if free is not None and free < 4 * a['size'] + (64 << 20):
        raise UpdateError(f'the drive of {p.root} has {size_text(free)} free; the update needs about '
                          f'{size_text(4 * a["size"] + (64 << 20))} for a moment')
    for n in os.listdir(d):                         # an older download that is not this one
        if n.endswith(('.zip', '.zip.part')) and not n.startswith(a['sha256']):
            os.remove(os.path.join(d, n))
    zpath = os.path.join(d, a['sha256'] + '.zip')
    log(f'{summary(entry)}: downloading')
    download(a['url'], zpath, a['size'], a['sha256'], progress=progress, stop=stop)
    new = os.path.join(d, 'new')
    if os.path.exists(new):
        shutil.rmtree(new)
    log(f'MTDX {entry.get("version", "")}: unpacking')
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            nn = n.replace('\\', '/')
            if nn.startswith('/') or '..' in nn.split('/') or ':' in nn:
                raise UpdateError(f'unsafe path in the MTDX download: {n}')
        z.extractall(new)
    tops = os.listdir(new)
    src = os.path.join(new, tops[0]) if len(tops) == 1 and os.path.isdir(os.path.join(new, tops[0])) else new
    for need in ('MTDX.exe', 'RTDX.exe', os.path.join('mtdx', 'release.json')):
        if not os.path.isfile(os.path.join(src, need)):
            shutil.rmtree(new, ignore_errors=True)
            raise UpdateError(f'the MTDX download has no {need}')
    got = install._load_json(os.path.join(src, 'mtdx', 'release.json'), {}).get('made')
    if got != entry['build']:                       # it would update again at every start
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError(f'the MTDX download is build {got}, the update information says {entry["build"]}')
    cmd = os.path.join(d, 'apply_update.cmd')
    failed = os.path.join(d, 'update_failed.txt')
    if os.path.exists(failed):
        os.remove(failed)
    with open(os.path.join(d, 'last_attempt.txt'), 'w', encoding='utf-8') as f:
        f.write(entry['build'])
    q = lambda path: path.replace('%', '%%')        # noqa: E731 - a batch file expands %...%
    rj = os.path.join('mtdx', 'release.json')
    with open(cmd, 'w', encoding='utf-8', newline='') as f:
        # cmd reads a batch file in the console code page: switch it to UTF-8 first, so any folder name works; the Windows
        # tools by full path: another "find" or "ping" earlier on PATH (Git's, say) would break the wait.
        # mtdx/release.json (the build number) is copied last and only when everything else was: a copy that failed half
        # way leaves the old number, so the next start sees the update as not installed and says so
        f.write('@echo off\r\n'
                'chcp 65001 >nul\r\n'
                'set "S=%SystemRoot%\\System32"\r\n'
                ':wait\r\n'
                '"%S%\\tasklist.exe" /FI "PID eq %1" /NH 2>nul | "%S%\\find.exe" " %1 " >nul '
                '&& ("%S%\\ping.exe" -n 2 127.0.0.1 >nul & goto wait)\r\n'
                f'"%S%\\robocopy.exe" "{q(src)}" "{q(p.root)}" /E /XF release.json /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul\r\n'
                f'if errorlevel 8 (echo the copy failed: robocopy code %errorlevel%> "{q(failed)}" & goto done)\r\n'
                f'copy /y "{q(os.path.join(src, rj))}" "{q(os.path.join(p.root, rj))}" >nul '
                f'|| (echo the copy of {rj} failed> "{q(failed)}" & goto done)\r\n'
                f'rmdir /s /q "{q(new)}"\r\n'
                f'del "{q(zpath)}"\r\n'
                ':done\r\n')
        if restart:
            f.write(f'cd /d "{q(p.root)}"\r\n'
                    f'start "" "{q(os.path.join(p.root, "MTDX.exe"))}"\r\n')
    log(f'MTDX {entry.get("version", "")}: ready - MTDX closes, copies it in and starts again')
    return cmd


def self_update_failed(p, build):
    """-> a reason when the last update to `build` did not take (then MTDX does not try it again on its own)"""
    d = os.path.join(p.state, 'update')
    failed = os.path.join(d, 'update_failed.txt')
    if os.path.isfile(failed):
        return open(failed, encoding='utf-8', errors='replace').read().strip() or 'the copy failed'
    try:
        last = open(os.path.join(d, 'last_attempt.txt'), encoding='utf-8').read().strip()
    except OSError:
        return None
    return 'it was copied in, but MTDX still reports its old build' if last == build else None


def just_updated(p):
    """-> True once after an update was copied in (last_attempt.txt names the build this folder has now); then forgets it"""
    d = os.path.join(p.state, 'update')
    f = os.path.join(d, 'last_attempt.txt')
    try:
        last = open(f, encoding='utf-8').read().strip()
    except OSError:
        return False
    if last and last == current_build(p) and not os.path.isfile(os.path.join(d, 'update_failed.txt')):
        os.remove(f)
        return True
    return False


def run_self_update(cmd):
    """start the prepared update; the caller exits right after (the command waits for this process)"""
    import subprocess
    # a hidden console the command's tools share (DETACHED_PROCESS would flash a window per tool); "call" keeps the
    # quotes of a path with spaces or parentheses intact under cmd /c
    flags = 0x00000200 | 0x08000000    # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    subprocess.Popen(['cmd', '/d', '/c', 'call', cmd, str(os.getpid())], creationflags=flags, close_fds=True,
                     cwd=os.path.dirname(cmd))

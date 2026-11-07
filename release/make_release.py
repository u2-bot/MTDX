"""Assemble an MTDX release folder from a Windows player build of the RTDX PC rebuild (no game data inside).

  py -3.13 -I release/make_release.py PLAYER_DIR PLAYER_BUNDLES DUMP OUT_DIR [--exe DIST_DIR]
PLAYER_DIR = the player build (RTDX.exe beside RTDX_Data); PLAYER_BUNDLES = its Windows bundles (<name>.ab), used only for
the shader pack; DUMP = a Rescue Team DX dump (the folder with romfs + exefs), read for IDs, names and the audit only -
nothing of it is copied. --exe = the frozen converter (release/build_exe.py output).
Steps: copy our player files (every file except the game data below) -> make_player_base (renumbered registry + base
globalgamemanagers) -> make_shaderpack -> mtdx/bundles.txt + release.json (incl. the sha1 of the native data image the
player was tested with) -> the frozen converter + its license texts -> LICENSE / THIRD-PARTY-NOTICES.md / README.md -> audit.
Left out of the release (the converter makes them from the user's dump): RTDX_Data/StreamingAssets/ab, abpc, data,
native_data, rtdx (the native data image); level0, sharedassets0.assets(.resS), resources.assets(.resS), globalgamemanagers
(the base copy lives in mtdx/base/), our globalgamemanagers.assets (replaced by the renumbered one); logs and the native
layer's diagnostic dumps (Plugins/*.log, Plugins/nvn_shaders); the play-test README.
Audit: no release file has the bytes of any dump file or names a build-machine folder (.pdb paths cut to the file name
first); every serialized file of the release holds only engine settings, MonoScripts, Unity built-in resources and our
renumbered registry (no Texture2D / Mesh / AudioClip / TextAsset / MonoBehaviour / GameObject ...).
"""
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, 'converter'))

EXCLUDE_DIRS = {'RTDX_Data/StreamingAssets/ab', 'RTDX_Data/StreamingAssets/abpc', 'RTDX_Data/StreamingAssets/data',
                'RTDX_Data/StreamingAssets/native_data', 'RTDX_Data/StreamingAssets/rtdx', 'RTDX_Data/Plugins/nvn_shaders',
                'RTDX_BackUpThisFolder_ButDontShipItWithYourGame', 'save_archive'}
EXCLUDE_FILES = {'RTDX_Data/level0', 'RTDX_Data/sharedassets0.assets', 'RTDX_Data/sharedassets0.assets.resS',
                 'RTDX_Data/resources.assets', 'RTDX_Data/resources.assets.resS', 'RTDX_Data/globalgamemanagers',
                 'RTDX_Data/globalgamemanagers.assets', 'README_PLAYTEST.txt', 'RTDX_Data/output_log.txt'}
EXCLUDE_SUFFIXES = ('.log', '.pdb')
GAME_TYPES = {'Texture2D', 'Cubemap', 'Mesh', 'AudioClip', 'VideoClip', 'TextAsset', 'MonoBehaviour', 'AnimationClip', 'Sprite',
              'Font', 'GameObject'}
SERIALIZED = ('RTDX_Data/globalgamemanagers.assets',)
# Unity runtime files that are equal to a dump file only because the game was built with the same Unity (2018.3.12f1, IL2CPP):
# mscorlib.dll-resources.dat = the embedded resources (exception messages) of Unity's own unityaot mscorlib.dll, written by
# il2cpp.exe into every IL2CPP player (checked 2026-11-07: its bytes occur in Editor/Data/MonoBleedingEdge/lib/mono/unityaot/
# mscorlib.dll). Not game data; listed in audit.txt as allowed.
UNITY_RUNTIME_SAME_AS_DUMP = {'RTDX_Data/il2cpp_data/Resources/mscorlib.dll-resources.dat'}


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def rel_files(root):
    for d, dirs, fs in os.walk(root):
        for f in fs:
            yield os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/')


def excluded(rel):
    return rel in EXCLUDE_FILES or rel.lower().endswith(EXCLUDE_SUFFIXES) or \
        any(rel == x or rel.startswith(x + '/') for x in EXCLUDE_DIRS)


def copy_player(player, out):
    n = 0
    for rel in sorted(rel_files(player)):
        if excluded(rel):
            continue
        d = os.path.join(out, *rel.split('/'))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(player, *rel.split('/')), d)
        n += 1
    return n


def neutral_pdb_paths(path, markers):
    """The linker writes the absolute path of the build's .pdb into the PE debug directory (CodeView 'RSDS' record); for a
    path on the build machine (one of `markers`: our own assemblies, built in the project's Temp folder) the release keeps
    only the file name (same length, zero-filled). Windows never reads it. Third-party binaries keep theirs untouched (they
    may be signed). -> the old paths."""
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    old_paths = []
    i = data.find(b'RSDS')
    while i != -1:
        start = i + 4 + 16 + 4                      # signature, GUID, age
        end = data.find(b'\0', start)
        old = bytes(data[start:end])
        if old.lower().endswith(b'.pdb') and any(m.lower() in old.lower() for m in markers):
            name = old.replace(b'/', b'\\').rsplit(b'\\', 1)[-1]
            data[start:end] = name + b'\0' * (len(old) - len(name))
            old_paths.append(old.decode('ascii', 'replace'))
        i = data.find(b'RSDS', i + 4)
    if old_paths:
        with open(path, 'wb') as f:
            f.write(data)
    return old_paths


def objcopy_tool():
    for c in (os.environ.get('MTDX_OBJCOPY'), shutil.which('objcopy'), os.path.join('C:' + os.sep, 'mingw64', 'bin', 'objcopy.exe')):
        if c and os.path.isfile(c):
            return c
    sys.exit('objcopy (MinGW binutils) is needed to strip the debug sections of rtdx_native.dll: set MTDX_OBJCOPY')


def strip_debug(path):
    """rtdx_native.dll is linked with DWARF debug sections (about 75 MB, the build machine's source paths in them): the
    release ships it with them removed (objcopy --strip-debug). Checked: every loadable section keeps its bytes."""
    import subprocess
    import tempfile
    tool = objcopy_tool()
    before = os.path.getsize(path)
    tmp = path + '.stripped'
    subprocess.run([tool, '--strip-debug', path, tmp], check=True)
    with tempfile.TemporaryDirectory() as td:
        for sec in ('.text', '.data', '.rdata', '.pdata', '.xdata', '.edata', '.idata', '.tls', '.reloc'):
            a, b = os.path.join(td, 'a'), os.path.join(td, 'b')
            subprocess.run([tool, '-O', 'binary', f'--only-section={sec}', path, a], check=True)
            subprocess.run([tool, '-O', 'binary', f'--only-section={sec}', tmp, b], check=True)
            if open(a, 'rb').read() != open(b, 'rb').read():
                sys.exit(f'{path}: section {sec} changed by --strip-debug')
    # objcopy stamps the current time into the PE header (TimeDateStamp, e_lfanew + 8): put the build's own back, so two
    # releases of one player are byte-identical
    import struct
    with open(path, 'rb') as f:
        src = f.read(4096)
    pe = struct.unpack_from('<I', src, 0x3c)[0]
    with open(tmp, 'rb') as f:
        data = bytearray(f.read())
    data[pe + 8:pe + 12] = src[pe + 8:pe + 12]
    # ... and the optional header's CheckSum (objcopy computed it over its own timestamp): the PE checksum = 16-bit
    # one's-complement-style sum of the file without that field, plus the file length
    cs = pe + 24 + 64
    data[cs:cs + 4] = b'\0\0\0\0'
    total = 0
    for (w,) in struct.iter_unpack('<H', bytes(data) + (b'\0' if len(data) % 2 else b'')):
        total += w
        total = (total & 0xffff) + (total >> 16)
    data[cs:cs + 4] = struct.pack('<I', ((total & 0xffff) + (total >> 16) + len(data)) & 0xffffffff)
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return before, os.path.getsize(path)


def pyz_top_modules(exe):
    """Top-level module names inside a PyInstaller executable's PYZ archive."""
    import tempfile
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    car = CArchiveReader(exe)
    out = set()
    for name in car.toc:
        if name.lower().endswith('.pyz') or name.upper().startswith('PYZ'):
            fd, tmp = tempfile.mkstemp(suffix='.pyz')
            os.close(fd)
            try:
                with open(tmp, 'wb') as f:
                    f.write(car.extract(name))
                out |= {m.split('.')[0] for m in ZlibArchiveReader(tmp).toc}
            finally:
                os.remove(tmp)
    return out


def collect_licenses(runtime, dst):
    """License texts of everything the frozen converter bundles -> dst/<distribution>/..., plus Python's LICENSE.txt and
    PyInstaller's COPYING.txt (bootloader). -> number of distributions."""
    import importlib.metadata as md
    owners = md.packages_distributions()
    mods = {entry.split('.')[0] for entry in os.listdir(runtime)}
    mods |= pyz_top_modules(os.path.join(os.path.dirname(runtime), 'MTDX.exe'))
    names = {'pyinstaller'}
    for mod in mods:
        for dist in owners.get(mod, []):
            names.add(dist)
    os.makedirs(dst, exist_ok=True)
    index = []
    for name in sorted(names, key=str.lower):
        try:
            meta = md.metadata(name)
            files = [f for f in (md.files(name) or []) if any(k in f.name.upper() for k in ('LICEN', 'COPYING', 'NOTICE'))]
        except md.PackageNotFoundError:
            continue
        lic = (meta.get('License-Expression') or '').strip()
        if not lic:
            lic = '; '.join(c.split('::')[-1].strip() for c in meta.get_all('Classifier') or [] if c.startswith('License'))
        if not lic:
            lic = (meta.get('License') or '').strip().splitlines()[0] if meta.get('License') else 'see files'
        d = os.path.join(dst, meta['Name'])
        for f in files:
            src = f.locate()
            if os.path.isfile(src):
                t = os.path.join(d, str(f).split('.dist-info/', 1)[-1].replace('/', '_'))
                os.makedirs(d, exist_ok=True)
                shutil.copyfile(src, t)
        index.append(f'{meta["Name"]}\t{meta["Version"]}\t{lic}')
    py = os.path.join(sys.base_prefix, 'LICENSE.txt')
    if os.path.isfile(py):
        os.makedirs(os.path.join(dst, 'Python'), exist_ok=True)
        shutil.copyfile(py, os.path.join(dst, 'Python', 'LICENSE.txt'))
        index.append(f'Python\t{sys.version.split()[0]}\tPSF-2.0 (+ the bundled libraries listed in its LICENSE.txt)')
    with open(os.path.join(dst, 'index.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('package\tversion\tlicense\n' + '\n'.join(index) + '\n')
    return len(index)


def local_markers(*paths):
    """Strings that would tie a release file to the build machine: the top folder of each input path (e.g. 'E:\\Work'),
    in the spellings binaries use."""
    out = set()
    for p in paths:
        parts = os.path.abspath(p).split(os.sep)
        if len(parts) > 1 and parts[1]:
            top = parts[0] + '\\' + parts[1]
            out |= {top.encode(), top.replace('\\', '/').encode(), top.encode('utf-16-le')}
    return out


def audit(out, dump, markers=()):
    import UnityPy
    problems = []
    allowed = []
    dump_hashes = {}
    sizes = {}
    for rel in rel_files(dump):
        p = os.path.join(dump, *rel.split('/'))
        sizes.setdefault(os.path.getsize(p), []).append(p)
    n = 0
    for rel in rel_files(out):
        p = os.path.join(out, *rel.split('/'))
        n += 1
        if markers and rel != 'mtdx/audit.txt':
            with open(p, 'rb') as f:
                blob = f.read()
            low = blob.lower()
            hit = next((m for m in markers if m.lower() in low), None)
            if hit:
                problems.append(f'{rel} names a build-machine folder ({hit.decode("utf-16-le") if b"\0" in hit else hit.decode()})')
        for q in sizes.get(os.path.getsize(p), []):
            if q not in dump_hashes:
                dump_hashes[q] = sha1(q)
            if dump_hashes[q] == sha1(p):
                if rel in UNITY_RUNTIME_SAME_AS_DUMP:
                    allowed.append(f'{rel} = dump file {q}: Unity runtime file, allowed')
                else:
                    problems.append(f'{rel} has the bytes of dump file {q}')
        if rel in SERIALIZED or (rel.startswith('RTDX_Data/') and '/' not in rel[len('RTDX_Data/'):]
                                 and not rel.endswith(('.info', '.config'))):
            try:
                objs = list(UnityPy.load(p).objects)
            except Exception:
                objs = []
            for o in objs:
                if o.type.name in GAME_TYPES:
                    problems.append(f'{rel}: holds a {o.type.name} object')
                    break
    return n, problems, allowed


def run(args):
    import subprocess
    print('>', ' '.join(args), flush=True)
    r = subprocess.run([sys.executable, '-I'] + args)
    if r.returncode:
        sys.exit(f'step failed: {args[0]}')


def main():
    a = sys.argv[1:]
    exe = a[a.index('--exe') + 1] if '--exe' in a else None
    flags = {'--exe'}
    pos = [x for i, x in enumerate(a) if x not in flags and (i == 0 or a[i - 1] not in flags)]
    player, bundles_dir, dump, out = (os.path.abspath(x) for x in pos[:4])
    data = os.path.join(dump, 'romfs', 'Data')
    if os.path.exists(out) and os.listdir(out):
        aside = f'{out}_{time.strftime("%Y%m%d_%H%M%S")}'
        os.rename(out, aside)
        print(f'previous release folder moved aside: {aside}')
    os.makedirs(out)
    n = copy_player(player, out)
    print(f'player files copied: {n}')
    markers = local_markers(player, dump, REPO)
    a_, b_ = strip_debug(os.path.join(out, 'RTDX_Data', 'Plugins', 'rtdx_native.dll'))
    print(f'rtdx_native.dll: debug sections stripped, {a_ / 2 ** 20:.1f} -> {b_ / 2 ** 20:.1f} MB (loadable sections unchanged)')
    for rel in rel_files(out):
        if rel.lower().endswith(('.dll', '.exe')):
            old = neutral_pdb_paths(os.path.join(out, *rel.split('/')), markers)
            if old:
                print(f'{rel}: debug path {old} -> file name only')
    pdata = os.path.join(player, 'RTDX_Data')
    run([os.path.join(HERE, 'make_player_base.py'), pdata, data, out])
    run([os.path.join(HERE, 'make_shaderpack.py'), pdata, bundles_dir, data, os.path.join(out, 'mtdx', 'shaders.pack')])
    shutil.copyfile(os.path.join(HERE, 'bundles.txt'), os.path.join(out, 'mtdx', 'bundles.txt'))
    img = os.path.join(pdata, 'StreamingAssets', 'rtdx', 'rtdx_image.bin')
    from mtdx import CONVERT_VERSION, __version__
    info = {'mtdx_version': __version__, 'convert_version': CONVERT_VERSION, 'image_sha1': sha1(img),
            'player': os.path.basename(player), 'made': time.strftime('%Y-%m-%d %H:%M:%S'),
            'game': 'Pokemon Mystery Dungeon: Rescue Team DX v1.0.2 (Switch dump: romfs + exefs)'}
    if exe:
        for rel in rel_files(exe):
            if '/__pycache__/' in '/' + rel or rel.endswith('.pyc'):
                continue                # a Python started inside the dist folder compiles caches there (they name its path)
            d = os.path.join(out, *rel.split('/'))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(os.path.join(exe, *rel.split('/')), d)
        info['exe'] = os.path.basename(os.path.abspath(exe))
        n = collect_licenses(os.path.join(exe, 'mtdx_runtime'), os.path.join(out, 'mtdx', 'licenses'))
        print(f'license texts collected: {n} packages')
    for f in ('LICENSE', 'THIRD-PARTY-NOTICES.md', 'README.md'):
        shutil.copyfile(os.path.join(REPO, f), os.path.join(out, f))
    with open(os.path.join(out, 'mtdx', 'release.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=1)
    files, problems, allowed = audit(out, dump, markers)
    rep = os.path.join(out, 'mtdx', 'audit.txt')
    with open(rep, 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'{files} release files; {len(problems)} problems\n' + '\n'.join(problems) + '\n'
                + ''.join(f'allowed: {x}\n' for x in allowed))
    print(f'audit: {files} files, {len(problems)} problems -> {rep}')
    for p in problems[:20]:
        print('  ', p)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

"""MTDX's update feed: the signed mtdx-update.json every GitHub release carries next to its zip (converter/mtdx/update.py
reads it from the latest release).

  py -3.13 -I release/update_feed.py keygen KEYFILE
        once: writes a new signing key to KEYFILE (the PRIVATE key - keep it out of every repository and back it up: without
        it no update can be signed, and a new key needs a new MTDX that players install by hand) and prints the public key
        for update.PUBLIC_KEY. Refuses to overwrite an existing key file (then it prints that key's public half).
  py -3.13 -I release/update_feed.py make ZIP KEYFILE [--tag vX.Y.Z] [--repo OWNER/NAME] [--url URL] [--notes TEXT]
        -> mtdx-update.json next to ZIP (a zip_release.py output). Upload BOTH to the release named by --tag (default
        v<version of the zip>); --url overrides the download address (default the release asset on GitHub).
  py -3.13 -I release/update_feed.py show FEED [ZIP]
        checks a feed's signature and prints it; with ZIP also that the zip is the one it names (size + SHA-256).
"""
import base64
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'converter'))
from mtdx import ed25519, update  # noqa: E402

FEED_NAME = 'mtdx-update.json'


def load_seed(path):
    with open(path, encoding='ascii') as f:
        for line in f:
            if line.startswith('private '):
                return bytes.fromhex(line.split()[1])
    raise SystemExit(f'no "private" line in {path}')


def keygen(path):
    if os.path.exists(path):
        print(f'key exists: {path}\npublic {ed25519.public_key(load_seed(path)).hex()}')
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    seed = os.urandom(32)
    pub = ed25519.public_key(seed)
    with open(path, 'x', encoding='ascii', newline='\n') as f:
        f.write('# MTDX update feed signing key (Ed25519). PRIVATE: keep it out of every repository; back it up.\n')
        f.write(f'private {seed.hex()}\npublic {pub.hex()}\n')
    print(f'written {path}\npublic {pub.hex()}')
    return 0


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for buf in iter(lambda: f.read(1 << 20), b''):
            h.update(buf)
    return h.hexdigest()


def zip_release_info(path):
    """-> release.json of a release zip (one top folder MTDX-<version>/)"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith('/mtdx/release.json') and n.count('/') == 2]
        if len(names) != 1 or not any(n.endswith('/MTDX.exe') and n.count('/') == 1 for n in z.namelist()):
            raise SystemExit(f'{path}: not an MTDX release zip (one top folder with MTDX.exe and mtdx/release.json)')
        return json.loads(z.read(names[0]).decode('utf-8'))


def make(args):
    def opt(name, default=None):
        if name in args:
            i = args.index(name)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default
    tag, repo, url, notes = opt('--tag'), opt('--repo', update.REPO), opt('--url'), opt('--notes', '')
    if len(args) != 2:
        raise SystemExit(__doc__)
    zpath, keyfile = (os.path.abspath(a) for a in args)
    seed = load_seed(keyfile)
    if ed25519.public_key(seed) != update.PUBLIC_KEY:
        raise SystemExit('the key file does not hold the key whose public half is update.PUBLIC_KEY')
    info = zip_release_info(zpath)
    if not info.get('made') or not info.get('mtdx_version'):
        raise SystemExit(f'{zpath}: its release.json has no "made" / "mtdx_version"')
    tag = tag or f'v{info["mtdx_version"]}'
    name = os.path.basename(zpath)
    entry = {'version': info['mtdx_version'], 'build': info['made'], 'notes': notes,
             'page': f'https://github.com/{repo}/releases/tag/{tag}',
             'asset': {'name': name, 'url': url or f'https://github.com/{repo}/releases/download/{tag}/{name}',
                       'size': os.path.getsize(zpath), 'sha256': sha256(zpath)}}
    text = json.dumps({'format': update.FORMAT, 'mtdx': entry}, indent=1, ensure_ascii=False)
    env = {'format': update.FORMAT, 'index': text,
           'sig': base64.b64encode(ed25519.sign(seed, text.encode('utf-8'))).decode('ascii')}
    out = os.path.join(os.path.dirname(zpath), FEED_NAME)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(env, f, indent=1, ensure_ascii=False)
        f.write('\n')
    update.parse_feed(open(out, 'rb').read())          # the client's own check
    print(f'{out}: MTDX {entry["version"]} build {entry["build"]}, {entry["asset"]["size"]:,} B, '
          f'sha256 {entry["asset"]["sha256"]}\n  download {entry["asset"]["url"]}\n  upload {name} + {FEED_NAME} to release {tag}')
    return 0


def show(args):
    if not args:
        raise SystemExit(__doc__)
    index = update.parse_feed(open(args[0], 'rb').read())
    print(json.dumps(index, indent=1, ensure_ascii=False))
    if len(args) > 1:
        a = index['mtdx']['asset']
        ok = os.path.getsize(args[1]) == a['size'] and sha256(args[1]) == a['sha256']
        info = zip_release_info(args[1])
        ok = ok and info.get('made') == index['mtdx']['build']
        print(f'{args[1]}: ' + ('the zip this feed names' if ok else 'NOT the zip this feed names'))
        return 0 if ok else 1
    return 0


def main():
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    cmd = a.pop(0)
    if cmd == 'keygen' and len(a) == 1:
        return keygen(a[0])
    if cmd == 'make':
        return make(a)
    if cmd == 'show':
        return show(a)
    raise SystemExit(__doc__)


if __name__ == '__main__':
    sys.exit(main())

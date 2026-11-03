"""Developer check: a converted bundle (or player file) against its Switch source, object by object.

  py -3.13 -I release/verify_bundle.py SWITCH_DIR OUT_DIR [NAME ...] [--all] [--workers N] [--report OUT.tsv]
--all = every *.ab of SWITCH_DIR. A NAME may also be a player file (resources.assets, sharedassets0.assets, level0): its
.resS is then the loose file beside it.
For every serialized file: same entry names, same path IDs and types, platform 19; Texture2D / Cubemap = the Switch data run
through the texture rules (decoded RGBA32 equal, copies byte-identical, every other field unchanged); Mesh = same fields +
the same stream bytes; Shader = replaced (not checked here); every other object byte-identical; .resource (audio) entries
byte-identical.
"""
import concurrent.futures as cf
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'converter'))


def _streams(env, path):
    from UnityPy.files.SerializedFile import SerializedFile
    if isinstance(env.file, SerializedFile):
        res = path + '.resS'
        name = os.path.basename(res)
        return {name: open(res, 'rb').read()} if os.path.isfile(res) else {}
    return {k: v.bytes for k, v in env.file.files.items() if k.endswith('.resS')}


def check(args):
    sw_path, out_path, rel = args
    import UnityPy
    from UnityPy.files.SerializedFile import SerializedFile
    from mtdx import textures
    from mtdx.unityfiles import serialized_files
    errs = []
    n = {'objects': 0, 'tex': 0, 'decoded': 0, 'mesh': 0, 'shader': 0, 'same': 0, 'audio': 0}
    try:
        a = UnityPy.load(sw_path)
        b = UnityPy.load(out_path)
        if not isinstance(a.file, SerializedFile):
            ea, eb = list(a.file.files), list(b.file.files)
            if ea != eb:
                errs.append(f'entries {ea} != {eb}')
            for k, v in a.file.files.items():
                if k.endswith('.resource'):
                    if bytes(v.bytes) != bytes(b.file.files[k].bytes):
                        errs.append(f'{k}: audio bytes differ')
                    else:
                        n['audio'] += 1
        sa, sb = dict(serialized_files(a)), dict(serialized_files(b))
        streams_a, streams_b = _streams(a, sw_path), _streams(b, out_path)

        def stream(streams, sd):
            if not sd or not sd.get('size'):
                return None
            s = streams[sd['path'].rsplit('/', 1)[-1]]
            return bytes(s[sd['offset']:sd['offset'] + sd['size']])

        for name, fa in sa.items():
            fb = sb.get(name)
            if fb is None:
                errs.append(f'{name} missing')
                continue
            if fb._m_target_platform != 19:
                errs.append(f'{name} platform {fb._m_target_platform}')
            if set(fa.objects) != set(fb.objects):
                errs.append(f'{name}: path id sets differ')
                continue
            if [e.path for e in fa.externals] != [e.path for e in fb.externals][:len(fa.externals)]:
                errs.append(f'{name}: externals changed')
            for pid, oa in fa.objects.items():
                ob = fb.objects[pid]
                n['objects'] += 1
                t = oa.type.name
                if t != ob.type.name:
                    errs.append(f'{pid}: type {t} != {ob.type.name}')
                    continue
                if t in ('Texture2D', 'Cubemap'):
                    ta, tb = oa.read_typetree(), ob.read_typetree()
                    da = stream(streams_a, ta.get('m_StreamData')) or bytes(ta.get('image data', b'') or b'')
                    db = stream(streams_b, tb.get('m_StreamData')) or bytes(tb.get('image data', b'') or b'')
                    fmt, want, kind = textures.convert(ta['m_TextureFormat'], ta['m_Width'], ta['m_Height'],
                                                       ta['m_MipCount'], ta.get('m_ImageCount', 1) or 1, da)
                    if tb['m_TextureFormat'] != fmt or db != want:
                        errs.append(f'texture {ta["m_Name"]} ({pid}): format {tb["m_TextureFormat"]} vs {fmt}, data '
                                    f'{"equal" if db == want else "DIFFERS"}')
                    for k in ta:
                        if k not in ('m_TextureFormat', 'm_CompleteImageSize', 'm_StreamData', 'image data') \
                                and ta[k] != tb[k]:
                            errs.append(f'texture {ta["m_Name"]}: field {k} changed')
                    n['tex'] += 1
                    n['decoded'] += kind == 'decoded'
                elif t == 'Mesh':
                    ta, tb = oa.read_typetree(), ob.read_typetree()
                    if stream(streams_a, ta.get('m_StreamData')) != stream(streams_b, tb.get('m_StreamData')):
                        errs.append(f'mesh {pid}: stream bytes differ')
                    for k in ta:
                        if k != 'm_StreamData' and ta[k] != tb[k]:
                            errs.append(f'mesh {pid}: field {k} changed')
                    n['mesh'] += 1
                elif t == 'Shader':
                    n['shader'] += 1
                else:
                    if oa.get_raw_data() != ob.get_raw_data():
                        errs.append(f'{t} {pid}: bytes differ')
                    else:
                        n['same'] += 1
    except Exception as e:
        import traceback
        errs.append(f'EXCEPTION {type(e).__name__}: {e} {traceback.format_exc()[-400:]}')
    return rel, n, errs


def main():
    a = sys.argv[1:]
    workers = int(a[a.index('--workers') + 1]) if '--workers' in a else 8
    report = a[a.index('--report') + 1] if '--report' in a else None
    flags = {'--workers', '--report'}
    pos = [x for i, x in enumerate(a) if x not in flags and x != '--all' and (i == 0 or a[i - 1] not in flags)]
    sw, out = pos[0], pos[1]
    rels = pos[2:]
    if '--all' in a:
        rels += sorted(x for x in os.listdir(sw) if x.endswith('.ab'))
    jobs = [(os.path.join(sw, r), os.path.join(out, r), r) for r in rels]
    bad = 0
    tot = {}
    rows = []
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for rel, n, errs in ex.map(check, jobs, chunksize=4):
            for k, v in n.items():
                tot[k] = tot.get(k, 0) + v
            if errs:
                bad += 1
                print('FAIL', rel, errs[:5])
            rows.append((rel, 'ok' if not errs else 'FAIL', n, errs[:3]))
    if report:
        with open(report, 'w', encoding='utf-8', newline='\n') as f:
            for r in rows:
                f.write('\t'.join(map(str, r)) + '\n')
    print(f'{len(jobs)} files, {bad} failed, totals {tot}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

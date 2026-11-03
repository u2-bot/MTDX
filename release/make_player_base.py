"""Our player's own data files, made to fit the dump's player files (which the converter writes from the dump as they are).

  py -3.13 -I release/make_player_base.py PLAYER_DATA SWITCH_DATA OUT_DIR
PLAYER_DATA = the player's RTDX_Data. Writes OUT_DIR/RTDX_Data/globalgamemanagers.assets and OUT_DIR/mtdx/base/globalgamemanagers
(+ OUT_DIR/mtdx/make_player_base.txt, the report).

Why: the dump's level0, sharedassets0.assets and resources.assets point into globalgamemanagers.assets (the script registry:
MonoScripts, Sprites-Default and its two shaders, PreloadData) by the SWITCH path IDs - every MonoBehaviour's m_Script, the
files' script-type tables. Our Mono build numbered its registry its own way (1,940 MonoScripts against the Switch build's
1,268). So the registry is renumbered: every object of ours whose counterpart exists in the Switch registry (MonoScript by
namespace + class + assembly; Material / Shader by name; PreloadData) takes the Switch path ID, the rest get IDs above the
Switch range in their old order. References into the registry are remapped: globalgamemanagers MonoManager (m_Scripts),
GraphicsSettings, ResourceManager (replaced at install time anyway).
globalgamemanagers ScriptMapper (Shader.Find's table) points at shaders of resources.assets and sharedassets0.assets by OUR
path IDs: they are remapped by shader name to the Switch files; entries for shaders the Switch file does not hold (copies
our build added) are dropped, and an entry our build lists twice (two same-named copies) is kept once.
Every remapped reference is checked after writing: it resolves to an object of the expected type and name.
"""
import os
import sys

import UnityPy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'converter'))
from mtdx.unityfiles import walk_pptrs  # noqa: E402

GGM_ASSETS = 'globalgamemanagers.assets'
SHADER_FILES = ('resources.assets', 'sharedassets0.assets')


def load(path):
    return UnityPy.load(path).file


def registry_keys_one(o):
    t = o.type.name
    if t == 'MonoScript':
        tr = o.read_typetree()
        return ('MonoScript', tr.get('m_Namespace', ''), tr['m_ClassName'], tr['m_AssemblyName'])
    if t == 'PreloadData':
        return ('PreloadData', '')
    if t == 'Shader':
        return ('Shader', o.read_typetree()['m_ParsedForm']['m_Name'])
    return (t, o.peek_name())


def registry_keys(sf, unique=True):
    """{key: path id} (unique=False: {key: [path ids]})"""
    out = {}
    for o in sf.objects.values():
        k = registry_keys_one(o)
        if unique and k in out:
            raise SystemExit(f'duplicate registry key {k}')
        if unique:
            out[k] = o.path_id
        else:
            out.setdefault(k, []).append(o.path_id)
    return out


def plan_ids(ours_multi, switch_multi, say):
    """{our path id: new path id}. A key both registries hold gets the Switch ids: copies paired in path-id order (Unity
    writes two MonoScript objects for 77 package classes - UnityEngine.UI, Timeline, TextMesh Pro, Cinemachine ... - in
    both builds, numbered alike). Everything else is numbered above the Switch range in its old order."""
    sw_by_class = {}
    for k, pids in switch_multi.items():
        if k[0] == 'MonoScript':
            sw_by_class.setdefault(k[1:3], []).append((k[3], sorted(pids)))
    new = {}
    for k, pids in ours_multi.items():
        pids = sorted(pids)
        target = None
        if k[0] == 'MonoScript':
            # by assembly too: our assemblies are the shipped ones (no class moved between assemblies); a global-namespace
            # helper such as AssemblyInfo exists in several of them
            same = [p for a, p in sw_by_class.get(k[1:3], []) if a == k[3]]
            if same:
                target = same[0]
        elif k in switch_multi:
            target = sorted(switch_multi[k])
        if target:
            if len(pids) != len(target):
                say(f'  {k}: {len(pids)} copies of ours, {len(target)} in the Switch registry - paired in order')
            for a, b in zip(pids, target):
                new[a] = b
    used = set(new.values())
    if len(used) != len(new):
        raise SystemExit('two of our registry objects map to one Switch path id')
    nxt = max(p for pids in switch_multi.values() for p in pids) + 1
    for pid in sorted(p for pids in ours_multi.values() for p in pids):
        if pid not in new:
            while nxt in used:
                nxt += 1
            new[pid] = nxt
            used.add(nxt)
            nxt += 1
    return new


def rekey(sf, idmap):
    objs = list(sf.objects.values())
    for o in objs:
        o.path_id = idmap[o.path_id]
    sf.objects = {o.path_id: o for o in sorted(objs, key=lambda x: x.path_id)}


def shader_names(sf):
    return {o.path_id: o.read_typetree()['m_ParsedForm']['m_Name'] for o in sf.objects.values() if o.type.name == 'Shader'}


def build(pdata, switch_data, out, say=print):
    sw_reg = load(os.path.join(switch_data, GGM_ASSETS))
    reg = load(os.path.join(pdata, GGM_ASSETS))
    sw_multi, keys_multi = registry_keys(sw_reg, unique=False), registry_keys(reg, unique=False)
    idmap = plan_ids(keys_multi, sw_multi, say)
    sw_key_of = {pid: k for k, pids in sw_multi.items() for pid in pids}
    sw_ids = set(sw_key_of)
    kept = sum(1 for p in idmap.values() if p in sw_ids)
    say(f'registry: {len(idmap)} objects, {kept} take a Switch path id, {len(idmap) - kept} numbered above '
        f'{max(sw_ids)}; Switch registry {len(sw_ids)} objects')
    taken = set(idmap.values())
    missing = sorted({sw_key_of[p] for p in sw_ids - taken})
    say(f'Switch registry objects with no counterpart of ours: {len(sw_ids - taken)} {missing[:20]}')
    # local references inside the registry (Material -> its shader, PreloadData)
    for o in reg.objects.values():
        if o.type.name == 'MonoScript':
            continue
        tree = o.read_typetree()

        def loc(p):
            if p['m_FileID'] == 0 and p['m_PathID'] in idmap:
                return {'m_FileID': 0, 'm_PathID': idmap[p['m_PathID']]}
            return None
        if walk_pptrs(tree, loc) is not None:
            o.save_typetree(tree)
    rekey(reg, idmap)

    # globalgamemanagers
    ggm = load(os.path.join(pdata, 'globalgamemanagers'))
    ext = [e.path for e in ggm.externals]
    reg_fid = ext.index(GGM_ASSETS) + 1
    sh_fid = {ext.index(f) + 1: f for f in SHADER_FILES}
    pc_sh = {f: shader_names(load(os.path.join(pdata, f))) for f in SHADER_FILES}
    sw_sh_by_name = {}
    for f in SHADER_FILES:
        m = {}
        for pid, n in shader_names(load(os.path.join(switch_data, f))).items():
            m.setdefault(n, pid)
        sw_sh_by_name[f] = m
    counts = {}
    for o in ggm.objects.values():
        t = o.type.name
        try:
            tree = o.read_typetree()
        except Exception as e:
            say(f'  {t} {o.path_id}: not readable ({type(e).__name__}) - kept as it is')
            continue

        def remap(p, t=t):
            if p['m_FileID'] == reg_fid and p['m_PathID']:
                counts[(t, 'registry')] = counts.get((t, 'registry'), 0) + 1
                return {'m_FileID': reg_fid, 'm_PathID': idmap[p['m_PathID']]}
            return None
        changed = walk_pptrs(tree, remap) is not None
        if t == 'ScriptMapper':
            pairs = tree['m_Shaders']['m_ObjectToName']
            new_pairs, seen = [], set()
            for pair in pairs:
                p, name = pair
                if p['m_FileID'] in sh_fid:
                    f = sh_fid[p['m_FileID']]
                    n = pc_sh[f].get(p['m_PathID'])
                    if n is None or n != name:
                        raise SystemExit(f'ScriptMapper: {name} -> {f} {p["m_PathID"]} = {n}')
                    if n not in sw_sh_by_name[f]:
                        counts['ScriptMapper dropped'] = counts.get('ScriptMapper dropped', 0) + 1
                        say(f'  ScriptMapper: dropped {name} (not in the Switch {f})')
                        continue
                    pair = ({'m_FileID': p['m_FileID'], 'm_PathID': sw_sh_by_name[f][n]}, name)
                    counts['ScriptMapper remapped'] = counts.get('ScriptMapper remapped', 0) + 1
                key = (pair[0]['m_FileID'], pair[0]['m_PathID'], pair[1])
                if key in seen:
                    counts['ScriptMapper duplicate'] = counts.get('ScriptMapper duplicate', 0) + 1
                    continue
                seen.add(key)
                new_pairs.append(pair)
            tree['m_Shaders']['m_ObjectToName'] = new_pairs
            changed = True
        if changed:
            o.save_typetree(tree)
    say(f'globalgamemanagers references remapped: {counts}')

    os.makedirs(os.path.join(out, 'RTDX_Data'), exist_ok=True)
    os.makedirs(os.path.join(out, 'mtdx', 'base'), exist_ok=True)
    p_reg = os.path.join(out, 'RTDX_Data', GGM_ASSETS)
    p_ggm = os.path.join(out, 'mtdx', 'base', 'globalgamemanagers')
    with open(p_reg, 'wb') as f:
        f.write(reg.save())
    with open(p_ggm, 'wb') as f:
        f.write(ggm.save())

    # check: re-read; every object carries its own key; every shared Switch id holds the same class; MonoManager resolves
    reg2, ggm2 = load(p_reg), load(p_ggm)
    before = {pid: k for k, pids in keys_multi.items() for pid in pids}
    back = {new: old for old, new in idmap.items()}
    for o in reg2.objects.values():
        k = registry_keys_one(o)
        if before.get(back.get(o.path_id)) != k:
            raise SystemExit(f'check: registry object {o.path_id} is {k}, expected {before.get(back.get(o.path_id))}')
    for pid, k in sw_key_of.items():
        o = reg2.objects.get(pid)
        if o is not None:
            mk = registry_keys_one(o)
            if (mk[1:3] if k[0] == 'MonoScript' else mk) != (k[1:3] if k[0] == 'MonoScript' else k):
                raise SystemExit(f'check: Switch id {pid} = {k} but ours holds {mk}')
    say(f'check: {len(reg2.objects)} registry objects carry their own key; every shared Switch id holds the same object')
    for o in ggm2.objects.values():
        if o.type.name == 'MonoManager':
            scripts = o.read_typetree()['m_Scripts']
            bad = [p for p in scripts if p['m_FileID'] == reg_fid and p['m_PathID'] not in reg2.objects]
            if bad:
                raise SystemExit(f'check: {len(bad)} MonoManager scripts do not resolve')
            say(f'check: MonoManager {len(scripts)} scripts resolve')
        if o.type.name == 'ScriptMapper':
            sw_sh = {f: shader_names(load(os.path.join(switch_data, f))) for f in SHADER_FILES}
            n = 0
            for p, name in o.read_typetree()['m_Shaders']['m_ObjectToName']:
                if p['m_FileID'] in sh_fid:
                    if sw_sh[sh_fid[p['m_FileID']]].get(p['m_PathID']) != name:
                        raise SystemExit(f'check: ScriptMapper {name} -> {p} is not that shader in the Switch file')
                    n += 1
            say(f'check: ScriptMapper {n} shader entries name the Switch files\' shaders')
    say(f'-> {os.path.relpath(p_reg, out)}\n-> {os.path.relpath(p_ggm, out)}')
    return p_reg, p_ggm


def main():
    pdata, switch_data, out = sys.argv[1:4]
    log = []

    def say(s):
        print(s)
        log.append(s)
    build(pdata, switch_data, out, say)
    with open(os.path.join(out, 'mtdx', 'make_player_base.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(log) + '\n')


if __name__ == '__main__':
    main()

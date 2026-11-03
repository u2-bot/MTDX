"""Build MTDX.exe (PyInstaller, one-folder) from converter/.

  py -3.13 -I release/build_exe.py [DIST_DIR]      (default work dir: <repo>/build/, dist: <repo>/build/dist)
Output DIST_DIR/MTDX = MTDX.exe + mtdx_runtime/ (Python, UnityPy, numpy, texture2ddecoder, lz4 ...). make_release.py --exe
DIST_DIR/MTDX copies it into a release folder next to RTDX.exe. One-folder, not one-file: a one-file exe unpacks itself to
%TEMP% on every start and again in each of the converter's worker processes.
Pinned build inputs: converter/requirements.txt, release/requirements-build.txt.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    work = os.path.join(REPO, 'build')
    dist = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(work, 'dist')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'MTDX',
            '--contents-directory', 'mtdx_runtime', '--distpath', dist, '--workpath', os.path.join(work, 'pyinstaller'),
            '--specpath', os.path.join(work, 'spec'), '--paths', os.path.join(REPO, 'converter'),
            '--collect-all', 'UnityPy', '--collect-all', 'texture2ddecoder', '--hidden-import', 'mtdx.gui',
            '--collect-submodules', 'mtdx',
            '--exclude-module', 'matplotlib', '--exclude-module', 'IPython', '--exclude-module', 'pytest',
            '--exclude-module', 'setuptools', '--exclude-module', 'TypeTreeGeneratorAPI',
            os.path.join(REPO, 'converter', 'MTDX.py')]
    print('>', ' '.join(args), flush=True)
    r = subprocess.run(args)
    if r.returncode:
        return r.returncode
    exe = os.path.join(dist, 'MTDX', 'MTDX.exe')
    print(f'built {exe} ({os.path.getsize(exe)} bytes); folder {os.path.join(dist, "MTDX")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

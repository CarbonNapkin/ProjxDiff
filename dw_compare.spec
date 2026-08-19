# PyInstaller spec for Projx Diff.
#
# Produces a single-file, windowed executable. The version metadata (macOS
# bundle CFBundleShortVersionString, Windows file-properties version block) is
# filled in from dw_compare/_version.py. Drives both Mac and Windows builds;
# PyInstaller picks the right output format for the host platform (`.exe` on
# Windows, `.app` on macOS).
#
# Usage:
#   pyinstaller dw_compare.spec --clean --noconfirm
#
# With PROJX_ONEDIR=1 in the environment, builds a onedir variant instead
# (dist/ProjxDiff-app/: ProjxDiff.exe, ProjxDiff-cli.exe on Windows, and a
# shared _internal/ — see the COLLECT below). The Windows installer
# ships THIS variant so launch does no self-extraction into %TEMP% — the
# extraction step of a onefile exe races antivirus scanners on unknown new
# binaries and loses ("Failed to load Python DLL ... _MEI...\python312.dll").
# The portable download stays onefile: one copyable file is its whole point.
#
# Optional icon files (skipped silently if absent):
#   assets/icon.ico  (Windows .exe icon)
#   assets/icon.icns (macOS .app icon)
#   assets/icon.png  (live window/taskbar icon, loaded at runtime by gui.py)

import os
import sys

# Pull the version from the single-source-of-truth module without importing the
# package. It MUST be _version.py (a literal `__version__ = '...'` assignment) —
# __init__.py only re-exports it, so scanning there never matches and the build
# would silently fall back to the default below. That was the bug behind builds
# stamped 1.0.0 while the running app reported the real version.
_version = '0.0.0'
_version_path = os.path.join(os.path.dirname(SPEC), 'dw_compare', '_version.py')
with open(_version_path, 'r', encoding='utf-8') as fh:
    for line in fh:
        if line.strip().startswith('__version__'):
            _version = line.split('=', 1)[1].strip().strip('\'"')
            break

block_cipher = None

icon_ico = os.path.join('assets', 'icon.ico')
icon_icns = os.path.join('assets', 'icon.icns')
chosen_icon = None
if sys.platform == 'win32' and os.path.isfile(icon_ico):
    chosen_icon = icon_ico
elif sys.platform == 'darwin' and os.path.isfile(icon_icns):
    chosen_icon = icon_icns

# Bundle the runtime window icon (PNG) so the live app can load it via
# gui._resource_path at start-up. Skipped silently when the asset is absent.
_datas = []
icon_png = os.path.join('assets', 'icon.png')
if os.path.isfile(icon_png):
    _datas.append((icon_png, 'assets'))

# Windows: embed a version resource so each .exe's file properties match the
# in-app version. Written in PyInstaller's version-file format.
def _write_version_resource(stem, description):
    """Write a PyInstaller version-file for one output exe. Returns its path,
    or None off Windows (macOS uses the BUNDLE info_plist below instead)."""
    if sys.platform != 'win32':
        return None
    _vt = (tuple(int(p) for p in _version.split('.') if p.isdigit()) + (0, 0, 0, 0))[:4]
    _vtext = (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(filevers=%(v)s, prodvers=%(v)s, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'Base 10 Consultants'),\n"
        "      StringStruct('FileDescription', '%(d)s'),\n"
        "      StringStruct('FileVersion', '%(s)s'),\n"
        "      StringStruct('InternalName', '%(n)s'),\n"
        "      StringStruct('OriginalFilename', '%(n)s.exe'),\n"
        "      StringStruct('ProductName', 'Projx Diff'),\n"
        "      StringStruct('ProductVersion', '%(s)s')])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    ) % {'v': _vt, 's': _version, 'n': stem, 'd': description}
    path = os.path.join(os.path.dirname(SPEC), 'build_version_info_%s.txt' % stem)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(_vtext)
    return path


exe_version = _write_version_resource('ProjxDiff', 'Projx Diff')

a = Analysis(
    ['run_dw_compare.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=['dw_compare.gui', 'dw_compare.sync', 'dw_compare.census',
                   'dw_compare.dashboard',
                   # Optional at runtime (dbsource degrades without it) but
                   # must be bundled when present so the frozen exe can do
                   # database name resolution. PyInstaller warns-not-fails
                   # when it's absent from the build env (macOS/Linux).
                   'pyodbc'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_onedir = os.environ.get('PROJX_ONEDIR') == '1'

if _onedir:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='ProjxDiff',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=chosen_icon,
        version=exe_version,
    )
    # Second exe from the same Analysis, differing only in subsystem.
    #
    # ProjxDiff.exe is windowed (console=False) so a double-click does not
    # flash a terminal -- but a windowed binary launched FROM a terminal
    # detaches immediately and writes nothing to it, so `ProjxDiff.exe
    # --doctor` printed nothing at all and looked identical to a broken
    # install. ProjxDiff-cli.exe is the console build of the same entry
    # point, the python.exe/pythonw.exe split. Unlike AttachConsole it also
    # behaves correctly when piped, redirected, or waited on.
    #
    # COLLECT takes both, so they share one _internal\ rather than shipping
    # the runtime twice.
    _cli_exes = []
    if sys.platform == 'win32':
        _cli_exes.append(EXE(
            pyz,
            a.scripts,
            [],
            exclude_binaries=True,
            name='ProjxDiff-cli',
            debug=False,
            bootloader_ignore_signals=False,
            strip=False,
            upx=True,
            upx_exclude=[],
            console=True,
            disable_windowed_traceback=False,
            target_arch=None,
            codesign_identity=None,
            entitlements_file=None,
            icon=chosen_icon,
            version=_write_version_resource('ProjxDiff-cli',
                                            'Projx Diff (command line)'),
        ))

    coll = COLLECT(
        exe,
        *_cli_exes,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='ProjxDiff-app',
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='ProjxDiff',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=chosen_icon,
        version=exe_version,
    )

if sys.platform == 'darwin' and not _onedir:
    app = BUNDLE(
        exe,
        name='ProjxDiff.app',
        icon=chosen_icon,
        bundle_identifier='com.base10consultants.projxdiff',
        info_plist={
            'CFBundleShortVersionString': _version,
            'CFBundleVersion': _version,
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
        },
    )

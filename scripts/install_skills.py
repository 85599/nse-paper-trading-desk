"""Link the twelve trader skills into the Qoder skills directory.

Windows without symlink privileges uses directory junctions, so each linked
folder still resolves to the vendor checkout that run.py imports from.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'


def skills():
    return sorted(p for p in VENDOR.glob('trader-*') if (p / 'SKILL.md').is_file())


def link(source, target):
    if target.exists() or target.is_symlink():
        if Path(os.path.realpath(str(target))) == source:
            return 'already linked'
        if any(target.iterdir()):
            return 'refused -- %s already holds files, move it away first' % target
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(source)],
                                capture_output=True, text=True)
        if result.returncode:
            return 'FAILED -- %s' % (result.stdout + result.stderr).strip()
    else:
        target.symlink_to(source, target_is_directory=True)
    return 'linked'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default=str(Path.home() / '.qoder' / 'skills'))
    args = parser.parse_args(argv)
    dest = Path(args.dest).expanduser()
    failures = 0
    for source in skills():
        status = link(source, dest / source.name)
        print('%-22s %s  ->  %s' % (source.name, status, dest / source.name))
        failures += status.startswith('FAILED') or status.startswith('refused')

    # Each SKILL.md documents its commands through ../docs/WORKFLOW.md, which from a
    # flat skills directory means <dest>/docs.
    docs = link(VENDOR / 'docs', dest / 'docs')
    print('\ndoc references  %-20s ->  %s' % (docs, dest / 'docs'))
    print('%d skills, %d problems. Restart Qoder (or start a new session) to pick them up.'
          % (len(skills()), failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())

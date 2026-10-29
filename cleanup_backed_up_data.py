"""Manually preview/remove only data covered by the verified 2026-09-30 backup.

Preview is the default. This script preserves current data and Git history.
Deletion additionally requires the Actions inputs confirming the saved backup.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time

REPOSITORY = '8858gp-create/taipower'
BRANCH = 'main'
CUTOFF = date(2026, 9, 30)
BACKUP_COMMIT = '9aced4e2e65e8c827b486aa9d26c62bb8a379250'
BACKUP_FILE_COUNT = 57548
BACKUP_ZIP_SHA256 = 'c007025bbf218420d5c084c3bd2d21895a7ec328e4388857c1f4c45c4f04505f'
FAMILIES = {'genary', 'genloadareaperc', 'loadareas', 'loadfueltype', 'loadpara_json', 'loadpara_txt'}
CONFIRMATION = 'DELETE 20260930'


def git(repo, *args, data=None, extra_env=None):
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.update(extra_env or {})
    result = subprocess.run(['git', '-C', str(repo), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed: ' + result.stderr.decode('utf-8', errors='replace'))
    return result.stdout


def data_date(path):
    parts = PurePosixPath(path).parts
    if (len(parts) < 3 or parts[0] != 'data' or parts[1] not in FAMILIES
            or '\\' in path or any(p in ('', '.', '..') for p in path.split('/'))):
        return None
    match = re.fullmatch(r'(\d{3})(\d{2})(\d{2})(?:\d{4})?\.[A-Za-z0-9]+', parts[-1])
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def read_tree(repo, revision):
    result = {}
    for record in git(repo, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if record:
            header, path = record.split(b'\t', 1)
            mode, kind, sha = header.decode('ascii').split(' ')
            result[path.decode('utf-8')] = (mode, kind, sha)
    return result


def backup_inventory(source):
    inventory = {}
    for path, entry in source.items():
        parsed = data_date(path)
        if parsed and parsed <= CUTOFF:
            if entry[0] not in ('100644', '100755') or entry[1] != 'blob':
                raise RuntimeError(f'Unexpected backup file mode: {path}')
            inventory[path] = entry
    if len(inventory) != BACKUP_FILE_COUNT:
        raise RuntimeError(f'Backup inventory mismatch: expected {BACKUP_FILE_COUNT}, got {len(inventory)}')
    return inventory


def make_plan(current, inventory):
    plan = {'delete': [], 'already_absent': [], 'changed_after_backup_keep': [], 'old_not_backed_up_keep': []}
    for path, expected in inventory.items():
        if path not in current:
            plan['already_absent'].append(path)
        elif current[path] != expected:
            plan['changed_after_backup_keep'].append(path)
        else:
            plan['delete'].append(path)
    for path in current.keys() - inventory.keys():
        parsed = data_date(path)
        if parsed and parsed <= CUTOFF:
            plan['old_not_backed_up_keep'].append(path)
    for paths in plan.values():
        paths.sort()
    plan['retained_file_count'] = len(current) - len(plan['delete'])
    return plan


def check_apply_confirmation(mode, backup_confirmed, confirmation):
    if mode == 'preview':
        return
    if not backup_confirmed or confirmation != CONFIRMATION:
        raise RuntimeError(f'Deletion requires backup_confirmed=true and confirmation="{CONFIRMATION}".')
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
            or os.environ.get('GITHUB_REF') != f'refs/heads/{BRANCH}'):
        raise RuntimeError('Deletion is restricted to a manual Actions run on the original repository main branch.')
    if CUTOFF >= datetime.now(timezone(timedelta(hours=8))).date().replace(day=1):
        raise RuntimeError('Refusing to clean the current Taiwan month or future data.')


def create_deletion_commit(repo, base, current, paths, report_dir):
    if not paths or any(data_date(p) is None or data_date(p) > CUTOFF for p in paths):
        raise RuntimeError('Empty or invalid deletion plan.')
    with tempfile.TemporaryDirectory(prefix='cleanup-index-', dir=report_dir) as temporary:
        if not Path(temporary).resolve().is_relative_to(report_dir.resolve()):
            raise RuntimeError('Unsafe temporary index path.')
        env = {'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
        git(repo, 'read-tree', base, extra_env=env)
        changes = b''.join(b'0 ' + b'0' * 40 + b'\t' + p.encode('utf-8') + b'\0' for p in paths)
        git(repo, 'update-index', '-z', '--index-info', data=changes, extra_env=env)
        tree_sha = git(repo, 'write-tree', extra_env=env).decode('ascii').strip()
    deleted = set(paths)
    expected = {p: entry for p, entry in current.items() if p not in deleted}
    if read_tree(repo, tree_sha) != expected:
        raise RuntimeError('New tree changed files outside the approved deletion set. No push attempted.')
    message = (f'cleanup: remove backed-up data through {CUTOFF}\n\n'
               f'Backup commit: {BACKUP_COMMIT}\nBackup ZIP SHA-256: {BACKUP_ZIP_SHA256}\n'
               f'Files removed: {len(paths)}\nGit history and current data are preserved.\n').encode('utf-8')
    return git(repo, '-c', 'user.name=github-actions[bot]', '-c',
               'user.email=github-actions[bot]@users.noreply.github.com',
               'commit-tree', tree_sha, '-p', base, data=message).decode('ascii').strip()


def save_report(report_dir, report):
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'cleanup_plan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    counts = {key: len(report[key]) for key in ('delete', 'already_absent', 'changed_after_backup_keep', 'old_not_backed_up_keep')}
    print(json.dumps({k: v for k, v in report.items() if k not in counts} | counts, ensure_ascii=False, indent=2))
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        text = (f'## 歷史資料清理：{report["status"]}\n\n'
                f'- 專案：`{REPOSITORY}`；截止：**{CUTOFF}**（含當日）\n'
                f'- 與已下載備份一致：**{counts["delete"]:,}** 個檔案\n'
                f'- 已不存在：{counts["already_absent"]:,}\n'
                f'- 備份後改過，保留：{counts["changed_after_backup_keep"]:,}\n'
                f'- 備份未包含的舊檔，保留：{counts["old_not_backed_up_keep"]:,}\n'
                f'- 保留檔案：{report["retained_file_count"]:,}（含10月起資料與程式）\n\n'
                '完整路徑清單請下載本次執行的 cleanup-plan 成品。\n\n'
                '**一般刪除提交保留 Git 歷史，不會立即釋放歷史儲存容量。**\n')
        if report.get('cleanup_commit'):
            text += f'\n清理提交：https://github.com/{REPOSITORY}/commit/{report["cleanup_commit"]}\n'
        with open(summary_path, 'a', encoding='utf-8') as summary:
            summary.write(text)


def run(repo, mode, backup_confirmed, confirmation, report_dir):
    check_apply_confirmation(mode, backup_confirmed, confirmation)
    origin = git(repo, 'remote', 'get-url', 'origin').decode('utf-8').strip()
    if origin not in (f'https://github.com/{REPOSITORY}', f'https://github.com/{REPOSITORY}.git', f'git@github.com:{REPOSITORY}.git'):
        raise RuntimeError('Unexpected origin repository; refusing to continue.')
    git(repo, 'fetch', '--filter=blob:none', '--no-tags', '--depth=1', 'origin', BACKUP_COMMIT)
    inventory = backup_inventory(read_tree(repo, BACKUP_COMMIT))
    for attempt in range(1, 4):
        git(repo, 'fetch', '--filter=blob:none', '--no-tags', '--depth=1', 'origin', BRANCH)
        base = git(repo, 'rev-parse', 'FETCH_HEAD').decode('ascii').strip()
        current = read_tree(repo, base)
        plan = make_plan(current, inventory)
        report = {'repository': REPOSITORY, 'mode': mode, 'status': 'preview', 'cutoff_inclusive': str(CUTOFF),
                  'base_commit': base, 'backup_commit': BACKUP_COMMIT, 'backup_zip_sha256': BACKUP_ZIP_SHA256, **plan}
        save_report(report_dir, report)
        if mode == 'preview' or not plan['delete']:
            print('No remote changes made.')
            return report
        check_apply_confirmation(mode, backup_confirmed, confirmation)
        commit = create_deletion_commit(repo, base, current, plan['delete'], report_dir)
        try:
            # Normal fast-forward push only. A concurrent crawler commit cannot be overwritten.
            git(repo, 'push', 'origin', f'{commit}:refs/heads/{BRANCH}')
        except RuntimeError:
            remote = git(repo, 'ls-remote', 'origin', f'refs/heads/{BRANCH}').decode('ascii').split()[0]
            if remote == base or attempt == 3:
                raise
            print('main advanced; recalculating against the latest branch without force-pushing.')
            time.sleep(2)
            continue
        report.update(status='deleted', cleanup_commit=commit)
        save_report(report_dir, report)
        return report
    raise RuntimeError('Could not finish safely; no forced push was performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preview', 'delete'), default=os.environ.get('CLEANUP_MODE', 'preview'))
    args = parser.parse_args()
    repo = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode('utf-8').strip())
    report_dir = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'taipower-cleanup-reports'
    run(repo, args.mode, os.environ.get('BACKUP_CONFIRMED', '').lower() == 'true',
        os.environ.get('CONFIRMATION_TEXT', ''), report_dir)


if __name__ == '__main__':
    main()

"""scripts/pre-push: scan the tip tree in full, but only commits no remote-tracking ref has.

After a rebase or merge of main the pushed range ``rsha..lsha`` holds commits that are already
public on the remote. They cannot be changed and add no exposure, so the hook must not scan them.
The scanner file is a fake with one harmless pattern, so no real personal data is needed here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "pre-push"
ZERO = "0" * 40
BAD = "zorkmid"  # what the fake scanner treats as personal data


@pytest.fixture
def env(tmp_path):
    scan = tmp_path / "scan.py"
    scan.write_text('PATS = {"personal": r"zorkmid"}\n')
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update(
        HERMCP_SCAN_FILE=str(scan),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        HOME=str(tmp_path),
    )
    return e


class Repo:
    def __init__(self, path: Path, env: dict):
        self.path, self.env = path, env

    def git(self, *a, who="ok", **kw):
        e = {**self.env, "GIT_AUTHOR_NAME": who, "GIT_COMMITTER_NAME": who,
             "GIT_AUTHOR_EMAIL": "x@example.com", "GIT_COMMITTER_EMAIL": "x@example.com"}
        return subprocess.run(["git", *a], cwd=self.path, env=e, check=True,
                              capture_output=True, text=True, **kw).stdout.strip()

    def commit(self, name="f.txt", text="clean\n", msg="c", who="ok"):
        (self.path / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", msg, who=who)
        return self.git("rev-parse", "HEAD")

    def hook(self, lsha, rsha=ZERO):
        line = f"refs/heads/b {lsha} refs/heads/b {rsha}\n"
        return subprocess.run([sys.executable, str(HOOK)], cwd=self.path, env=self.env,
                              input=line, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, env):
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "remote.git")], env=env, check=True)
    work = tmp_path / "work"
    work.mkdir()
    r = Repo(work, env)
    r.git("init", "-q", "-b", "main")
    r.git("remote", "add", "origin", str(tmp_path / "remote.git"))
    return r


def publish(r: Repo):
    r.git("push", "-q", "origin", "main")  # also updates refs/remotes/origin/main


def test_new_commit_with_identity_is_blocked(repo):
    base = repo.commit()
    publish(repo)
    new = repo.commit("g.txt", msg="n", who=BAD)
    res = repo.hook(new, base)
    assert res.returncode == 1
    assert "(commit metadata)" in res.stderr and "[personal]" in res.stderr


def test_new_commit_trailer_is_blocked(repo):
    base = repo.commit()
    publish(repo)
    new = repo.commit("g.txt", msg=f"n\n\nCo-Authored-By: {BAD} <x@example.com>")
    assert repo.hook(new, base).returncode == 1


def test_already_public_commit_with_identity_is_not_blocked(repo):
    root = repo.commit()
    publish(repo)
    public = repo.commit("g.txt", msg="public", who=BAD)  # e.g. squash commit from a merged PR
    publish(repo)
    repo.git("checkout", "-q", "-b", "b", public)
    tip = repo.commit("h.txt", msg="mine")
    # rsha is an old tip, so rsha..lsha contains the public commit
    res = repo.hook(tip, root)
    assert res.returncode == 0, res.stderr
    assert "scan clean" in res.stdout


def test_new_ref_scans_only_unpublished_commits(repo):
    repo.commit()
    publish(repo)
    repo.commit("g.txt", who=BAD)
    publish(repo)
    tip = repo.commit("h.txt")
    assert repo.hook(tip).returncode == 0


def test_tree_finding_is_still_blocked(repo):
    root = repo.commit()
    publish(repo)
    public = repo.commit("leak.txt", text=f"{BAD}\n", msg="public leak")
    publish(repo)  # already public, but the tip tree is scanned regardless
    tip = repo.commit("h.txt")
    res = repo.hook(tip, root)
    assert res.returncode == 1
    assert "leak.txt" in res.stderr and "tree:" in res.stderr
    assert public != tip

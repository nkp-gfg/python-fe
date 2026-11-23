"""Spec for the Sabre credential template.

``sabre-webservices/.env.example`` tells a developer that this file is
gitignored and is a safe place for real Sabre credentials. The root ignore
rules ignore ``.env`` and ``.env.*``, then re-include ``.env.example``, and
the template is already tracked. A developer who follows the comment and
commits therefore publishes the username and password that the Bruno session
requests send to Sabre.

These tests fail while that invitation is in the tracked template. They pass
once the template no longer says this file is gitignored or safe for real
credentials, keeps only empty placeholders, and leaves real credentials to
the untracked ``.env`` file.

The tests use an obvious placeholder. They do not contain a real credential.

Run from the repository root:

    python3 -m unittest tests/test_sabre_credential_template.py
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = Path("sabre-webservices/.env.example")
LOCAL_ENVS = (Path(".env"), Path("sabre-webservices/.env"))
PLACEHOLDER_USERNAME = "placeholder-username"
PLACEHOLDER_PASSWORD = "placeholder-password"
ALLOWED_PLACEHOLDERS = {
    "",
    "your_username_here",
    "your_password_here",
    "your_username",
    "your_password",
    "changeme",
    "placeholder",
    "placeholder-username",
    "placeholder-password",
    "todo",
    "xxx",
    "<username>",
    "<password>",
}


def _comment_text(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    return stripped.lstrip("#").strip()


def comment_invites_credentials_in_this_file(text: str) -> list[str]:
    """Return comment lines that call this file a safe or ignored secret store.

    Guidance that tells the reader to copy this template to ``.env`` and to
    put credentials only in that other file is not an invitation.
    """
    hits: list[str] = []
    for line in text.splitlines():
        body = _comment_text(line)
        if body is None:
            continue
        low = body.lower()
        refers_to_this = bool(
            re.search(r"\bthis file\b|\bthis template\b|\.env\.example\b", low)
        )
        refers_to_dotenv = bool(re.search(r"(?<![\w.])\.env(?!\.)\b", low))
        copy_to_dotenv = bool(re.search(r"copy this file to \.env\b", low))
        ignored_claim = bool(
            re.search(
                r"gitignor|ignored by git|will not be committed|"
                r"won't be committed|not committed|not tracked",
                low,
            )
        )
        safe_claim = bool(
            re.search(
                r"safe to store|safe for (?:real |your )?(?:credential|password|secret)",
                low,
            )
        )
        negated = bool(re.search(r"\b(do not|don't|never|must not)\b", low))
        if negated and not ignored_claim and not safe_claim:
            continue
        if copy_to_dotenv and not safe_claim and not (ignored_claim and refers_to_this):
            continue
        if refers_to_this and (ignored_claim or safe_claim):
            hits.append(body)
            continue
        if safe_claim and not refers_to_dotenv:
            hits.append(body)
            continue
        if ignored_claim and refers_to_this and not refers_to_dotenv:
            hits.append(body)
    return hits


def assignment_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        capture_output=True,
    )


def _publish(repo: Path, hooks: Path) -> None:
    _git(repo, "add", "-A")
    status = _git(repo, "status", "--porcelain")
    if not status.stdout.strip():
        return
    _git(
        repo,
        "-c",
        "user.email=dev@example.com",
        "-c",
        "user.name=Dev",
        "-c",
        f"core.hooksPath={hooks}",
        "commit",
        "-m",
        "developer commit",
    )


def _tracked(repo: Path) -> set[str]:
    listed = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    return {line for line in listed.stdout.splitlines() if line}


def _blob(repo: Path, rel: Path) -> str:
    shown = _git(repo, "show", f"HEAD:{rel.as_posix()}")
    return shown.stdout


def _write_local_credentials(repo: Path) -> None:
    body = (
        f"SABRE_USERNAME={PLACEHOLDER_USERNAME}\n"
        f"SABRE_PASSWORD={PLACEHOLDER_PASSWORD}\n"
    )
    for rel in LOCAL_ENVS:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _fill_template(path: Path) -> None:
    text = path.read_text()
    updated = re.sub(
        r"(?m)^(SABRE_USERNAME)=.*$",
        rf"\1={PLACEHOLDER_USERNAME}",
        text,
        count=1,
    )
    updated = re.sub(
        r"(?m)^(SABRE_PASSWORD)=.*$",
        rf"\1={PLACEHOLDER_PASSWORD}",
        updated,
        count=1,
    )
    if updated == text:
        raise AssertionError(
            f"{path} has no SABRE_USERNAME and SABRE_PASSWORD assignments to fill in"
        )
    path.write_text(updated)


class SabreCredentialTemplateTests(unittest.TestCase):
    def test_comment_does_not_say_this_file_is_gitignored_or_safe_for_credentials(self):
        text = (REPO / TEMPLATE).read_text()
        invites = comment_invites_credentials_in_this_file(text)
        self.assertEqual(
            invites,
            [],
            "The tracked Sabre template invites real credentials into a file "
            f"Git publishes: {invites}",
        )

    def test_following_the_safe_credential_comment_does_not_publish_the_password(self):
        text = (REPO / TEMPLATE).read_text()
        invites = comment_invites_credentials_in_this_file(text)
        with tempfile.TemporaryDirectory(prefix="sabre-template-") as tmp:
            clone = Path(tmp) / "repo"
            hooks = Path(tmp) / "hooks"
            hooks.mkdir()
            _git(REPO, "clone", "--local", "--no-hardlinks", "--quiet", str(REPO), str(clone))
            destination = clone / TEMPLATE
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Evaluate the working tree under test, including an uncommitted fix.
            destination.write_text(text)
            gitignore = REPO / ".gitignore"
            if gitignore.exists():
                (clone / ".gitignore").write_text(gitignore.read_text())
            decision = _git(
                clone,
                "check-ignore",
                "-v",
                "--no-index",
                "--",
                TEMPLATE.as_posix(),
                check=False,
            )
            if invites:
                _fill_template(destination)
                _write_local_credentials(clone)
                _publish(clone, hooks)
                tracked = _tracked(clone)
                published = _blob(clone, TEMPLATE) if TEMPLATE.as_posix() in tracked else ""
                self.assertNotIn(
                    PLACEHOLDER_PASSWORD,
                    published,
                    "Filling in the template the comment calls gitignored and "
                    "safe published the password. "
                    f"check-ignore: {decision.stdout.strip() or decision.stderr.strip()}. "
                    f"tracked env paths: {sorted(name for name in tracked if '.env' in name)}",
                )
                for rel in LOCAL_ENVS:
                    self.assertNotIn(
                        rel.as_posix(),
                        tracked,
                        f"{rel.as_posix()} is the local credential file and was published",
                    )
                return

            values = assignment_values(text)
            self.assertIn("SABRE_USERNAME", values)
            self.assertIn("SABRE_PASSWORD", values)
            unexpected = {
                key: value
                for key, value in values.items()
                if key in {"SABRE_USERNAME", "SABRE_PASSWORD"}
                and value not in ALLOWED_PLACEHOLDERS
            }
            self.assertEqual(
                unexpected,
                {},
                "The tracked template must keep empty placeholders",
            )
            _write_local_credentials(clone)
            _publish(clone, hooks)
            tracked = _tracked(clone)
            for rel in LOCAL_ENVS:
                self.assertNotIn(rel.as_posix(), tracked)
            if TEMPLATE.as_posix() in tracked:
                self.assertNotIn(PLACEHOLDER_PASSWORD, _blob(clone, TEMPLATE))

    def test_claim_detector_accepts_guidance_that_points_at_the_untracked_env_file(self):
        self.assertEqual(
            comment_invites_credentials_in_this_file(
                "# This file is gitignored - safe to store your credentials\n"
            ),
            ["This file is gitignored - safe to store your credentials"],
        )
        self.assertEqual(
            comment_invites_credentials_in_this_file(
                "# Copy this file to .env and fill in your actual credentials\n"
                "# .env is gitignored. Only that untracked file may hold the "
                "Sabre username and password.\n"
                "# Do not store real credentials in this file.\n"
            ),
            [],
        )


if __name__ == "__main__":
    os.chdir(REPO)
    unittest.main(verbosity=2)

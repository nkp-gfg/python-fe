"""Containment tests for UI skill design-system persistence.

The documented persist command takes a project name and a page name and writes
markdown under design-system/. These tests fail while those names are joined
into the output path after replacing spaces only: a parent segment, an absolute
path, or a symlink at the joined location can create or overwrite a file
outside that directory. A passing run keeps every file and directory outside
design-system/ unchanged, including when a check of the lexical path would
still open a different file.

Copies exercised:
- .gemini/skills/ui-ux-pro-max/scripts/search.py and design_system.py
- .github/prompts/ui-ux-pro-max/scripts/search.py and design_system.py
- .kiro/steering/ui-ux-pro-max/scripts/search.py and design_system.py

design_system.py's own CLI does not pass persist=True, so it is not a write path.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (
    REPO / ".gemini" / "skills" / "ui-ux-pro-max",
    REPO / ".github" / "prompts" / "ui-ux-pro-max",
    REPO / ".kiro" / "steering" / "ui-ux-pro-max",
)
SENTINEL = b"SENTINEL-DO-NOT-OVERWRITE\n"
SAFE_PROJECT = "Serenity Spa"
SAFE_PAGE = "dashboard"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def outside_state(scan_root: Path, allowed: Path):
    """Files and directories under scan_root whose resolved path is outside allowed.

    Resolution is what the process opens. A lexical prefix such as
    design-system-evil matches design-system and is still outside.
    """
    files = {}
    dirs = set()
    for dirpath, dirnames, filenames in os.walk(scan_root, followlinks=False):
        for dirname in dirnames:
            path = Path(dirpath) / dirname
            resolved = path.resolve()
            if not _contained(resolved, allowed):
                dirs.add(str(resolved))
        for filename in filenames:
            path = Path(dirpath) / filename
            resolved = path.resolve()
            if not _contained(resolved, allowed):
                files[str(resolved)] = path.read_bytes()
    return files, dirs


def load_design_system(skill_root: Path):
    scripts = skill_root / "scripts"
    sys.path.insert(0, str(scripts))
    sys.modules.pop("core", None)
    module_name = "design_system_" + "_".join(
        part.replace(".", "") for part in skill_root.relative_to(REPO).parts
    )
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, scripts / "design_system.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def design_payload(project_name: str) -> dict:
    return {
        "project_name": project_name,
        "category": "General",
        "pattern": {
            "name": "Hero",
            "sections": "Hero > Features",
            "cta_placement": "Above fold",
            "color_strategy": "",
            "conversion": "",
        },
        "style": {"name": "Minimalism", "keywords": "", "best_for": "", "effects": ""},
        "colors": {},
        "typography": {},
        "key_effects": "",
        "anti_patterns": "",
    }


class DesignSystemPersistPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for root in SKILL_ROOTS:
            if not (root / "scripts" / "search.py").is_file():
                raise AssertionError(f"missing skill entry: {root}")
            if not (root / "scripts" / "design_system.py").is_file():
                raise AssertionError(f"missing persist module: {root}")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-persist-")
        self.scan_root = Path(self._tmp.name)
        identity = str(self.scan_root)
        if identity != identity.lower() or " " in identity:
            self.fail(f"temp path is not safe to use as a lowercased slug: {identity}")
        self._reset_tree()

    def _reset_tree(self):
        """Restore sentinels so one escaped write cannot mask the next."""
        if self.scan_root.exists():
            shutil.rmtree(self.scan_root)
        self.scan_root.mkdir()
        self.workspace = self.scan_root / "workspace"
        self.workspace.mkdir()
        self.allowed = self.workspace / "design-system"
        for relative in (
            "workspace/AGENTS.md",
            "workspace/agents.md",
            "workspace/readme.md",
            "agents.md",
            "AGENTS.md",
        ):
            path = self.scan_root / relative
            path.write_bytes(SENTINEL)
        self.sibling = self.workspace / "design-system-evil"
        self.sibling.mkdir()
        (self.sibling / "note.md").write_bytes(SENTINEL)

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self):
        return outside_state(self.scan_root, self.allowed)

    def _assert_outside_unchanged(self, before, label):
        files, dirs = self._snapshot()
        before_files, before_dirs = before
        self.assertEqual(
            before_dirs,
            dirs,
            f"{label}: directories outside design-system changed",
        )
        self.assertEqual(
            set(before_files),
            set(files),
            f"{label}: files outside design-system were created or removed",
        )
        for path, content in before_files.items():
            self.assertEqual(
                files[path],
                content,
                f"{label}: file outside design-system was modified: {path}",
            )

    def _call_contained(self, label, fn):
        before = self._snapshot()
        try:
            fn()
        except AssertionError:
            raise
        except Exception:
            # A refusal is containment only when nothing outside was created,
            # deleted, or modified first.
            pass
        self._assert_outside_unchanged(before, label)

    def _run_search(self, skill_root: Path, args):
        proc = subprocess.run(
            [sys.executable, str(skill_root / "scripts" / "search.py"), *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        combined = (proc.stdout + "\n" + proc.stderr).lower()
        if proc.returncode != 0:
            self.assertNotIn("unrecognized arguments", combined, proc.stderr)
            self.assertNotIn("modulenotfounderror", combined, proc.stderr)
        return proc

    def test_cli_page_parent_segments_stay_inside_every_skill_copy(self):
        """Documented --persist --page command, with the working directory as the base."""
        for skill_root in SKILL_ROOTS:
            for page in (
                "../../../readme",
                "dashboard/../../../../readme",
                "../../../../AGENTS",
            ):
                label = f"{skill_root.relative_to(REPO)} page={page!r}"
                with self.subTest(label=label):
                    self._reset_tree()
                    # A missing intermediate directory makes open() fail before `..`
                    # is applied. Create it so an embedded parent segment is tested.
                    if page.startswith("dashboard/"):
                        (
                            self.allowed / "injected-project-string" / "pages" / "dashboard"
                        ).mkdir(parents=True)
                    self._call_contained(
                        label,
                        lambda page=page, skill_root=skill_root: self._run_search(
                            skill_root,
                            [
                                "saas dashboard",
                                "--design-system",
                                "--persist",
                                "-p",
                                "INJECTED-PROJECT-STRING",
                                "--page",
                                page,
                            ],
                        ),
                    )

    def test_cli_absolute_and_parent_project_names_stay_inside_every_skill_copy(self):
        absolute_project = str(self.scan_root / "absolute-project")
        for skill_root in SKILL_ROOTS:
            cases = (
                ("parent-project", "../../escaped"),
                ("prefix-sibling", "../design-system-evil"),
                ("absolute-project", absolute_project),
                ("query-as-project", None),
            )
            for name, project in cases:
                label = f"{skill_root.relative_to(REPO)} {name}"
                with self.subTest(label=label):
                    self._reset_tree()
                    if project is None:
                        args = ["../../from-query", "--design-system", "--persist"]
                    else:
                        args = [
                            "saas dashboard",
                            "--design-system",
                            "--persist",
                            "-p",
                            project,
                        ]
                    self._call_contained(
                        label,
                        lambda args=args, skill_root=skill_root: self._run_search(
                            skill_root, args
                        ),
                    )

    def test_cli_output_dir_does_not_change_page_containment(self):
        """--output-dir selects the base. The page name is still not a path above it."""
        for skill_root in SKILL_ROOTS:
            label = f"{skill_root.relative_to(REPO)} output-dir"
            with self.subTest(label=label):
                self._reset_tree()
                self._call_contained(
                    label,
                    lambda skill_root=skill_root: self._run_search(
                        skill_root,
                        [
                            "saas dashboard",
                            "--design-system",
                            "--persist",
                            "-p",
                            "INJECTED-PROJECT-STRING",
                            "--page",
                            "../../../readme",
                            "--output-dir",
                            str(self.workspace),
                        ],
                    ),
                )

    def test_persist_function_rejects_path_names_on_every_copy(self):
        absolute_page = str(self.sibling / "note")
        absolute_project = str(self.scan_root / "absolute-project")
        pages = (
            "../../../readme",
            "dashboard/../../../../readme",
            "../../../../AGENTS",
            absolute_page,
        )
        projects = (
            "../../escaped",
            "../design-system-evil",
            absolute_project,
        )
        for skill_root in SKILL_ROOTS:
            module = load_design_system(skill_root)
            for page in pages:
                label = f"{skill_root.relative_to(REPO)} persist page={page!r}"
                with self.subTest(label=label):
                    self._reset_tree()
                    if page.startswith("dashboard/"):
                        (
                            self.allowed / "injected-project-string" / "pages" / "dashboard"
                        ).mkdir(parents=True)
                    self._call_contained(
                        label,
                        lambda module=module, page=page: module.persist_design_system(
                            design_payload("INJECTED-PROJECT-STRING"),
                            page=page,
                            output_dir=str(self.workspace),
                            page_query="dashboard",
                        ),
                    )
            for project in projects:
                label = f"{skill_root.relative_to(REPO)} persist project={project!r}"
                with self.subTest(label=label):
                    self._reset_tree()
                    self._call_contained(
                        label,
                        lambda module=module, project=project: module.persist_design_system(
                            design_payload(project),
                            page=None,
                            output_dir=str(self.workspace),
                        ),
                    )

    def test_generate_design_system_forwards_unsanitized_names_on_every_copy(self):
        """search.py calls generate_design_system, which copies the project name through."""
        for skill_root in SKILL_ROOTS:
            module = load_design_system(skill_root)
            label = f"{skill_root.relative_to(REPO)} generate page"
            with self.subTest(label=label):
                self._reset_tree()
                previous = os.getcwd()
                os.chdir(self.workspace)
                try:
                    self._call_contained(
                        label,
                        lambda module=module: module.generate_design_system(
                            "saas dashboard",
                            "INJECTED-PROJECT-STRING",
                            persist=True,
                            page="../../../readme",
                        ),
                    )
                finally:
                    os.chdir(previous)
            label = f"{skill_root.relative_to(REPO)} generate query"
            with self.subTest(label=label):
                self._reset_tree()
                self._call_contained(
                    label,
                    lambda module=module: module.generate_design_system(
                        "../../from-query",
                        None,
                        persist=True,
                        output_dir=str(self.workspace),
                    ),
                )

    def test_symlink_target_is_the_file_that_must_stay_unchanged(self):
        """A safe slug still escapes when the joined path is a symlink to the outside.

        Checking the name for parent segments, then opening the joined path,
        writes the symlink target. The resolved target is the file that must
        stay unchanged.
        """
        for skill_root in SKILL_ROOTS:
            module = load_design_system(skill_root)
            tag = "".join(ch for ch in skill_root.parents[1].name if ch.isalnum())
            with self.subTest(skill=str(skill_root.relative_to(REPO)), kind="project-dir"):
                self._reset_tree()
                outside = self.scan_root / f"linked-project-{tag}"
                outside.mkdir()
                (outside / "MASTER.md").write_bytes(SENTINEL)
                self.allowed.mkdir(parents=True, exist_ok=True)
                link = self.allowed / f"link{tag}"
                link.symlink_to(outside, target_is_directory=True)
                project_name = f"Link{tag}"
                self._call_contained(
                    "project symlink",
                    lambda module=module, project_name=project_name: module.persist_design_system(
                        design_payload(project_name),
                        page=None,
                        output_dir=str(self.workspace),
                    ),
                )
            with self.subTest(skill=str(skill_root.relative_to(REPO)), kind="page-file"):
                self._reset_tree()
                pages = self.allowed / f"page{tag}" / "pages"
                pages.mkdir(parents=True, exist_ok=True)
                target = self.scan_root / f"linked-page-{tag}.md"
                target.write_bytes(SENTINEL)
                (pages / "dashboard.md").symlink_to(target)
                project_name = f"Page{tag}"
                self._call_contained(
                    "page symlink",
                    lambda module=module, project_name=project_name: module.persist_design_system(
                        design_payload(project_name),
                        page="dashboard",
                        output_dir=str(self.workspace),
                        page_query="dashboard",
                    ),
                )

    def test_safe_slug_persists_inside_design_system(self):
        self._reset_tree()
        module = load_design_system(SKILL_ROOTS[0])
        before = self._snapshot()
        result = module.persist_design_system(
            design_payload(SAFE_PROJECT),
            page=SAFE_PAGE,
            output_dir=str(self.workspace),
            page_query="dashboard",
        )
        self._assert_outside_unchanged(before, "safe slug")
        master = self.allowed / "serenity-spa" / "MASTER.md"
        page = self.allowed / "serenity-spa" / "pages" / "dashboard.md"
        self.assertTrue(master.is_file(), result)
        self.assertTrue(page.is_file(), result)
        self.assertIn(SAFE_PROJECT, master.read_text(encoding="utf-8"))
        self.assertIn(SAFE_PROJECT, page.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()

import os

import yaml
from loguru import logger

SKILL_SCAN_DIRS = [".claude/skills", ".agents/skills", ".od-skills", ".codex/skills"]


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}


class SkillsService:
    def __init__(self) -> None:
        self._skills: dict[str, dict] = {}
        self._discover()

    def _discover(self) -> None:
        home = os.path.expanduser("~")
        for rel_dir in SKILL_SCAN_DIRS:
            base = os.path.join(home, rel_dir)
            if not os.path.isdir(base):
                continue
            self._scan_dir(base, scope="user")

        cwd = os.getcwd()
        for rel_dir in SKILL_SCAN_DIRS:
            base = os.path.join(cwd, rel_dir)
            if not os.path.isdir(base):
                continue
            self._scan_dir(base, scope="project")

    def _scan_dir(self, base: str, scope: str) -> None:
        try:
            entries = os.scandir(base)
        except PermissionError:
            return

        for entry in entries:
            if not entry.is_dir() or entry.name in _SKIP_DIRS:
                continue
            skill_md = os.path.join(entry.path, "SKILL.md")
            if os.path.isfile(skill_md):
                self._load_skill(skill_md, scope)

    def _load_skill(self, skill_md_path: str, scope: str) -> None:
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            logger.warning(f"Skills: cannot read {skill_md_path}: {e}")
            return

        name, description, body = self._parse_skill_md(raw, skill_md_path)
        if not name or not description:
            return

        if name in self._skills:
            existing_scope = self._skills[name]["scope"]
            if existing_scope == "project" and scope == "project":
                logger.warning(
                    f"Skills: collision for '{name}' — keeping first found, "
                    f"shadowing {skill_md_path}"
                )
                return
            elif scope == "project":
                logger.debug(
                    f"Skills: project skill '{name}' overrides user-level skill"
                )
            else:
                return

        self._skills[name] = {
            "name": name,
            "description": description,
            "location": skill_md_path,
            "body": body,
            "scope": scope,
        }
        logger.debug(f"Skills: loaded '{name}' from {skill_md_path}")

    def _parse_skill_md(self, raw: str, path: str):
        if not raw.startswith("---"):
            logger.warning(f"Skills: no frontmatter in {path}, skipping")
            return None, None, ""

        end_idx = raw.find("---", 3)
        if end_idx == -1:
            logger.warning(f"Skills: unclosed frontmatter in {path}, skipping")
            return None, None, ""

        yaml_block = raw[3:end_idx].strip()
        body = raw[end_idx + 3 :].strip()

        frontmatter = self._parse_yaml_lenient(yaml_block, path)
        if frontmatter is None:
            return None, None, ""

        name = frontmatter.get("name", "") or ""
        description = frontmatter.get("description", "") or ""

        if not name:
            dir_name = os.path.basename(os.path.dirname(path))
            logger.warning(
                f"Skills: missing name in {path}, using directory name '{dir_name}'"
            )
            name = dir_name

        if not description:
            logger.warning(f"Skills: missing description in {path}, skipping")
            return None, None, ""

        if len(name) > 64:
            logger.warning(f"Skills: name '{name}' exceeds 64 chars, truncating")
            name = name[:64]

        return name, description, body

    def _parse_yaml_lenient(self, yaml_block: str, path: str) -> dict | None:
        try:
            return yaml.safe_load(yaml_block) or {}
        except yaml.YAMLError:
            pass

        fixed_lines = []
        for line in yaml_block.splitlines():
            if ":" in line:
                key_end = line.index(":")
                key = line[:key_end]
                value = line[key_end + 1 :].strip()
                if ":" in value and not (value.startswith(('"', "'"))):
                    value = f'"{value}"'
                    fixed_lines.append(f"{key}: {value}")
                    continue
            fixed_lines.append(line)

        try:
            return yaml.safe_load("\n".join(fixed_lines)) or {}
        except yaml.YAMLError as e:
            logger.warning(f"Skills: unparseable YAML in {path}: {e}, skipping")
            return None

    def has_skills(self) -> bool:
        return len(self._skills) > 0

    def get_catalog(self) -> list[dict]:
        return [
            {"name": s["name"], "description": s["description"]}
            for s in self._skills.values()
        ]

    def get_skill(self, name: str) -> dict | None:
        return self._skills.get(name)

    def refresh(self) -> bool:
        """Re-discover skills from filesystem.

        Returns:
            True if the set of skill names changed, False otherwise.
        """
        old_names = set(self._skills.keys())
        self._skills = {}
        self._discover()
        new_names = set(self._skills.keys())
        return old_names != new_names

    def get_skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def list_resources(self, skill_dir: str) -> list[str]:
        resources = []
        try:
            for entry in os.scandir(skill_dir):
                if entry.is_file() and entry.name != "SKILL.md":
                    resources.append(entry.name)
                elif entry.is_dir() and entry.name not in _SKIP_DIRS:
                    for sub_entry in os.scandir(entry.path):
                        if sub_entry.is_file():
                            resources.append(os.path.join(entry.name, sub_entry.name))
        except OSError:
            pass
        return resources

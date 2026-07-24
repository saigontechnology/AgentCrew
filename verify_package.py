import sys
import tarfile
import zipfile
from pathlib import Path


def check_wheel_files(wheel_path):
    """Check if the wheel file contains expected files and directories."""
    required_dirs = [
        "AgentCrew",
        "AgentCrew/modules",
        "AgentCrew/modules/chat",
        "AgentCrew/modules/gui",
        "AgentCrew/modules/memory",
    ]

    required_files = [
        "AgentCrew/__init__.py",
        "AgentCrew/main.py",
    ]

    found_dirs = set()
    found_files = set()

    with zipfile.ZipFile(wheel_path, "r") as wheel:
        for file_info in wheel.filelist:
            path = file_info.filename

            if path.endswith("/"):
                # It's a directory
                path = path.rstrip("/")
                for req_dir in required_dirs:
                    if path.endswith((req_dir, req_dir.replace("/", "-"))):
                        found_dirs.add(req_dir)
            else:
                # It's a file
                for req_file in required_files:
                    if path.endswith(req_file.replace("/", "-")):
                        found_files.add(req_file)

    missing_dirs = set(required_dirs) - found_dirs
    missing_files = set(required_files) - found_files

    return not missing_dirs and not missing_files, missing_dirs, missing_files


def check_sdist_files(sdist_path):
    """Check if the source distribution contains expected files and directories."""
    required_dirs = [
        "AgentCrew",
        "AgentCrew/modules",
    ]

    required_files = [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "MANIFEST.in",
        "AgentCrew/__init__.py",
        "AgentCrew/main.py",
    ]

    found_dirs = set()
    found_files = set()

    with tarfile.open(sdist_path, "r:gz") as sdist:
        package_prefix = None
        for member in sdist.getmembers():
            path = member.name

            # Find the package prefix (usually "package-name-version/")
            if package_prefix is None and "/" in path:
                package_prefix = path.split("/")[0] + "/"

            if package_prefix and path.startswith(package_prefix):
                rel_path = path[len(package_prefix) :]

                if member.isdir():
                    for req_dir in required_dirs:
                        if rel_path == req_dir or rel_path == req_dir + "/":
                            found_dirs.add(req_dir)
                else:
                    for req_file in required_files:
                        if rel_path == req_file:
                            found_files.add(req_file)

    missing_dirs = set(required_dirs) - found_dirs
    missing_files = set(required_files) - found_files

    return not missing_dirs and not missing_files, missing_dirs, missing_files


def main():
    """Main entry point for package verification."""
    # Check if dist/ directory exists
    dist_dir = Path("dist")
    if not dist_dir.exists() or not dist_dir.is_dir():
        print("Error: 'dist' directory not found. Run 'uv build' first.")
        return 1

    # Find wheel files
    wheel_files = list(dist_dir.glob("*.whl"))
    if not wheel_files:
        print("Error: No wheel files found in 'dist' directory.")
        return 1

    # Find sdist files
    sdist_files = list(dist_dir.glob("*.tar.gz"))
    if not sdist_files:
        print("Error: No source distribution files found in 'dist' directory.")
        return 1

    # Check wheel file
    # wheel_ok, missing_wheel_dirs, missing_wheel_files = check_wheel_files(wheel_files[0])
    # if wheel_ok:
    #     print(f"✅ Wheel file {wheel_files[0].name} contains all required files")
    # else:
    #     print(f"❌ Wheel file {wheel_files[0].name} is missing files/directories:")
    #     if missing_wheel_dirs:
    #         print("  Missing directories:", ", ".join(missing_wheel_dirs))
    #     if missing_wheel_files:
    #         print("  Missing files:", ", ".join(missing_wheel_files))

    # Check sdist file
    sdist_ok, missing_sdist_dirs, missing_sdist_files = check_sdist_files(
        sdist_files[0]
    )
    if sdist_ok:
        print(
            f"✅ Source distribution {sdist_files[0].name} contains all required files"
        )
    else:
        print(
            f"❌ Source distribution {sdist_files[0].name} is missing files/directories:"
        )
        if missing_sdist_dirs:
            print("  Missing directories:", ", ".join(missing_sdist_dirs))
        if missing_sdist_files:
            print("  Missing files:", ", ".join(missing_sdist_files))

    # Overall result
    if sdist_ok:
        print("✅ All package checks passed!")
        return 0
    else:
        print("❌ Some package checks failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())

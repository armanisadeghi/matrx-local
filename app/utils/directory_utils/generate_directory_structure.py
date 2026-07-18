import os
import json
import re
import time
from io import StringIO
from pathlib import Path
from app.config import BASE_DIR, TEMP_DIR
from app.common.fancy_prints import print_link


def generate_directory_structure(root_dir, ignore_dirs=None, include_dirs=None, ignore_files=None, include_files=None, ignore_extensions=None, include_extensions=None, include_files_override=True, include_text_output=False, text_output_file=None):
    ignore_dirs = ignore_dirs or []
    include_dirs = include_dirs or []
    ignore_files = ignore_files or []
    include_files = include_files or []
    ignore_extensions = ignore_extensions or []
    include_extensions = include_extensions or []

    directory_structure = {}

    # Initialize a buffer to capture printed output if include_text_output is enabled
    output_buffer = StringIO() if include_text_output else None

    for root, dirs, files in os.walk(root_dir):
        # Filter out ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        # Filter to only include specified directories
        if include_dirs:
            dirs[:] = [d for d in dirs if d in include_dirs]

        # Calculate the depth of the current directory from the root
        depth = root.count(os.path.sep) - root_dir.count(os.path.sep)
        relative_path = os.path.relpath(root, root_dir)
        if relative_path == '.':
            relative_path = ''

        # Create the directory structure in the dictionary
        current_level = directory_structure
        if relative_path:
            for part in relative_path.split(os.path.sep):
                current_level = current_level.setdefault(part, {})
                if include_files_override:
                    current_level.setdefault("_files", [])
        elif include_files_override:
            current_level["_files"] = []

        # Print the directory name with appropriate indentation and capture to buffer if enabled
        line = (f"{root_dir.split(os.path.sep)[-1]}/" if depth == 0 else
                "│   " * (depth - 1) + "├── " + os.path.basename(root) + "/")
        print(line)
        if output_buffer:
            output_buffer.write(line + "\n")

        # Add the files to the current directory level
        if include_files_override:
            for file in files:
                file_ext = os.path.splitext(file)[1]
                if file not in ignore_files and file_ext not in ignore_extensions:
                    if (not include_files or file in include_files) and \
                            (not include_extensions or file_ext in include_extensions):
                        line = "│   " * depth + "├── " + file
                        print(line)
                        if output_buffer:
                            output_buffer.write(line + "\n")
                        current_level["_files"].append(file)

            # Remove "_files" key if it's empty
            if include_files_override and not current_level.get("_files"):
                del current_level["_files"]

    # Write the buffer to the specified text file if include_text_output is enabled
    if include_text_output and text_output_file:
        with open(text_output_file, 'w', encoding='utf-8') as f:
            f.write(output_buffer.getvalue())

    return directory_structure


def prune_empty_directories(directory_structure):
    def has_files_or_subdirectories(d):
        if "_files" in d and d["_files"]:
            return True
        for k, v in d.items():
            if k != "_files" and has_files_or_subdirectories(v):
                return True
        return False

    def prune(d):
        keys_to_delete = []
        for k, v in d.items():
            if k != "_files" and not has_files_or_subdirectories(v):
                keys_to_delete.append(k)
            elif k != "_files":
                prune(v)
        for k in keys_to_delete:
            del d[k]

    prune(directory_structure)


def save_structure_to_json(structure, output_file):
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(structure, f, ensure_ascii=False, indent=4)


def generate_and_save_directory_structure(config):
    # Load configuration
    root_directory = config["root_directory"]
    ignore_directories = config["ignore_directories"]
    include_directories = config["include_directories"]
    ignore_filenames = config["ignore_filenames"]
    include_filenames = config["include_filenames"]
    ignore_extensions = config["ignore_extensions"]
    include_extensions = config["include_extensions"]
    include_files_override = config["include_files_override"]
    ignore_dir_with_no_files = config.get("ignore_dir_with_no_files", False)
    include_text_output = config.get("include_text_output", False)

    # Generate unique filename suffix
    unique_suffix = time.strftime("%y%m%S")
    sanitized_root_directory = re.sub(r'[\\/:*?"<>|]', '-', root_directory)
    output_json_file = os.path.join(config["root_save_path"], f"dir_{sanitized_root_directory}_{unique_suffix}.json")
    os.makedirs(config["root_save_path"], exist_ok=True)

    # Define text output file path if text output is enabled
    text_output_file = None
    if include_text_output:
        text_output_file = os.path.join(config["root_save_path"], f"dir_{sanitized_root_directory}_{unique_suffix}.txt")

    # Generate directory structure
    directory_structure = generate_directory_structure(
        root_directory,
        ignore_dirs=ignore_directories,
        include_dirs=include_directories,
        ignore_files=ignore_filenames,
        include_files=include_filenames,
        ignore_extensions=ignore_extensions,
        include_extensions=include_extensions,
        include_files_override=include_files_override,
        include_text_output=include_text_output,
        text_output_file=text_output_file
    )

    # Prune directories with no files if the option is set
    if ignore_dir_with_no_files:
        prune_empty_directories(directory_structure)

    # Save directory structure to JSON
    save_structure_to_json(directory_structure, output_json_file)

    return directory_structure, output_json_file, text_output_file


local_configs = {
        "root_directory": "",
        "project_root": "",
        "ignore_directories": [
            ".", "_dev", ".history", "notes", "templates", "venv", "external libraries",
            "scratches", "consoles", ".git", "node_modules", "__pycache__", ".github",
            ".idea", "frontend", '.next', '__tests__', 'temp', 'static', 'templates', 'migrations', 'coreui-icons-pro',
            'staticfiles'
        ],
        "include_directories": [],
        "ignore_filenames": ['__init__.py'],
        "include_filenames": [],
        "ignore_extensions": ['txt'],
        "include_extensions": [],
        "include_files_override": True,
        "ignore_dir_with_no_files": True,
        "root_save_path": os.path.join(TEMP_DIR, 'dir_structure'),
        "include_text_output": True,
        "alias_map": {
            "@": "",
        },
    }


def get_structure_with_common_configs(root_directory, project_root, common_configs=None):
    if common_configs is None:
        common_configs = local_configs.copy()

    common_configs["root_directory"] = root_directory
    common_configs["project_root"] = project_root

    directory_structure, output_file, text_output_file = generate_and_save_directory_structure(common_configs)

    return directory_structure, output_file, text_output_file


if __name__ == "__main__":
    _project_root = str(Path(__file__).resolve().parent.parent.parent)
    config = {
        "root_directory": _project_root,
        "project_root": _project_root,
        "ignore_directories": [
            ".", "_dev", ".history", "notes", "templates", "venv", "external libraries",
            "scratches", "consoles", ".git", "node_modules", "__pycache__", ".github",
            ".idea", "frontend", '.next', '__tests__', 'temp', 'static', 'templates', 'migrations', 'coreui-icons-pro',
            'staticfiles'
        ],
        "include_directories": [],
        "ignore_filenames": ['__init__.py'],
        "include_filenames": [],
        "ignore_extensions": ['txt'],
        "include_extensions": [],
        "include_files_override": True,
        "ignore_dir_with_no_files": True,
        "root_save_path": os.path.join(TEMP_DIR, 'dir_structure'),
        "include_text_output": True,
        "alias_map": {
            "@": _project_root,
        },
    }

    directory_structure, output_file, text_output_file = generate_and_save_directory_structure(config)
    print(directory_structure)
    print()
    print_link(output_file)
    if text_output_file:
        print_link(text_output_file)

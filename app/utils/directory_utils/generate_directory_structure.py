"""matrx-local's directory-structure endpoints: this app's defaults over the one generator.

The tree text and JSON come from ``matrx_utils.utils.get_dir_structure`` (the
shared generator, which this file used to carry a stale copy of); only where
matrx-local saves the output and which folders it skips live here.
"""

import os
from pathlib import Path

from matrx_utils.utils.get_dir_structure import generate_and_save_directory_structure

from app.common.fancy_prints import print_link
from app.config import TEMP_DIR

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
    directory_structure, output_file, text_output_file = get_structure_with_common_configs(_project_root, _project_root)
    print(directory_structure)
    print()
    print_link(output_file)
    if text_output_file:
        print_link(text_output_file)

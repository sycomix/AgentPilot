import os


def get_system_text(key):
    filename = f'{key}.txt'
    file_path = os.path.join(os.path.dirname(__file__), 'system', filename)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"No file found for key {key}, path={file_path}")
    with open(file_path, 'r') as file:
        return file.read().strip()

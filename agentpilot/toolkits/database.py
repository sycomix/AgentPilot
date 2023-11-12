import os

from agentpilot.utils import sql


def get_database_type(filepath):
    SQLITE_SIGNATURE = b'SQLite format 3\x00'

    if not os.path.isfile(filepath):
        return 'File not found.'

    with open(filepath, 'rb') as f:
        header = f.read(16)

    return 'SQLITE' if header == SQLITE_SIGNATURE else 'UNKNOWN'


def get_create_table_schemas(db_filepath):
    db_type = get_database_type(db_filepath)
    if db_type == 'SQLITE':
        return sql.get_scalar(
            """
            SELECT GROUP_CONCAT(sql, x'0a0a') 
            FROM sqlite_master 
            WHERE type='table'
                AND name!='sqlite_sequence';
        """
        )
    else:
        raise Exception('Unknown database type')

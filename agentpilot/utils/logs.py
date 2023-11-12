from termcolor import cprint

from agentpilot.utils import sql, config
# from agentpilot.utils.popups import show_popup


class Logger:
    def __init__(self):
        self.log = []

    def add(self, _type, message):
        # Types:
        #  task created
        #  task resumed
        #  task cancelled
        #  task error
        #  task completed
        #  request
        #  action
        #  observation
        self.log.append((_type, message))

    def print(self):
        for _type, message in self.log:
            print(f'{_type}: {message}')


def insert_log(type, message, print_=True):
    try:
        if print_ and config.get_value('system.debug'):
            print("\r", end="")
            cprint(f'{type}: {message}', 'light_grey')  # print(f'{type}: {message}')
        sql.execute(
            "INSERT INTO logs (log_type, message) VALUES (?, ?);",
            (type, message),
        )

    except Exception as e:
        print('ERROR INSERTING LOG')


def log_invalid_task_decision(popup):
    pass

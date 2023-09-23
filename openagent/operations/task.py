import inspect
import time
from operations.action import ActionSuccess
from operations.react import ExplicitReAct
from utils.apis import llm
from utils import logs, config, retrieval
from utils.helpers import remove_brackets


# import interpreter


class Task:
    def __init__(self, agent, objective=None, parent_react=None):
        # self.task_context = Context(messages=messages)
        self.agent = agent
        self.status = TaskStatus.INITIALISING
        self.time_expression = None
        self.recurring = False
        self.parent_react = parent_react
        self.react = None
        self.interpreter = None
        self.actions = []
        self.action_methods = []
        self.current_action_index = 0
        self.objective = objective
        self.root_msg_id = 0

        last_msg = agent.context.message_history.last()
        if last_msg is not None:
            if self.objective is None:
                self.objective = last_msg['content']
            self.root_msg_id = last_msg['id']

        # Initialise variables
        react_enabled = config.get_value('react.enabled')
        force_react = False  # Todo - Add ability to enforce a react task
        enforce_react = react_enabled and (force_react or not config.get_value('actions.try-without-react'))
        validate_guess = config.get_value('actions.use-validator') and not enforce_react

        # Don't use react if task is inside another react, unless react.recursive = true
        if self.parent_react is not None:
            enforce_react = False
            validate_guess = False

        # Get action detection
        actions = self.get_action_guess()

        # Check if task cancelled
        if self.status == TaskStatus.CANCELLED:
            return

        # Validate guess if react is not enforced
        is_guess_valid = self.validate_guess(actions) if validate_guess else True

        use_react = enforce_react

        if is_guess_valid:
            if len(actions) == 0:
                self.status = TaskStatus.CANCELLED
                return

            elif len(actions) > 0:
                action_invoked_interpreter = any([getattr(action, 'use-interpreter', False) for action in actions])
                if action_invoked_interpreter:
                    use_react = False
                    self.interpreter = Interpreter(self)
                else:
                    self.actions = actions
                    self.action_methods = [action.run_action for action in self.actions]
        else:
            use_react = True

        # Use react if necessary
        if use_react:
            self.react = ExplicitReAct(self)

        if self.status != TaskStatus.CANCELLED:
            logs.insert_log('TASK CREATED', self.fingerprint())

    def fingerprint(self, _type='name', delimiter=','):
        if _type == 'name':
            return delimiter.join([action.__class__.__name__ for action in self.actions])
        elif _type == 'desc':
            return delimiter.join([action.desc for action in self.actions])
        elif _type == 'result':
            return delimiter.join(['Done, ' if action.result_code == 200 else 'Failed, ' + action.result for action in self.actions])
        else:
            raise Exception(f'Unknown fingerprint type: {_type}')

    def get_action_guess(self):
        last_2_msgs = self.agent.context.message_history.get(only_role_content=False, msg_limit=2)
        action_data_list = retrieval.match_request(last_2_msgs)

        if config.get_value('actions.use-function-calling'):
            collected_actions = retrieval.function_call_decision(self, action_data_list)
        else:
            collected_actions = retrieval.native_decision(self, action_data_list)

        if collected_actions:
            actions = [action_class(self.agent) for action_class in collected_actions]
            return actions

        on_no_actions = config.get_value('react.on-no-actions')
        use_interpreter = config.get_value('open-interpreter.enabled') and on_no_actions == 'INTERPRETER'
        if not use_interpreter:
            self.status = TaskStatus.CANCELLED
        return []

    def validate_guess(self, actions):
        if len(actions) > 1:
            return False

        conversation_str = self.agent.context.message_history.get_conversation_str(msg_limit=1)
        action_str = 'ACTION PLAN OVERVIEW:\nOrder, Action\n----------------\n'\
            + ',\n'.join(f'{actions.index(action) + 1}: {getattr(action, "desc", action.__class__.__name__)}' for action in actions)
        if len(actions) == 0:
            action_str += "No actions planned, return TRUE if there should be action taken based on the user's request"
        validator_response = llm.get_scalar(f"""
Analyze the provided conversation and action plan overview{' which is empty,' if len(actions) == 0 else ''} and {"return a boolean ('TRUE' or 'FALSE') indicating whether or not the user's request can been fully satisfied given the actions."
        if len(actions) > 0 else "return a boolean ('TRUE' or 'FALSE') indicating whether or not there should be action taken based on the user's request."}

{"The actions may have undisclosed parameters, which aren't shown in this action plan overview."
"An action parameter may be time based, and can natively understand expressions of time." if len(actions) > 0 else ''}

Use the following message to guide your analysis. This user message (denoted with arrows ">> ... <<") is the message with the user request.
{conversation_str}

{action_str}

{"Considering the action plan overview, can the users request be fully satisfied?"
"If more actions are needed to fully satisfy the request, return 'FALSE'."
"If the request can be fully satisfied using only these actions, return 'TRUE'." if len(actions) > 0 else
        "Considering the empty action plan, should there be action taken based on the user's request?"
        "If there should be action taken, return 'TRUE'."
        "If there should be no action taken, return 'FALSE'."}
        
Answer: """, single_line=True)  # If FALSE, explain why
        validator_response = validator_response.upper() == 'TRUE'
        if len(actions) == 0: validator_response = not validator_response  # Flip boolean if empty action list, as per the prompt
        if config.get_value('system.debug'):
            logs.insert_log('VALIDATOR RESPONSE', validator_response)
        return validator_response

    def run(self):
        if self.react is not None:
            return self.react.run()
        if self.interpreter is not None:
            return self.interpreter.run()

        task_response = ''
        self.status = TaskStatus.RUNNING
        for action_indx, action in enumerate(self.actions):
            # If action is already done, continue
            if action_indx < self.current_action_index:
                continue

            action.extract_inputs()

            if action.cancelled:
                self.status = TaskStatus.CANCELLED
                break

            if action.can_run():  # or force_run:
                action_method = self.action_methods[action_indx]
                try:
                    if inspect.isgeneratorfunction(action_method):
                        action_result = next(action_method())
                    else:
                        action_result = action_method()
                        if action_result is None:
                            action_result = ActionSuccess(f'[SAY]Done')

                except StopIteration as e:
                    if e.args[0] is False:
                        action_result = ActionSuccess(f'[SAY] Failed')
                    else:
                        action_result = ActionSuccess(f'[SAY] Done')
                except Exception as e:
                    return True, str(e)

                action.result_code = action_result.code
                response = action_result.response

                if not isinstance(response, str):
                    raise Exception('Response must be a string')

                if '[MI]' in response:
                    response = response.replace('[MI]', action.get_missing_inputs_string())
                if config.get_value('system.debug'):
                    logs.insert_log(f"TASK {'FINISHED' if action_result.code == 200 else 'MESSAGE'}", response)

                # if self.parent_react is None or action_result.code != 200:
                task_response = remove_brackets(response, '(')

                if action_result.code == 200:
                    action.result = response  # remove_brackets(response, '[')
                    self.current_action_index += 1
                else:
                    self.status = TaskStatus.FAILED if action_result.code == 500 else TaskStatus.PAUSED
                    break
            else:
                task_response = action.get_missing_inputs_string()
                self.status = TaskStatus.PAUSED
                break

        if self.status == TaskStatus.RUNNING:
            self.status = TaskStatus.COMPLETED
            logs.insert_log('TASK FINISHED', self.fingerprint(), print_=False)
            time.sleep(0.2)
            return True, task_response
        elif self.status == TaskStatus.PAUSED:
            return False, task_response
        else:
            return True, task_response

        # if self.recurring:
        #     self.status = TaskStatus.SCHEDULED
        #     # self.task_context.  # todo - reschedule task
        #     self.actions = []

    def is_duplicate_action(self):
        return any([action.is_duplicate_action() for action in self.actions])


class Interpreter:
    def __init__(self, parent_task):
        self.parent_task = parent_task
        self.open_interpreter = None  # Interpreter
#
#     def run(self):
#         custom_msg = interpreter.system_message
#         interpreter.chat()
#         pass


class TaskStatus:
    INITIALISING = 0
    RUNNING = 1
    PAUSED = 2
    CANCELLED = 3
    FAILED = 4
    COMPLETED = 5


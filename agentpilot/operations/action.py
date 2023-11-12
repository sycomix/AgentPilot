from termcolor import colored

from agentpilot.utils.apis import llm
from agentpilot.utils import helpers, logs, config
from agentpilot.operations.parameters import *


class BaseAction:
    def __init__(self, agent, example='', return_ftype=TextFValue):
        self.agent = agent
        self.add_response = lambda response: self.agent.intermediate_task_responses.put(response)
        self.inputs = ActionInputCollection()
        self.input_predict_count = 0

        self.desc_prefix = ''
        self.desc = ''
        self.example = example
        self.return_ftype = return_ftype

        self.cancelled = False

        self.result = ''
        self.result_code = 0

        self.when_to_run_input = ActionInput("when_to_run_the_action", time_based=True)

    def auto_populate_inputs(self, messages, exclude_inputs=None):  # context_string):
        if exclude_inputs is None:
            exclude_inputs = []

        class_name = self.__class__.__name__
        if config.get_value('system.debug'):
            print(f'\nAUTO POPULATING INPUTS FOR `{class_name}`')
        # rerun_action = False

        conversation_str = self.agent.context.message_history.get_conversation_str(msg_limit=4)
        input_format_str = "\n".join(f"    {inp.input_name}{inp.pretty_input_format()}" for inp in [self.when_to_run_input] + self.inputs.inputs if inp.input_name not in exclude_inputs)

        prompt = f"""Assistant wants to perform the action: `{class_name}` for the user.
Action Description: "{self.desc}"
All parameters for `{class_name}`:
{input_format_str}

{conversation_str}

Your task is to populate all of the parameter values for `{class_name}`. Give the most reasonable value based on common sense and popular opinion.

OUTPUT:
Output is in the format "{{parameter_name}}: {{parameter_value}}".
If there are multiple parameters, put each parameter on a new line.
If the parameter_name appears to be a question, then {{parameter_value}} should be the full answer to the question.

Based on common sense and popular opinion, populate all action parameters below:
-- `{class_name}` auto-populated parameters --
"""
        response = llm.get_scalar(prompt)  # , model='gpt-4')

        extracted_lines = [x.strip().strip(',') for x in response.split('\n') if (':' in x)]  # or no_param_names)]
        for extracted_line in extracted_lines:
            if extracted_line.strip().strip(':').lower() == class_name.lower(): continue

            line_split = [x.strip() for x in extracted_line.split(':', 1)]
            if len(line_split) == 1 and len(self.inputs) == 1 and len(extracted_lines) == 1:
                self.inputs.get(0).user_input = extracted_line
                if config.get_value('system.debug'):
                    tcolor = config.get_value('system.termcolor-verbose')
                    print(colored(f"Found INPUT '{self.inputs.get(0).input_name}' with VAL: '{extracted_line}'", tcolor))
                break

            if "CANCEL" in [x.upper() for x in line_split]:
                self.cancel()
                return None

            input_name, input_value = line_split

            # patch for class name
            if len(extracted_lines) == 1:
                if input_name == class_name.lower() and len(self.inputs) > 0:
                    input_name = self.inputs.get(0).input_name  # .inputs.get(0).input_name

            self.inputs.fill(input_name, input_value, overwrite_if_filled=True)
            # if rerun: rerun_action = True

    def extract_inputs(self):
        class_name = self.__class__.__name__
        self.input_predict_count += 1

        if len(self.inputs) == 0:
            return

        if config.get_value('system.debug'):
            logs.insert_log('EXTRACTING INPUTS', class_name)

        input_lookback_msg_cnt = self.agent.config.get('action_inputs.lookback_msg_count')
        is_msg_increment = self.agent.config.get('action_inputs.lookback_msg_count_increment')

        if self.input_predict_count > 1:
            is_msg_increment = False

        for i in range(0, input_lookback_msg_cnt if is_msg_increment else 1):
            root_msg_id = self.agent.active_task.root_msg_id if self.agent.active_task else 0
            msg_limit = i + 1 if is_msg_increment else input_lookback_msg_cnt
            conversation_str = self.agent.context.message_history.get_conversation_str(msg_limit=msg_limit)
            react_str = self.agent.context.message_history.get_react_str(msg_limit=8, from_msg_id=root_msg_id)
            input_format_str = "\n".join(f"    {inp.input_name}{inp.pretty_input_format()}" for inp in [self.when_to_run_input] + self.inputs.inputs)

            prompt = f"""Assistant wants to perform the action: `{class_name}` for the user.
Action Description: "{self.desc}"
All parameters for `{class_name}`:
{input_format_str}

{conversation_str}

{react_str}

Your task is to analyze the conversation and requests, and based on the last user message, return all parameter values for `{class_name}`.
{"It is possible it was a mistake to start this action. If the action isn't initiated on - or relevant to - the last user message (denoted with arrows `>> ... <<`), then just return 'CANCEL'."
    if self.input_predict_count == 1 
        else f'If the conversation or last user message (denoted with arrows `>> ... <<`) is no longer relevant to the action `{class_name}`, then just return "CANCEL".'}

OUTPUT:
Output is in the format "{{parameter_name}}: {{value}}".
If there are multiple parameters, each parameter will be on a new line.
If the value cannot be determined based on the conversation, then format the value like this: "{{parameter_name}}: NA".
If the parameter_name appears to be a question, then format the value like this: "{{parameter_name}}: {{answer}}". Here, {{answer}} should be the detected answer to the question parameter.
If a parameter has multiple explicit values, separate each value with three ampersands "&&&", like this: "{{parameter_name}}: {{value_a}}&&&{{value_b}}".

Based on the conversation, return all action parameters below:
-- `{class_name}` parameters --
"""
            # todo - add check for multivals on inputs that don't end with /s

            response = llm.get_scalar(prompt)  # , model='gpt-4')

            if response == 'CANCEL':
                self.cancel()
                return

            extracted_lines = [x.strip().strip(',') for x in response.split('\n') if (':' in x)]  # or no_param_names)]
            for extracted_line in extracted_lines:
                if extracted_line.strip().strip(':').lower() == class_name.lower():
                    continue

                line_split = [x.strip() for x in extracted_line.split(':', 1)]
                if len(line_split) == 1 and len(self.inputs) == 1 and len(extracted_lines) == 1:
                    input_name = self.inputs.get(0).input_name
                    input_value = extracted_line
                    self.inputs.fill(input_name, input_value)
                    break

                if "CANCEL" in [x.upper() for x in line_split]:
                    self.cancel()
                    return

                input_name, input_value = line_split

                # patch for class name bug
                if len(extracted_lines) == 1:
                    if len(self.inputs) > 0 and input_name.lower() == class_name.lower():
                        input_name = self.inputs.get(0).input_name  # .inputs.get(0).input_name

                self.inputs.fill(input_name, input_value)
                # if rerun: rerun_action = True

            self.inputs.fill_defaults()

            if self.can_run():
                break

        decay_at_idle_count = self.agent.config.get('action_inputs.decay_at_idle_count')
        if self.input_predict_count > decay_at_idle_count:
            self.cancel()
            return

    def can_run(self):
        return self.inputs.all_filled()

    def cancel(self):
        self.cancelled = True
        class_name = self.__class__.__name__
        logs.insert_log('ACTION CANCELLED', class_name)

    def get_missing_inputs_string(self):
        inp_str = '\n'.join([f'- {i.input_name}' for i in self.inputs.inputs if i.value == '' and not i.hidden])
        return f"[MI]\n{inp_str}\nVery briefly ask for this information in a naturally spoken way."


class ActionInput:
    def __init__(self, input_name, format='', examples='', fvalue=None, required=True, time_based=False, hidden=False, default=None):
        self.input_name = input_name.lower().strip().strip('_')
        self.format = format
        self.examples = examples
        self.desc = ''
        self.value = ''
        self.fvalue = TextFValue() if fvalue is None else fvalue()
        self.required = required
        self.time_based = time_based
        self.hidden = hidden
        self.default = default
        if self.default is not None:
            self.required = False

    def description(self):  # hacky for FC - todo
        return self.desc if self.desc else self.input_name.replace('-', ' ').replace('_', ' ')

    def pretty_input_format(self):
        # format = f" (Format {self.format})" if self.format != '' else ''
        accepts = self.fvalue.accepts
        return f" (This parameter takes {accepts})" if accepts != '' else ''


class ActionInputCollection:
    def __init__(self, inputs=None):
        self.inputs = [] if inputs is None else inputs

    def __len__(self):
        return len(self.inputs)

    def add(self, inp, **kwargs):
        if isinstance(inp, str):
            self.inputs.append(ActionInput(inp, **kwargs))
        else:
            self.inputs.append(inp)

    def get(self, item):
        if isinstance(item, str):
            return next((x for x in self.inputs if x.input_name == item), None)
        elif isinstance(item, int):
            return self.inputs[item]

    def get_value(self, item):
        inp = self.get(item)
        return inp.fvalue.base_value if inp else None

    def fill(self, input_name, input_value, overwrite_if_filled=False):
        # if input_name starts with a number or dash, remove them
        input_value = input_value.strip().strip(',')
        input_name = input_name.strip().strip('-').strip().lower()
        if input_name[0].isdigit():
            input_name = input_name[1:].replace('.', '').strip()

        if helpers.remove_brackets(input_value).upper() == "NA":
            # print(f"INPUT '{input_name}' not detected.")
            return False

        for i in self.inputs:
            if i.input_name != input_name:
                continue
            if i.value not in ['', 'NA'] and not overwrite_if_filled:
                continue
            i.value = input_value
            if config.get_value('system.debug'):
                tcolor = config.get_value('system.termcolor-verbose')
                print(colored(f"Found INPUT '{input_name}' with VAL: '{input_value}'", tcolor))
            return True

    def fill_defaults(self):
        for i in self.inputs:
            if i.value == '' and i.default is not None:
                i.value = i.default

    def all_filled(self):
        # return True if all inputs are filled
        return all(inp.value not in ['', 'NA'] for inp in self.inputs if inp.required)

    def pop(self):
        return self.inputs.pop()


class ActionResponse:
    def __init__(self, response, code=200):
        self.response = response
        self.code = code

        if '[MI]' in self.response:
            self.code = 400


class ActionSuccess(ActionResponse):
    def __init__(self, response):
        super().__init__(response, code=200)


class ActionError(ActionResponse):
    def __init__(self, response):
        super().__init__(response, code=500)


class MissingInputs(ActionResponse):
    def __init__(self, response):
        super().__init__(response, code=400)

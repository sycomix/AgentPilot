import json
import re
import threading
import time
import string
import asyncio
from queue import Queue
import agentpilot.agent.speech as speech
from agentpilot.agent.context import Context
from agentpilot.plugins.memgpt.modules.agent_plugin import MemGPT_AgentPlugin
from agentpilot.operations import task
from agentpilot.utils import sql, logs, helpers, retrieval
from agentpilot.plugins.openinterpreter.modules.agent_plugin import *
from agentpilot.utils.apis import llm


class Agent:
    def __init__(self, agent_id=0, context_id=None):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.config = {}  # self.get_global_config()
        self.context = Context(agent=self, agent_id=agent_id, context_id=context_id)
        self.id = self.context.agent_id
        self.name = ''
        self.desc = ''
        self.speaker = None
        self.blocks = {}
        self.active_plugin = AgentPlugin()  # OpenInterpreter_AgentPlugin(self)  # AgentPlugin()  #
        self.actions = None
        self.voice_data = None

        self.load_agent()

        self.intermediate_task_responses = Queue()
        self.speech_lock = asyncio.Lock()
        # self.listener = Listener(self.speaker.is_speaking, lambda response: self.save_message('assistant', response))

        self.active_task = None

        self.new_bubble_callback = None

        self.latest_analysed_msg_id = 0

        main_thread = threading.Thread(target=self.run, daemon=True)
        main_thread.start()

    def run(self):
        bg_tasks = [
            self.loop.create_task(self.speaker.download_voices()),
            self.loop.create_task(self.speaker.speak_voices()),
            self.loop.create_task(self.__intermediate_response_thread()),
            # self.loop.create_task(self.__summary_thread()),
            # self.loop.create_task(self.listener.listen())
        ]
        self.loop.run_until_complete(asyncio.gather(*bg_tasks))

    async def __intermediate_response_thread(self):
        while True:
            await asyncio.sleep(0.03)
            if self.speech_lock.locked():
                continue
            if self.intermediate_task_responses.empty():
                continue

            async with self.speech_lock:
                response_str = self.format_message(self.intermediate_task_responses.get())
                self.get_response(extra_prompt=response_str,
                                  check_for_tasks=False)

#     async def __summary_thread(self):
#         while True:
#             await asyncio.sleep(2)
#
#             unsummarised_ids = sql.get_results("SELECT id FROM contexts WHERE summary = '' AND id < (SELECT MAX(id) FROM contexts)")
#             if len(unsummarised_ids) == 0:
#                 continue
#
#             for context_id in unsummarised_ids:
#                 msg_log = sql.get_results(
#                     "SELECT * FROM ( SELECT id, role, msg FROM contexts_messages WHERE context_id = ? ORDER BY id DESC LIMIT 6 ) ORDER BY id",
#                     (context_id[0],))
#                 if len(msg_log) == 0:
#                     continue
#                 conversation = '\n'.join([f'{m[1]}: > {m[2]}' for m in msg_log])
#                 summary = llm.get_scalar(f"""
# Please provide a concise summary of the following conversation, outlining any key points, decisions, or disagreements.
# Exclude any formalities or irrelevant details that will be irrelevant or obsolete when the conversation ends.
#
# CONVERSATION:
# {conversation}
#
# SUMMARY:
# """, model='gpt-4')
#                 sql.execute("UPDATE contexts SET summary = ? WHERE id = ?", (summary, context_id[0]))

    def load_agent(self):
        self.blocks = sql.get_results("""
            SELECT
                name,
                text
            FROM blocks""", return_type='dict')

        if self.id > 0:
            agent_data = sql.get_results("""
                SELECT
                    a.`name`,
                    a.`desc`,
                    a.`config`,
                    s.`value` AS `global_config`
                FROM agents a
                LEFT JOIN settings s ON s.field = 'global_config'
                WHERE a.id = ? """, (self.id,))[0]
            self.name = agent_data[0]
            self.desc = agent_data[1]
            agent_config = json.loads(agent_data[2])
            global_config = json.loads(agent_data[3])

            # set self.config = global_config with agent_config overriding
            self.config = {**global_config, **agent_config}

        self.active_plugin = AgentPlugin()
        if use_plugin := self.config.get('general.use_plugin', None):
            if use_plugin == 'openinterpreter':
                self.active_plugin = OpenInterpreter_AgentPlugin(self)
            elif use_plugin == 'memgpt':
                self.active_plugin = MemGPT_AgentPlugin(self)
            else:
                raise Exception(f'Plugin "{use_plugin}" not recognised')

        voice_id = self.config.get('voice.current_id', None)
        if voice_id is not None and str(voice_id) != '0':  # todo dirty
            self.voice_data = sql.get_results("""
                SELECT
                    v.id,
                    v.api_id,
                    v.uuid,
                    v.display_name,
                    v.known_from,
                    v.creator,
                    v.lang,
                    v.verb
                FROM voices v
                WHERE v.id = ? """, (voice_id,))[0]
        else:
            self.voice_data = None

        if self.speaker is not None: self.speaker.kill()
        self.speaker = speech.Stream_Speak(self)

        source_dir = self.config.get('actions.source_directory', '.')
        # self.actions = retrieval.ActionCollection(source_dir)

    def get_global_config(self):
        global_config = sql.get_scalar("""
            SELECT
                s.`value` AS `global_config`
            FROM settings s
            WHERE s.field = 'global_config' """)
        return json.loads(global_config)

    def system_message(self, msgs_in_system=None, response_instruction='', msgs_in_system_len=0):
        date = time.strftime("%a, %b %d, %Y", time.localtime())
        time_ = time.strftime("%I:%M %p", time.localtime())
        timezone = time.strftime("%Z", time.localtime())
        location = "Sheffield, UK"

        # Use the SafeDict class to format the text to gracefully allow non existent keys
        # Fill SafeDict with blocks
        blocks_dict = helpers.SafeDict(dict(self.blocks.items()))

        semi_formatted_sys_msg = string.Formatter().vformat(
            self.config.get('context.sys_msg', ''), (), blocks_dict,
        )

        if self.voice_data:
            char_name = re.sub(r'\([^)]*\)', '', self.voice_data[3]).strip()
            full_name = f"{char_name} from {self.voice_data[4]}" if self.voice_data[4] != '' else char_name
            verb = self.voice_data[7]
            if verb != '':
                verb = f' {verb}'
        else:
            char_name = 'a helpful assistant'
            full_name = char_name
            verb = ''

        # ungrouped_actions = [fk for fk, fv in retrieval.all_category_files['_Uncategorised'].all_actions_data.items()]
        # action_groups = [k for k, v in retrieval.all_category_files.items() if not k.startswith('_')]
        all_actions = []  # ungrouped_actions + action_groups

        response_type = self.config.get('context.response_type', 'response')

        # Use the SafeDict class to format the text to gracefully allow non existent keys
        final_formatted_sys_msg = string.Formatter().vformat(
            semi_formatted_sys_msg, (), helpers.SafeDict(
                char_name=char_name,
                full_name=full_name,
                verb=verb,
                actions=', '.join(all_actions),
                response_instruction=response_instruction.strip(),
                date=date,
                time=time_,
                timezone=timezone,
                location=location,
                response_type=response_type
            )
        )

        message_str = ''
        if msgs_in_system:
            if msgs_in_system_len > 0:
                msgs_in_system = msgs_in_system[-msgs_in_system_len:]
            message_str = "\n".join(
                f"""{msg['role']}: \"{msg['content'].strip().strip('"')}\"""" for msg in msgs_in_system)
            message_str = f"\n\nCONVERSATION:\n\n{message_str}\nassistant: "
        if response_instruction != '':
            response_instruction = f"\n\n{response_instruction}\n\n"

        return final_formatted_sys_msg + response_instruction + message_str

    def format_message(self, message):
        dialogue_placeholders = {
            '[RES]': '[ITSOC] very briefly respond to the user in no more than [3S] ',
            '[INF]': '[ITSOC] very briefly inform the user in no more than [3S] ',
            '[ANS]': '[ITSOC] very briefly respond to the user considering the following information: ',
            '[Q]': '[ITSOC] Ask the user the following question: ',
            '[SAY]': '[ITSOC], say: ',
            '[MI]': '[ITSOC] Ask for the following information: ',
            '[ITSOC]': 'In the style of {char_name}{verb}, spoken like a genuine dialogue ',
            '[WOFA]': 'Without offering any further assistance, ',
            '[3S]': 'Three sentences',
        }
        for k, v in dialogue_placeholders.items():
            message = message.replace(k, v)

        if message != '':
            message = f"[INSTRUCTIONS-FOR-NEXT-RESPONSE]\n{message}\n[/INSTRUCTIONS-FOR-NEXT-RESPONSE]"
        return message

    def send(self, message):
        return self.save_message('user', message)

    def receive(self, stream=False):
        return self.get_response_stream() if stream else self.get_response()

    def send_and_receive(self, message, stream=True):
        self.send(message)
        return self.receive(stream=stream)

    def get_response(self, extra_prompt='', msgs_in_system=False, check_for_tasks=True):

        return ''.join(
            self.get_response_stream(extra_prompt, msgs_in_system, check_for_tasks)
        )

    def get_response_stream(self, extra_prompt='', msgs_in_system=False, check_for_tasks=True, use_davinci=False):
        messages = self.context.message_history.get(llm_format=True)
        last_role = self.context.message_history.last_role()

        check_for_tasks = self.config.get('actions.enable_actions', False) if check_for_tasks else False
        if check_for_tasks and last_role == 'user':
            replace_busy_action_on_new = self.config.get('actions.replace_busy_action_on_new')
            if self.active_task is None or replace_busy_action_on_new:

                new_task = task.Task(self)

                if new_task.status != task.TaskStatus.CANCELLED:
                    self.active_task = new_task

            if self.active_task:
                assistant_response = ''
                try:
                    task_finished, task_response = self.active_task.run()
                    if task_response != '':
                        extra_prompt = self.format_message(task_response)
                        for sentence in self.get_response_stream(extra_prompt=extra_prompt, check_for_tasks=False):
                            assistant_response += sentence
                            print(f'YIELDED: {sentence}  - FROM GetResponseStream')
                            yield sentence
                    else:
                        task_finished = True

                    if task_finished:
                        self.active_task = None

                except Exception as e:
                    logs.insert_log('TASK ERROR', str(e))
                    extra_prompt = self.format_message(
                        f'[SAY] "I failed the task" (Task = `{self.active_task.objective}`)')
                    for sentence in self.get_response_stream(extra_prompt=extra_prompt, check_for_tasks=False):
                        assistant_response += sentence
                        print(f'YIELDED: {sentence}  - FROM GetResponseStream')
                        yield sentence
                return assistant_response

        if last_role == 'assistant':
            on_consec_response = self.config.get('context.on_consecutive_response')
            if on_consec_response == 'PAD':
                messages.append({'role': 'user', 'content': ''})
            elif on_consec_response == 'REPLACE':
                messages.pop()

        # use_gpt4 = '[GPT4]' in extra_prompt
        # extra_prompt = extra_prompt.replace('[GPT4]', '')
        if extra_prompt != '' and len(messages) > 0:
            messages[-1]['content'] += '\nsystem: ' + extra_prompt

        use_msgs_in_system = messages if msgs_in_system else None
        system_msg = self.system_message(msgs_in_system=use_msgs_in_system,
                                         response_instruction=extra_prompt)
        model = self.config.get('context.model', 'gpt-3.5-turbo')
        if isinstance(
            self.active_plugin, (OpenInterpreter_AgentPlugin, MemGPT_AgentPlugin)
        ):
            stream = self.active_plugin.hook_stream()  # messages, messages[-1]['content'])
        else:
            stream = self.active_plugin.stream(messages, msgs_in_system, system_msg, model, use_davinci=False)
        had_fallback = False
        response = ''

        for key, chunk in self.speaker.push_stream(stream):
            if key == 'CONFIRM':
                language, code = chunk
                self.save_message('code', self.combine_lang_and_code(language, code))
                break
            if key == 'PAUSE':
                break

            if chunk == '[FALLBACK]':
                fallback_system_msg = self.system_message(msgs_in_system=messages,
                                                          response_instruction=extra_prompt)

                response = ''

                stream = self.active_plugin.stream(messages, msgs_in_system, fallback_system_msg, model, use_davinci=True)  # self.get_response_stream(msgs_in_system=True, check_for_tasks=False)
                for key in stream:
                    if key == 'assistant':
                        response += chunk
                    print(f'YIELDED: {str(key)}, {str(chunk)}  - FROM GetResponseStream')
                    yield key, chunk

                had_fallback = True
                logs.insert_log('PROMPT',
                                f'{fallback_system_msg}\n\n--- RESPONSE ---\n\n{response}',
                                print_=False)
                break
            elif key == 'assistant':
                response += chunk

            print(f'YIELDED: {str(key)}, {str(chunk)}  - FROM GetResponseStream')
            yield key, chunk

        if not had_fallback:
            initial_prompt = ''
            logs.insert_log('PROMPT', f'{initial_prompt}\n\n--- RESPONSE ---\n\n{response}',
                            print_=False)

        if response != '':
            self.save_message('assistant', response)

    def combine_lang_and_code(self, lang, code):
        return f'```{lang}\n{code}\n```'

    def save_message(self, role, content):
        if role == 'user':
            if self.context.message_history.last_role() == 'user':
                # return None  # Don't allow double user messages
                pass  # Allow for now
        elif role == 'assistant':
            content = content.strip().strip('"').strip()  # hack to clean up the assistant's messages from FB and DevMode
        elif role == 'output':
            content = 'The code executed without any output' if content.strip() == '' else content

        if content == '':
            return None
        return self.context.message_history.add(role, content)

    def __wait_until_finished_speaking(self):
        while True:
            if not self.speaker.speaking: break
            time.sleep(0.05)
import asyncio
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from queue import Queue

from agentpilot.utils import config
from agentpilot.utils.apis import elevenlabs, uberduck, awspolly, fakeyou, tts
from agentpilot.utils.helpers import replace_times_with_spoken, remove_brackets

chunk_chars = ['.', '?', '!', '\n', ': ', ';']  # , ',']


class Stream_Speak:
    def __init__(self, agent):
        self.agent = agent
        # self.agent.voice_data = agent.voice_data
        # self.api_id = None
        # self.uuid = None
        # self.char_name = ''

        self.queued_blocks = Queue()
        self.voice_uuids = Queue()
        self.voice_files = Queue()

        self.current_pid = None
        self.current_msg_uuid = None
        self.speaking = False
        self.stream_lock = threading.Lock()

    # async def is_speaking(self):
    #     return self.speaking

    def kill(self):
        try:
            self.current_msg_uuid = ''
            if self.current_pid is not None:
                os.kill(self.current_pid, signal.SIGTERM)

            # empty the queues
            while not self.voice_uuids.empty():
                self.voice_uuids.get()
            while not self.voice_files.empty():
                self.voice_files.get()
            self.speaking = False

        except OSError:
            pass
        except Exception as e:
            print('speech.kill ', e)

    def push_stream(self, stream):  # , fallbacks=True, block_until_spoken=False):
        with self.stream_lock:
            self.kill()
            response = ''
            current_block = ''
            msg_uuid = str(uuid.uuid4())
            self.current_msg_uuid = msg_uuid

        use_fallbacks = self.agent.config.get('context.fallback_to_davinci', True)
        speak_in_segments = self.agent.config.get('voice.speak_in_segments', True)

        is_first = True
        is_og = False
        og_response = ''
        is_dm = False
        awaiting_bracket_close = False
        # is_code_block = False

        try:
            ignore_keys = ['CONFIRM', 'PAUSE', 'language', 'code', 'output']
            for key, chunk in stream:
                if key == 'CONFIRM':
                    # return chunk
                    yield key, chunk
                    return
                if key in ignore_keys:
                    continue

                if self.current_msg_uuid != msg_uuid:
                    # print(f'YIELDED: event, [INTERRUPTED]  - FROM PushStream')
                    yield 'event', '[INTERRUPTED]'

                if chunk == '':
                    continue

                if awaiting_bracket_close:
                    if ')' in chunk:
                        awaiting_bracket_close = False
                    continue
                if '🔓' in chunk:
                    awaiting_bracket_close = True
                    if current_block.endswith('('):
                        current_block = current_block[:-1].strip('\n')
                    is_dm = True
                    is_og = False
                    continue

                if '🔒' in chunk:
                    awaiting_bracket_close = True
                    if current_block.endswith('('):
                        current_block = current_block[:-1].strip('\n')
                    if is_dm:
                        break
                    else:
                        is_og = True
                    continue
                if is_og:
                    og_response += chunk
                    continue

                current_block += chunk.replace('🔓', '').replace('🔒', '')
                # if current_block.strip().startswith('Normal Output)'):  # HACKY FIX todo
                #     current_block = ''
                #     break
                # # if is_code_block:
                # #     continue

                # if '```' in current_block:  # todo
                #     is_code_block = not is_code_block
                #     if is_code_block:
                #         # remove the ``` from the current block and all text to the right of it
                #         current_block = current_block[:current_block.rfind('```')].strip('\n')

                if any(word in chunk for word in chunk_chars):
                    if is_first:
                        current_block = current_block.strip()
                        if current_block.lower().startswith('assistant: '):
                            current_block = current_block[11:]
                        elif current_block.lower().startswith('bot: '):
                            current_block = current_block[5:]

                        if self.agent.voice_data:
                            char_name = self.agent.voice_data[3].lower()
                            char_first_name = char_name.split(' ')[0]
                            if (
                                current_block.lower()
                                .strip("'")
                                .startswith(f'{char_name}: ')
                            ):
                                current_block = current_block[len(char_name) + 2:].strip('"')
                            elif current_block.lower().startswith(
                                f'{char_first_name}: '
                            ):
                                current_block = current_block[len(char_first_name) + 2:].strip('"')
                        is_first = False

                    # if is_code_block:
                    #     continue

                    if speak_in_segments:
                        spaces_count = len(re.findall(r'\s+', current_block))
                        if spaces_count > 2:

                            if use_fallbacks and fallback_to_davinci(current_block):
                                # print("\r", end="")
                                print('YIELDED: event, [FALLBACK]  - FROM PushStream')
                                yield 'event', '[FALLBACK]'
                            response = self.generate_voices(msg_uuid, current_block, response)
                            # print(colored(current_block, tcolor), end='')
                            print(f'YIELDED: assistant, {current_block}  - FROM PushStream')
                            yield 'assistant', current_block
                            current_block = ''

            if is_og:
                current_block = og_response.replace('🔒', '').replace('🔓', '')

            if current_block.strip() != '':
                if use_fallbacks and fallback_to_davinci(current_block):
                    # print("\r", end="")
                    print('YIELDED: event, [FALLBACK]  - FROM PushStream')
                    yield 'event', '[FALLBACK]'
                response = self.generate_voices(msg_uuid, current_block, response)
                # print(colored(current_block, tcolor), end='')
                print(f'YIELDED: assistant, {current_block}  - FROM PushStream')
                yield 'assistant', current_block

        except StopIteration as si:
            raise si
        # except Exception as e:
        #     print('ERROR: speech.push_stream: ', e)
        #     pass

    def generate_voices(self, msg_uuid, current_block, response=''):
        for i in range(5):
            try:
                preproc_block = self.preproc_text(current_block)
                if len(preproc_block) <= 1:
                    return response + current_block

                if self.agent.voice_data:
                    api_id = int(self.agent.voice_data[1])
                    character_uuid = self.agent.voice_data[2]
                    if api_id == 1:
                        self.voice_uuids.put((msg_uuid, fakeyou.generate_voice_async(character_uuid, preproc_block)))
                        time.sleep(3.1)
                    elif api_id == 2:
                        self.voice_uuids.put((msg_uuid, uberduck.generate_voice_async(character_uuid, preproc_block)))
                    elif api_id in {3, 5}:
                        self.voice_uuids.put((msg_uuid, (character_uuid, preproc_block)))
                    else:
                        raise Exception('Invalid API ID')

                response += current_block
                return response

            except Exception as e:
                print('speech.gen_voices ', e)
                if i == 3: raise e
                time.sleep(0.1 * (i+1))
                raise e

    def preproc_text(self, text):
        text = remove_brackets(text, '[(*')

        text = replace_times_with_spoken(text)

        # REMOVE CODE BLOCKS FROM TEXT AND THEIR CONTENTS (```...```)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)

        # for k, v in switch_words.items():
        #     if k not in text: continue
        #     text = text.replace(k, v)
        # if '*' in text:
        #     ono = 1
        # # words = text split with a space, where each word only keeps letters and numbers
        # words = re.findall(r'\w[\w,]*', text)
        # for k, v in switch_real_words.items():
        #     if k not in words: continue
        #     text = ' '.join([v if word == k else word for word in words])
        # pattern = r"\b\d{1,2}:\d{2}\s?[ap]m\b"
        # time_matches = re.findall(pattern, text)
        # for time_match in time_matches:
        #     has_space = ' ' in time_match
        #     is_12hr = 'PM' in time_match.upper() and int(time_match.split(':')[0]) < 13
        #     h_symbol = '%I' if is_12hr else '%H'
        #     converted_time = time.strptime(time_match, f'{h_symbol}:%M %p' if has_space else f'{h_symbol}:%M%p')  # '%H = 24hr, %I = 12hr'
        #     spoken_time = time_to_human_spoken(converted_time)  # , include_timeframe=False)
        #     text = text.replace(time_match, f' {spoken_time} ')

        pattern = r'\$([\d.]+)\s?(\w+)'
        text = re.sub(pattern, r'\1 \2 dollars', text)
        pattern = r'\$([\d.]+)\s?(\w+)'
        text = re.sub(pattern, r'\1 \2 pounds', text)
        pattern = r'\$([\d.]+)\s?(\w+)'
        text = re.sub(pattern, r'\1 \2 euro', text)

        # remove emojies (some still get through)
        EMOJI_PATTERN = re.compile(
            "(["
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F700-\U0001F77F"  # alchemical symbols
            "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
            "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
            "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
            "\U0001FA00-\U0001FA6F"  # Chess Symbols
            "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
            "\U00002702-\U000027B0"  # Dingbats
            "\U000024C2-\U0001F251" 
            "]+)"
        )

        text = re.sub(EMOJI_PATTERN, r' \1 ', text)

        return text.strip()

    async def download_voices(self):
        while True:
            await asyncio.sleep(0.03)

            if not self.agent.voice_data:  # If offline TTS
                await asyncio.sleep(1)
                continue

            if self.voice_uuids.empty():
                continue

            msg_uuid, voice_file_uuid = self.voice_uuids.get()  # timeout=1)
            if msg_uuid != self.current_msg_uuid:
                continue
            if voice_file_uuid is None:
                continue

            api_id = int(self.agent.voice_data[1])
            if api_id == 1:
                audio_filepath = fakeyou.try_download_voice(voice_file_uuid)
                time.sleep(3.1)
            elif api_id == 2:
                audio_filepath = uberduck.try_download_voice(voice_file_uuid)
            elif api_id == 3:
                voice_uuid, text = voice_file_uuid
                audio_filepath = elevenlabs.try_download_voice(voice_uuid, text)
            elif api_id == 5:
                voice_uuid, text = voice_file_uuid
                audio_filepath = awspolly.try_download_voice(voice_uuid, text)
            else:
                raise Exception('Invalid API ID')

            if audio_filepath is not None:
                self.voice_files.put((msg_uuid, audio_filepath))

    async def speak_voices(self):
        while True:
            await asyncio.sleep(0.03)

            if not self.agent.voice_data:  # If offline TTS
                await asyncio.sleep(0.2)
                continue

            if self.voice_files.empty():
                continue

            msg_uuid, voice_file = self.voice_files.get()
            if msg_uuid != self.current_msg_uuid:
                continue
            self.speaking = True

            # print(f'PLAY CHUNK ({msg_uuid[-3:]})')
            if voice_file.endswith('.wav'):
                process = subprocess.Popen(['aplay', '-q', voice_file])
            elif voice_file.endswith('.mp3'):
                process = subprocess.Popen(['mpg123', '-q', voice_file])
            else:
                raise Exception('Invalid file extension')

            self.current_pid = process.pid
            process.communicate()
            if self.voice_files.empty():
                self.speaking = False


def fallback_to_davinci(text):
    lower_text = text.lower().replace('-', ' ')
    return any(trigger.lower() in lower_text for trigger in fallback_triggers)


fallback_iams = [
    'ai powered chatbot',
    "ai text based model",
    "ai text based language model",
    'chat bot',
    'chatbot',
    "artificial intelligence",
    "ai language model",
    "language model",
    "computer program",
    "virtual agent",
    "artificial intelligence agent",
    "artificially intelligent agent",
    "ai agent",
    "ai assistant",
    "text based ai",
    "text based ai agent",
    "text based ai language model",
    "text based ai assistant"
]
iam_prefixes = [
    "as a",
    "as an",
    "i'm a",
    "i'm an",
    "i am a",
    "i am an",
    "i am just a",
    "i am just an"
]

fallback_triggers = []
for iam in fallback_iams:
    fallback_triggers.extend(f'{iam_pf} {iam}' for iam_pf in iam_prefixes)

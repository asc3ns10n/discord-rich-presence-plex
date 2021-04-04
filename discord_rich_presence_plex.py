import asyncio
import datetime
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time

import plexapi.myplex

#pylint: disable=too-many-instance-attributes
#pylint: disable=too-many-arguments

class PlexConfig:

    extra_logging = True
    time_remaining = False

    def __init__(
        self,
        server_name="",
        username="",
        password="",
        token="",
        listen_for_user="",
        blacklisted_libraries=None,
        whitelisted_libraries=None,
        client_id="413407336082833418",
    ):
        self.server_name = server_name
        self.username = username
        self.password = password
        self.token = token
        self.listen_for_user = (
            username if listen_for_user == "" else listen_for_user
        ).lower()
        self.blacklisted_libraries = blacklisted_libraries
        self.whitelisted_libraries = whitelisted_libraries
        self.client_id = client_id


class DiscordRichPresence:
    def __init__(self, client_id, child):
        self.ipc_pipe = (
            (
                (
                    os.environ.get("XDG_RUNTIME_DIR", None)
                    or os.environ.get("TMPDIR", None)
                    or os.environ.get("TMP", None)
                    or os.environ.get("TEMP", None)
                    or "/tmp"
                )
                + "/discord-ipc-0"
            )
            if is_linux
            else "\\\\?\\pipe\\discord-ipc-0"
        )
        self.client_id = client_id
        self.pipe_reader = None
        self.pipe_writer = None
        self.process = None
        self.running = False
        self.child = child

    async def read(self):
        try:
            data = await self.pipe_reader.read(1024)
            self.child.log("[READ] " + str(json.loads(data[8:].decode("utf-8"))))
        except Exception as exception:
            self.child.log("[READ] " + str(exception))
            self.stop()

    def write(self, op_code, payload):
        payload = json.dumps(payload)
        self.child.log("[WRITE] " + str(payload))
        data = self.pipe_writer.write(
            struct.pack("<ii", op_code, len(payload)) + payload.encode("utf-8")
        )

    async def handshake(self):
        try:
            if is_linux:
                self.pipe_reader, self.pipe_writer = await asyncio.open_unix_connection(
                    self.ipc_pipe, loop=self.loop
                )
            else:
                self.pipe_reader = asyncio.StreamReader(loop=self.loop)
                self.pipe_writer, _ = await self.loop.create_pipe_connection(
                    lambda: asyncio.StreamReaderProtocol(
                        self.pipe_reader, loop=self.loop
                    ),
                    self.ipc_pipe,
                )
            self.write(0, {"v": 1, "client_id": self.client_id})
            await self.read()
            self.running = True
        except Exception as exception:
            self.child.log("[HANDSHAKE] " + str(exception))

    def start(self):
        self.child.log("Opening Discord IPC Pipe")
        empty_process_file_path = (
            tempfile.gettempdir()
            + ("/" if is_linux else "\\")
            + "DiscordRichPresencePlex-emptyProcess.py"
        )
        if not os.path.exists(empty_process_file_path):
            with open(empty_process_file_path, "w") as empty_process_file:
                empty_process_file.write(
                    "import time\n\ntry:\n\twhile (True):\n\t\ttime.sleep(3600)\nexcept:\n\tpass"
                )
        self.process = subprocess.Popen(
            ["python3" if is_linux else "pythonw", empty_process_file_path]
        )
        self.loop = asyncio.new_event_loop() if is_linux else asyncio.ProactorEventLoop()
        self.loop.run_until_complete(self.handshake())

    def stop(self):
        self.child.log("Closing Discord IPC Pipe")
        self.child.last_state, self.child.last_session_key, self.child.last_rating_key = (
            None,
            None,
            None,
        )
        self.process.kill()
        if self.child.stop_timer:
            self.child.stop_timer.cancel()
            self.child.stop_timer = None
        if self.child.stop_timer2:
            self.child.stop_timer2.cancel()
            self.child.stop_timer2 = None
        if self.pipe_writer:
            try:
                self.pipe_writer.close()
            except:
                pass
            self.pipe_writer = None
        if self.pipe_reader:
            try:
                self.loop.run_until_complete(self.pipe_reader.read(1024))
            except:
                pass
            self.pipe_reader = None
        try:
            self.loop.close()
        except:
            pass
        self.running = False

    def send(self, activity):
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"activity": activity, "pid": self.process.pid},
            "nonce": "{0:.20f}".format(time.time()),
        }
        self.write(1, payload)
        self.loop.run_until_complete(self.read())


class DiscordRichPresencePlex(DiscordRichPresence):

    product_name = "Plex Media Server"
    stop_timer_interval = 5
    stop_timer2_interval = 35
    check_connection_timer_interval = 60
    maximum_ignores = 3

    def __init__(self, plex_config):
        self.plex_config = plex_config
        self.instance_id = hashlib.md5(str(id(self)).encode("UTF-8")).hexdigest()[:5]
        super().__init__(plex_config.client_id, self)
        self.plex_account = None
        self.plex_server = None
        self.is_server_owner = False
        self.plex_alert_listener = None
        self.last_state = None
        self.last_session_key = None
        self.last_rating_key = None
        self.stop_timer = None
        self.stop_timer2 = None
        self.check_connection_timer = None
        self.ignore_count = 0

    def run(self):
        self.reset()
        connected = False
        while not connected:
            try:
                if self.plex_config.token:
                    self.plex_account = plexapi.myplex.MyPlexAccount(
                        self.plex_config.username, token=self.plex_config.token
                    )
                else:
                    self.plex_account = plexapi.myplex.MyPlexAccount(
                        self.plex_config.username, self.plex_config.password
                    )
                self.log('Logged in as Plex User "' + self.plex_account.username + '"')
                self.plex_server = None
                for resource in self.plex_account.resources():
                    if (
                        resource.product == self.product_name
                        and resource.name == self.plex_config.server_name
                    ):
                        self.plex_server = resource.connect()
                        try:
                            self.plex_server.account()
                            self.is_server_owner = True
                        except:
                            pass
                        self.log(
                            "Connected to "
                            + self.product_name
                            + ' "'
                            + self.plex_config.server_name
                            + '"'
                        )
                        self.plex_alert_listener = self.plex_server.startAlertListener(
                            self.on_plex_server_alert
                        )
                        self.log(
                            'Listening for PlaySessionStateNotification alerts from user "'
                            + self.plex_config.listen_for_user
                            + '"'
                        )
                        if self.check_connection_timer:
                            self.check_connection_timer.cancel()
                            self.check_connection_timer = None
                        self.check_connection_timer = threading.Timer(
                            self.check_connection_timer_interval, self.check_connection
                        )
                        self.check_connection_timer.start()
                        connected = True
                        break
                if not self.plex_server:
                    self.log(
                        self.product_name
                        + ' "'
                        + self.plex_config.server_name
                        + '" not found'
                    )
                    break
            except Exception as exception:
                self.log("Failed to connect to Plex: " + str(exception))
                self.log("Reconnecting in 10 seconds")
                time.sleep(10)

    def reset(self):
        if self.running:
            self.stop()
        self.plex_account, self.plex_server = None, None
        if self.plex_alert_listener:
            try:
                self.plex_alert_listener.stop()
            except:
                pass
            self.plex_alert_listener = None
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None
        if self.stop_timer2:
            self.stop_timer2.cancel()
            self.stop_timer2 = None
        if self.check_connection_timer:
            self.check_connection_timer.cancel()
            self.check_connection_timer = None

    def check_connection(self):
        try:
            self.log(
                "Request for clients list to check connection: "
                + str(self.plex_server.clients()),
                extra=True,
            )
            self.check_connection_timer = threading.Timer(
                self.check_connection_timer_interval, self.check_connection
            )
            self.check_connection_timer.start()
        except Exception as exception:
            self.log("Connection to Plex lost: " + str(exception))
            self.log("Reconnecting")
            self.run()

    def log(self, text, colour="", extra=False):
        timestamp = datetime.datetime.now().strftime("%I:%M:%S %p")
        prefix = (
            "["
            + timestamp
            + "] ["
            + self.plex_config.server_name
            + "/"
            + self.instance_id
            + "] "
        )
        lock.acquire()
        if extra:
            if self.plex_config.extra_logging:
                print(prefix + color_text(str(text), colour))
        else:
            print(prefix + color_text(str(text), colour))
        lock.release()

    def on_plex_server_alert(self, data):
        if not self.plex_server:
            return
        try:
            if data["type"] == "playing" and "PlaySessionStateNotification" in data:
                session_data = data["PlaySessionStateNotification"][0]
                state = session_data["state"]
                session_key = int(session_data["sessionKey"])
                rating_key = int(session_data["rating_key"])
                view_offset = int(session_data["viewOffset"])
                self.log(
                    "Received Update: "
                    + color_text(session_data, "yellow").replace("'", '"'),
                    extra=True,
                )
                metadata = self.plex_server.fetchItem(rating_key)
                library_name = metadata.section().title
                if isinstance(self.plex_config.blacklisted_libraries, list):
                    if library_name in self.plex_config.blacklisted_libraries:
                        self.log(
                            'Library "' + library_name + '" is blacklisted, ignoring',
                            "yellow",
                            True,
                        )
                        return
                if isinstance(self.plex_config.whitelisted_libraries, list):
                    if library_name not in self.plex_config.whitelisted_libraries:
                        self.log(
                            'Library "'
                            + library_name
                            + '" is not whitelisted, ignoring',
                            "yellow",
                            True,
                        )
                        return
                if (
                    self.last_session_key == session_key
                    and self.last_rating_key == rating_key
                ):
                    if self.stop_timer2:
                        self.stop_timer2.cancel()
                        self.stop_timer2 = None
                    if self.last_state == state:
                        if self.ignore_count == self.maximum_ignores:
                            self.ignore_count = 0
                        else:
                            self.log("Nothing changed, ignoring", "yellow", True)
                            self.ignore_count += 1
                            self.stop_timer2 = threading.Timer(
                                self.stop_timer2_interval, self.stop_on_no_update
                            )
                            self.stop_timer2.start()
                            return
                    elif state == "stopped":
                        self.last_state, self.last_session_key, self.last_rating_key = (
                            None,
                            None,
                            None,
                        )
                        self.stop_timer = threading.Timer(
                            self.stop_timer_interval, self.stop
                        )
                        self.stop_timer.start()
                        self.log("Started stop_timer", "yellow", True)
                        return
                elif state == "stopped":
                    self.log(
                        '"stopped" state update from unknown session key, ignoring',
                        "yellow",
                        True,
                    )
                    return
                if self.is_server_owner:
                    self.log(
                        "Checking Sessions for Session Key "
                        + color_text(session_key, "yellow"),
                        extra=True,
                    )
                    plex_server_sessions = self.plex_server.sessions()
                    if len(plex_server_sessions) < 1:
                        self.log("Empty session list, ignoring", "red", True)
                        return
                    for session in plex_server_sessions:
                        self.log(
                            str(session)
                            + ", Session Key: "
                            + color_text(session.session_key, "yellow")
                            + ", Users: "
                            + color_text(session.usernames, "yellow").replace("'", '"'),
                            extra=True,
                        )
                        session_found = False
                        if session.session_key == session_key:
                            session_found = True
                            self.log("Session found", "green", True)
                            if (
                                session.usernames[0].lower()
                                == self.plex_config.listen_for_user
                            ):
                                self.log(
                                    'Username "'
                                    + session.usernames[0].lower()
                                    + '" matches "'
                                    + self.plex_config.listen_for_user
                                    + '", continuing',
                                    "green",
                                    True,
                                )
                                break
                            else:
                                self.log(
                                    'Username "'
                                    + session.usernames[0].lower()
                                    + '" doesn\'t match "'
                                    + self.plex_config.listen_for_user
                                    + '", ignoring',
                                    "red",
                                    True,
                                )
                                return
                    if not session_found:
                        self.log("No matching session found", "red", True)
                        return
                if self.stop_timer:
                    self.stop_timer.cancel()
                    self.stop_timer = None
                if self.stop_timer2:
                    self.stop_timer2.cancel()
                self.stop_timer2 = threading.Timer(
                    self.stop_timer2_interval, self.stop_on_no_update
                )
                self.stop_timer2.start()
                self.last_state, self.last_session_key, self.last_rating_key = (
                    state,
                    session_key,
                    rating_key,
                )
                media_type = metadata.type
                if state != "playing":
                    extra = (
                        seconds_to_text(view_offset / 1000, ":")
                        + "/"
                        + seconds_to_text(metadata.duration / 1000, ":")
                    )
                else:
                    extra = seconds_to_text(metadata.duration / 1000)
                if media_type == "movie":
                    title = metadata.title + " (" + str(metadata.year) + ")"
                    extra = (
                        extra
                        + " · "
                        + ", ".join([genre.tag for genre in metadata.genres[:3]])
                    )
                    large_text = "Watching a Movie"
                elif media_type == "episode":
                    title = metadata.grandparentTitle
                    extra = (
                        extra
                        + " · S"
                        + str(metadata.parentIndex)
                        + " · E"
                        + str(metadata.index)
                        + " - "
                        + metadata.title
                    )
                    large_text = "Watching a TV Show"
                elif media_type == "track":
                    title = metadata.title
                    artist = metadata.originalTitle
                    if not artist:
                        artist = metadata.grandparentTitle
                    extra = artist + " · " + metadata.parentTitle
                    large_text = "Listening to Music"
                else:
                    self.log(
                        'Unsupported media type "' + media_type + '", ignoring',
                        "red",
                        True,
                    )
                    return
                activity = {
                    "details": title,
                    "state": extra,
                    "assets": {
                        "large_text": large_text,
                        "large_image": "logo",
                        "small_text": state.capitalize(),
                        "small_image": state,
                    },
                }
                if state == "playing":
                    current_timestamp = int(time.time())
                    if self.plex_config.time_remaining:
                        activity["timestamps"] = {
                            "end": round(
                                current_timestamp
                                + ((metadata.duration - view_offset) / 1000)
                            )
                        }
                    else:
                        activity["timestamps"] = {
                            "start": round(current_timestamp - (view_offset / 1000))
                        }
                if not self.running:
                    self.start()
                if self.running:
                    self.send(activity)
                else:
                    self.stop()
        except Exception as exception:
            self.log("on_plex_server_alert Error: " + str(exception))

    def stop_on_no_update(self):
        self.log(
            "No updates from session key " + str(self.last_session_key) + ", stopping",
            "red",
            True,
        )
        self.stop()


def color_text(text, colour=""):
    prefix = ""
    suffix = ""
    colour = colour.lower()
    if colour in colours:
        prefix = "\033[" + colours[colour] + "m"
        suffix = "\033[0m"
    return prefix + str(text) + suffix


def seconds_to_text(seconds, joiner=""):
    seconds = round(seconds)
    text = {"h": seconds // 3600, "m": seconds // 60 % 60, "s": seconds % 60}
    if joiner == "":
        text = [str(v) + k for k, v in text.items() if v > 0]
    else:
        if text["h"] == 0:
            del text["h"]
        text = [str(v).rjust(2, "0") for k, v in text.items()]
    return joiner.join(text)


with open('config.json') as f:
    config_data = json.load(f)

plex_configs = [
    PlexConfig(**config_data)
]

if len(plex_configs) == 0:
    print("Error: plex_configs list is empty")
    sys.exit()

is_linux = sys.platform in ["linux", "darwin"]
lock = threading.Semaphore(value=1)

os.system("clear" if is_linux else "cls")

colours = {
    "red": "91",
    "green": "92",
    "yellow": "93",
    "blue": "94",
    "magenta": "96",
    "cyan": "97",
}

DiscordRichPresencePlexInstances = []
for config in plex_configs:
    DiscordRichPresencePlexInstances.append(DiscordRichPresencePlex(config))
try:
    for DiscordRichPresencePlexInstance in DiscordRichPresencePlexInstances:
        DiscordRichPresencePlexInstance.run()
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    for DiscordRichPresencePlexInstance in DiscordRichPresencePlexInstances:
        DiscordRichPresencePlexInstance.reset()
except Exception as exception:
    print("Error: " + str(exception))

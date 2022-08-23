import sys
import os
import json
import logging
from threading import Thread, Lock, Event
from subprocess import Popen, PIPE
from typing import Iterator, Type, Union
from collections import defaultdict
from ulauncher.api.result.extension_result import ExtensionResult

from ulauncher.api.shared.Response import Response
from ulauncher.api.shared.action.BaseAction import BaseAction
from ulauncher.api.shared.event import (BaseEvent, KeywordQueryEvent, InputTriggerEvent, ItemEnterEvent,
                                        LaunchTriggerEvent, PreferencesUpdateEvent, UnloadEvent)
from ulauncher.api.shared.query import Query
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.client.Client import Client
from ulauncher.api.client.setup_logging import setup_logging


class Extension:
    """
    Manages extension runtime
    """

    def __init__(self):
        setup_logging()
        self.extension_id = os.path.basename(os.path.dirname(sys.argv[0]))
        self.logger = logging.getLogger(self.extension_id)
        self._listeners = defaultdict(list)
        self._client = Client(self)
        self.preferences = {}
        self.process: Popen | None = None
        self.process_lock = Lock()
        self.thread: StoppableThread | None = None
        try:
            self.preferences = json.loads(os.environ.get("EXTENSION_PREFERENCES", "{}"))
        except Exception:
            pass

        # subscribe with methods if user has added their own
        if self.__class__.on_input is not Extension.on_input:
            self.subscribe(InputTriggerEvent, 'on_input')
        if self.__class__.on_launch is not Extension.on_launch:
            self.subscribe(LaunchTriggerEvent, 'on_launch')
        if self.__class__.on_item_enter is not Extension.on_item_enter:
            self.subscribe(ItemEnterEvent, 'on_item_enter')
        if self.__class__.on_unload is not Extension.on_unload:
            self.subscribe(UnloadEvent, 'on_unload')
        if self.__class__.on_preferences_update is not Extension.on_preferences_update:
            self.subscribe(PreferencesUpdateEvent, 'on_preferences_update')

    def subscribe(self, event_type: Type[BaseEvent], listener: Union[str, object]):
        """
        Example: extension.subscribe(InputTriggerEvent, "on_input")
        """
        method_name = None
        if isinstance(listener, str):
            method_name = listener
            listener = self

        self._listeners[event_type].append((listener, method_name))

    def trigger_event(self, event: BaseEvent):
        event_type = type(event)
        listeners = self._listeners[event_type]

        if not listeners:
            if event_type == InputTriggerEvent and self._listeners[KeywordQueryEvent]:
                # convert InputTriggerEvent to KeywordQueryEvent for backwards compatibility
                input_text, trigger_id = event.args
                keyword = self.preferences[trigger_id]
                kw_event = KeywordQueryEvent(Query(f"{keyword} {input_text}"), event)
                self.trigger_event(kw_event)
            else:
                self.logger.debug('No listeners for event %s', event_type.__name__)

        for listener, method_name in listeners:
            method = getattr(listener, method_name or "on_event")
            # We can use method_name to determine if listener was added the old way or the new class method way
            # Pass the event args if method_name isn't None, otherwise event and self for backwards compatibility
            args = tuple(event.args) if method_name else (event, self)
            if self.thread:
                self.thread.stop()
            self.thread = StoppableThread(target=self.run_event_listener, args=(event, listener, method, args))
            self.thread.start()

    def run_event_listener(self, event, listener, method, args):
        action = method(*args)
        while action:
            if isinstance(action, Iterator):
                action = list(action)
            if isinstance(action, list) and len(action) != 0 and not isinstance(action[0], ExtensionResult):
                if isinstance(action[0], str):
                    action = [action]
                stdout = None
                success = True
                for cmd in action:
                    self.logger.debug('run command: %s', ' '.join(cmd))
                    process = Popen(cmd, stdin=stdout, stdout=PIPE, stderr=PIPE)
                    stdout = process.stdout
                    self.process_lock.acquire()
                    if self.process and self.process.poll() is None:
                        self.process.kill()
                    self.process = process
                    self.process_lock.release()
                    if process.wait() != 0:
                        self.logger.debug('run command failed: %s', ' '.join(cmd))
                        success = False
                        break
                if success:
                    output = stdout.read().decode('utf-8')
                    method = getattr(listener, 'on_output')
                    action = method(process.args, output)
                else:
                    self.logger.warning('process return non-zero')
                    action = None
            else:
                assert isinstance(action, (list, BaseAction)), "on_event must return list of Results or a BaseAction"
                origin_event = getattr(event, "origin_event", event)
                self._client.send(Response(origin_event, action))
                action = None

    def run(self):
        """
        Subscribes to events and connects to Ulauncher socket server
        """
        self.subscribe(PreferencesUpdateEvent, PreferencesUpdateEventListener())
        self._client.connect()

    def on_input(self, query: str, trigger_id: str):
        pass

    def on_launch(self, trigger_id: str):
        pass

    def on_item_enter(self, data):
        pass

    def on_preferences_update(self, id, value, previous_value):
        pass

    def on_unload(self):
        pass

    def on_output(self, cmd, output):
        pass


class PreferencesUpdateEventListener(EventListener):

    def on_event(self, event, extension):
        extension.preferences[event.id] = event.new_value

class StoppableThread(Thread):

    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, target, args):
        super(StoppableThread, self).__init__(target=target, args=args)
        self._stop_event = Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

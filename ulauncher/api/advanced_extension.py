import os
import threading
import subprocess
from typing import Iterator, Union, Dict, List
from ulauncher.api.extension import Extension
from ulauncher.api.shared.Response import Response
from ulauncher.api.shared.action.BaseAction import BaseAction

# pylint: disable=unused-argument

class AdvancedExtension(Extension):

    def __init__(self):
        super(AdvancedExtension, self).__init__()
        self.load_locks: Dict[str, threading.Lock] = {}
        self.thread: Union[threading.Thread, None] = None
        self.thread_lock = threading.Lock()
        self.cmd: Union[subprocess.Popen, None] = None
        self.cmd_lock = threading.Lock()

    def run_event_listener(self, event, method, args):
        t = threading.current_thread()
        self.thread_lock.acquire()
        if self.thread:
            self.thread.name = "stop"
        self.thread = t
        self.thread_lock.release()
        action = method(*args)
        if action is None:
            action = []
        if isinstance(action, Iterator):
            action = list(action)
        assert isinstance(action, (list, BaseAction)), "on_event must return list of Results or a BaseAction"
        origin_event = getattr(event, "origin_event", event)
        self._client.send(Response(origin_event, action))

    def notify(self, head: str, body: Union[str, int]) -> None:
        head = f'[{self.extension_id}] {head}'
        if isinstance(body, str):
            os.system(f'notify-send -r 1000 -i ulauncher "{head}" "{body}"')
        else:
            if body < 0:
                body = 0
            elif body > 100:
                body = 100
            count1 = int(body / 5)
            count2 = 20 - count1
            part1 = '=' * count1
            if len(part1) > 0:
                part1 = part1[:-1] + '>'
            part2 = '-' * count2
            body = f'[{part1}{part2}] {body:3d}%'
            os.system(f'notify-send -r 1000 -i ulauncher "{head}" "{body}"')

    def debug(self, msg: object, *args: object) -> None:
        self.logger.debug(msg, *args)

    def info(self, msg: object, *args: object) -> None:
        self.logger.info(msg, *args)

    def warn(self, msg: object, *args: object) -> None:
        self.logger.warning(msg, *args)

    def error(self, msg: object, *args: object) -> None:
        self.logger.error(msg, *args)

    def run_command(self, cmd: List[str], stdin = None, stdout = subprocess.PIPE):
        p = subprocess.Popen(cmd, stdin=stdin, stdout=stdout)
        with self.cmd_lock:
            if self.cmd and self.cmd.poll() is None:
                self.cmd.kill()
            self.cmd = p
        if p.wait() == 0:
            return p.stdout

    def need_load(self, sth: str) -> bool:
        return False

    def do_load(self, sth: str) -> bool:
        return False

    @staticmethod
    def load(*sths: str):
        def decorator(fn):
            def wrapper(self: AdvancedExtension, *args, **kwargs):
                for sth in sths:
                    success = True
                    if self.need_load(sth):
                        lock = self.load_locks.setdefault(sth, threading.Lock())
                        if lock.acquire(blocking=False):
                            if self.need_load(sth):
                                success = self.do_load(sth)
                            lock.release()
                        else:
                            success = False
                    if success:
                        continue
                    else:
                        self.warn('failed to load "%s"', sth)
                        self.notify('Load Failed', f'failed to load "{sth}"')
                        return
                return fn(self, *args, **kwargs)
            return wrapper
        return decorator

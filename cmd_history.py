import os
from cudatext import *
from cudax_lib import get_translation

_   = get_translation(__file__)  # I18N


LOG = False

fn_config = os.path.join(app_path(APP_DIR_SETTINGS), 'plugins.ini')
fn_history = os.path.join(app_path(APP_DIR_SETTINGS), 'cmd_history.txt')

CFG_SECTION = "command_history"
TIMER_DELAY = 30*1000   # ms -- 30 sec
HISTORY_TAG = 'cmdhst'

INVOKE_PALETTE = 'app_pal'
INVOKE_MENU    = 'menu_main'

cmd_None = 99   # cudatext_cmd.py

history = [] # command codes  (new - last)


def _cleanup(f):
    """ cleanups Command._all_commands after function call """
    def p(self, *args, **vargs):
        result = f(self, *args, **vargs)
        self._all_commands = None
        return result
    return p


class Command:

    def __init__(self):
        self._opt_history_size = None

        self._ed = None     # last focused editor
        # cache -- cmd_code -> name  (PROC_GET_COMMANDS: "cmd" -> "name")
        # NOTE: dont show in dialog if value None -- missing command
        self._cmd_names = {}
        self._modmeth_cmds = {} # cache -- command's module+method string  to  command id
        self._all_commands = None

        self._load_config()

        # DBG
        self._hst = history

    @property
    def opt_history_size(self):
        if self._opt_history_size is None:
            self._opt_history_size = int(ini_read(fn_config, CFG_SECTION, 'history_size', '24'))
        return self._opt_history_size

    @property
    def all_commands(self):
        if self._all_commands is None:
            self._all_commands = app_proc(PROC_GET_COMMANDS, '')
        return self._all_commands


    @_cleanup
    def _load_config(self):
        if os.path.isfile(fn_history):
            with open(fn_history, 'r') as f:
                cmd_names = (name.rstrip() for name in f.readlines())
            cmd_ids = filter(None, map(self._get_cmdid_by_name, cmd_names))
            history.clear()
            history.extend(cmd_ids)


    def config(self):
        ini_write(fn_config, CFG_SECTION, 'history_size', str(self.opt_history_size))
        file_open(fn_config)

    # start immediately
    def on_start(self, ed_self):
        timer_proc(TIMER_START, 'module=cuda_cmd_history;cmd=_on_timer;', TIMER_DELAY)

    def on_exit(self, ed_self):
        self._save_history()

    # discard reference to closed Editor
    def on_close(self, ed_self):
        if self._ed  and  self._ed.h == ed_self.h:
            self._ed = None

    # check last editor for new commands
    def on_focus(self, ed_self):
        self._process_command_log(self._ed)

        self._ed = ed_self

    @_cleanup
    def show_history(self):
        self._process_command_log(ed)

        if not history:     return

        hcmds = list(reversed(history))
        _cmd_names = filter(None, map(self._get_cmd_name, hcmds)) # get non-None names for history commands
        # command name to left, command path (origin) - to right
        _cmd_names = list('\t'.join(name.rsplit(':', 1)[::-1])  for name in _cmd_names)
        res = dlg_menu(DMENU_LIST, _cmd_names, caption=_('Commands history'))
        if res is not None:
            cmd_id = hcmds[res]
            ed.cmd(cmd_id)
            # move called command to bottom of history
            del history[history.index(cmd_id)]
            history.append(cmd_id)


    def _on_timer(self, tag='', info=''):
        self._process_command_log(ed)


    # check if new commands had been added
    @_cleanup
    def _process_command_log(self, ed_self):
        if ed_self is None: return

        cmds = ed_self.get_prop(PROP_COMMAND_LOG)
        if not cmds:
            return

        new_cmds = []
        for cmd in reversed(cmds):
            cmd_id = cmd['code']
            if cmd_id == cmd_None  and  cmd['text'] == HISTORY_TAG:   # stop
                break

            invoke = cmd['invoke']
            if invoke == INVOKE_PALETTE:
                if cmd_id == 1 or cmd_id == 2:
                    continue
            elif invoke == INVOKE_MENU:
                if cmd_id == 1:     # start of command -- find cmd_id by module,method
                    cmd_id = self._get_modmeth_cmd(cmd)
                elif cmd_id == 2:   # end of command - ignore
                    continue
                # else - native menu command -- 'cmd_id' already present
            else:
                continue

            pass;       LOG and print(f'NOTE: added to history: {cmd_id, cmd}')
            new_cmds.append(cmd_id)     # new on top
        #end for

        if new_cmds:
            _new_cmds_set = set(new_cmds)
            for i,cmd_id in enumerate(history):     # remove duplicates of new items
                if cmd_id in _new_cmds_set:
                    del history[i]
            history.extend(reversed(new_cmds))
            del history[0:-self.opt_history_size]   # trim history

        ed_self.cmd(cmd_None, HISTORY_TAG)


    def _get_cmd_name(self, cmd_id):
        if cmd_id not in self._cmd_names:
            if len(self._cmd_names) >= 512:
                self._cmd_names.clear()

            cmd = next((cmd for cmd in self.all_commands  if cmd.get('cmd') == cmd_id), None)
            if cmd is None:
                self._cmd_names[cmd_id] = None
                pass;       LOG and print(f'HistError: no such command: {cmd_id}')
                return

            name = cmd.get('name', '')
            if name:
                self._cmd_names[cmd_id] = name
            elif name is None:  # no such command  (None -- to not check every time)
                self._cmd_names[cmd_id] = None
            else:   # command without a name (just in case)
                (module, method) = cmd.get('p_module'), cmd.get('p_method')
                if module and method:   # show module.method as name
                    self._cmd_names[cmd_id] = '{}.{}'.format(module, method)
                else:   # missing module or name
                    self._cmd_names[cmd_id] = _('Unnamed command: ') + str(cmd_id)
        #end if
        return self._cmd_names.get(cmd_id)


    def _get_modmeth_cmd(self, log_cmd):
        # `modmeth` format:   "text": "py:cuda_breadcrumbs,show_tree,"
        modmeth = log_cmd['text'] # module, method
        if modmeth  and  modmeth not in self._modmeth_cmds:
            if len(self._modmeth_cmds) >= 512:
                self._modmeth_cmds.clear()

            cmd_id = None
            if modmeth.startswith('py:')  and  modmeth[-1] == ',':
                try:
                    (module, method) = modmeth[3:-1].split(',')
                except ValueError:
                    pass;       LOG and print(f'NOTE: weird menu command text 2: {log_cmd}')
                else:
                    # search for command's module and method in `PROC_GET_COMMANDS` for command id
                    for cmd in self.all_commands:
                        if cmd.get('p_module') == module  and   cmd.get('p_method') == method:
                            cmd_id = cmd['cmd']
                            break
                    else:
                        pass;       LOG and print(f'NOTE: can\'t find log cmd for: {log_cmd}')
            else:
                pass;       LOG and print(f'NOTE: weird menu command text 1: {log_cmd}')

            self._modmeth_cmds[modmeth] = cmd_id
        #end if
        return self._modmeth_cmds.get(modmeth)


    def _get_cmdid_by_name(self, name):
        for cmd in self.all_commands:
            if cmd.get('name') == name:
                return cmd['cmd']


    def _save_history(self):
        if history:
            _real_cmd_names = filter(None, map(self._get_cmd_name, history))
            txt = '\n'.join(_real_cmd_names)
            if txt:
                with open(fn_history, 'w') as f:
                    f.write(txt)

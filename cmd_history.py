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
PINNED_PREFIX = 'pinned:'

INVOKE_PALETTE     = 'app_pal'
INVOKE_MENU        = 'menu_main'
INVOKE_MENU_API    = 'menu_api'

cmd_None = 99   # cudatext_cmd.py

history = [] # command codes  (new - last)
pinned = []


def _cleanup(f):
    """ cleanups Command._all_commands after function call """
    def p(self, *args, **vargs): #SKIP
        result = f(self, *args, **vargs)
        self._all_commands = None
        return result
    return p

def unique(seq):
    seen = set()
    for item in seq:
        if item not in seen:
            seen.add( item )
            yield item


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
            history.clear()
            pinned.clear()
            for name in cmd_names:
                is_pinned = False
                if name.startswith(PINNED_PREFIX):
                    is_pinned = True
                    name = name.replace(PINNED_PREFIX, '', 1)
                cmd_id = self._get_cmdid_by_name(name)
                if cmd_id is not None:
                    if is_pinned:   pinned.append(cmd_id)
                    else:           history.append(cmd_id)


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
        def name_to_dlg_item(s): #SKIP
            _spl = s.rsplit(':', 1)[::-1]
            return '\t'.join( map(str.strip, _spl) )

        self._process_command_log(ed)

        if not history  and  not pinned:     return

        dlg_items = []
        ind_to_cmd = {}
        n_pinned = 0
        for i,cmd_id in enumerate(pinned+list(reversed(history))):
            is_pinned = i < len(pinned)
            cmd_name = self._get_cmd_name(cmd_id)
            if not cmd_name:    continue

            prefix = ''
            if is_pinned:
                n_pinned += 1
                prefix = '*{}. '.format(len(dlg_items) + 1)

            dlg_items.append(prefix + name_to_dlg_item(cmd_name))
            ind_to_cmd[len(dlg_items) - 1] = cmd_id

        _focused = n_pinned  if history else  0     # select first not pinned
        res = dlg_menu(DMENU_LIST, dlg_items, focused=_focused, caption=_('Commands history'))

        # test pin/unpin

        if res is not None:
            cmd_id = ind_to_cmd[res]
            # pin/unpin if Control held
            if 'c' not in app_proc(PROC_GET_KEYSTATE, ""):
                ed.cmd(cmd_id)
                # move called command to bottom of history
                if cmd_id in history:
                    history.remove(cmd_id)
                    history.append(cmd_id)
            else:
                if cmd_id in history:
                    history.remove(cmd_id)
                    pinned.append(cmd_id)
                else:
                    pinned.remove(cmd_id)
                    history.insert(0, cmd_id)   # to the bottom of dialog list

                self.show_history()


    def _on_timer(self, tag='', info=''):
        self._process_command_log(ed)


    # check if new commands had been added
    @_cleanup
    def _process_command_log(self, ed_self):
        if ed_self is None: return

        cmds = ed_self.get_prop(PROP_COMMAND_LOG)
        if not cmds:
            return

        new_cmds = False
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
            history.append(cmd_id)
            new_cmds = True
        #end for

        if new_cmds:
            history.reverse()
            history[:] = unique(history)
            history.reverse()
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
            _p_real_cmd_names = filter(None, map(self._get_cmd_name, pinned))
            _h_real_cmd_names = filter(None, map(self._get_cmd_name, history))
            _ptxt = '\n'.join(PINNED_PREFIX+n for n in  _p_real_cmd_names)  # add pinned prefix
            _htxt = '\n'.join(_h_real_cmd_names)
            txt = '\n'.join([_ptxt, _htxt])
            if txt:
                with open(fn_history, 'w') as f:
                    f.write(txt)

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


def format_cmd_name(name):
    if name:
        r = name
    elif name is None:  # no such command  (None -- to not check every time)
        r = None
    else:   # command without a name (just in case)
        (module, method) = cmd.get('p_module'), cmd.get('p_method')
        if module and method:   # show module.method as name
            r = '{}.{}'.format(module, method)
        else:   # missing module or name
            r = _('Unnamed command: ') + str(cmd_id)
    return r


def list_replace(l, v, r):
    for i,lv in enumerate(l):
        if lv == v:
            l[i] = r


class Command:

    def __init__(self):
        self._opt_history_size = None

        self._history = None #[] # command codes|name  (new - last)
        self._pinned = None #[]  # ^

        self._ed = None     # last focused editor
        # cache -- cmd_code -> name  (PROC_GET_COMMANDS: "cmd" -> "name")
        # NOTE: dont show in dialog if value None -- missing command
        self._cmd_names = {}
        self._modmeth_cmds = {} # cache -- command's module+method string  to  command id
        self._apimenu_names = {} # cache -- command's menu path -> cmd name
        self._all_commands = None


    @property
    def history(self):
        """list item can be int (command id) or str (command name)
        """
        if self._history is None:
            self._load_config()
        return self._history

    @property
    def pinned(self):
        """list item can be int (command id) or str (command name)
        """
        if self._pinned is None:
            self._load_config()
        return self._pinned

    @property
    def opt_history_size(self):
        if self._opt_history_size is None:
            self._opt_history_size = int(ini_read(fn_config, CFG_SECTION, 'history_size', '24'))
        return self._opt_history_size

    def all_commands(self):
        if self._all_commands is None:
            self._all_commands = app_proc(PROC_GET_COMMANDS, '')
        return self._all_commands


    @_cleanup
    def _load_config(self):
        self._history = []
        self._pinned = []

        if os.path.isfile(fn_history):
            with open(fn_history, 'r') as f:
                cmd_names = (name.rstrip() for name in f.readlines())
            for name in cmd_names:
                is_pinned = False
                if name.startswith(PINNED_PREFIX):
                    is_pinned = True
                    name = name.replace(PINNED_PREFIX, '', 1)
                cmd = self._get_cmd_by_name(name)
                if cmd is not None:  cmd_item = cmd['cmd']
                else:                cmd_item = name

                if is_pinned:   self.pinned.append(cmd_item)
                else:           self.history.append(cmd_item)


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

        if not self.history  and  not self.pinned:     return
        history = self.history
        pinned = self.pinned

        dlg_items = []
        ind_to_cmd = {}     # can be string - cmd name
        n_pinned = 0
        for i,cmd_item in enumerate(pinned+list(reversed(history))):
            is_pinned = i < len(pinned)
            cmd_name = cmd_item  if isinstance(cmd_item, str) else  self._get_cmd_name(cmd_item)
            if not cmd_name:    continue

            prefix = ''
            if is_pinned:
                n_pinned += 1
                prefix = '*{}. '.format(len(dlg_items) + 1)

            dlg_items.append(prefix + name_to_dlg_item(cmd_name))
            ind_to_cmd[len(dlg_items) - 1] = cmd_item

        _focused = n_pinned  if history else  0     # select first not pinned
        res = dlg_menu(DMENU_LIST, dlg_items, focused=_focused, caption=_('Commands history'))

        # test pin/unpin

        if res is not None:
            cmd_item = ind_to_cmd[res]
            if isinstance(cmd_item, str):
                cmd = self._get_cmd_by_name(cmd_item)
                if cmd is None:
                    print(_('NOTE: Command history: command is missing - {!r}').format(cmd_item))
                    return

                cmd_id = cmd['cmd']
            else:
                cmd_id = cmd_item
            # pin/unpin if Control held
            if 'c' not in app_proc(PROC_GET_KEYSTATE, ""):
                ed.cmd(cmd_id)
                # move called command to bottom of history
                if cmd_item in history:
                    history.remove(cmd_item)
                    history.append(cmd_item)
            else:
                if cmd_item in history:
                    history.remove(cmd_item)
                    pinned.append(cmd_item)
                else:
                    pinned.remove(cmd_item)
                    history.insert(0, cmd_item)   # to the bottom of dialog list

                self.show_history() # repeat show


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
            cmd_item = cmd_id
            if cmd_id == cmd_None  and  cmd['text'] == HISTORY_TAG:   # stop
                break

            invoke = cmd['invoke']
            if invoke == INVOKE_PALETTE:
                if cmd_id == 1 or cmd_id == 2:
                    continue
            elif invoke == INVOKE_MENU:
                if cmd_id == 1:     # start of command -- find cmd_id by module,method
                    cmd_item = self._get_modmeth_cmd(cmd)
                elif cmd_id == 2:   # end of command - ignore
                    continue
                # else: # - native menu command -- 'cmd_id' already present
            elif invoke == INVOKE_MENU_API  and  cmd_id == 1:
                cmd_item = self._get_api_menu_name(cmd)
            else:
                continue

            pass;       LOG and print(f'NOTE: added to history: {cmd_id, cmd_item, cmd}')
            self.history.append(cmd_item)
            new_cmds = True
        #end for

        if new_cmds:
            self.history.reverse()
            self.history[:] = unique(self.history)
            self.history.reverse()
            del self.history[0:-self.opt_history_size]   # trim history

        ed_self.cmd(cmd_None, HISTORY_TAG)


    def _get_cmd_name(self, cmd_id):
        if cmd_id not in self._cmd_names:
            if len(self._cmd_names) >= 512:
                self._cmd_names.clear()

            cmd = next((cmd for cmd in self.all_commands()  if cmd.get('cmd') == cmd_id), None)
            if cmd is None:
                self._cmd_names[cmd_id] = None
                pass;       LOG and print(f'HistError: no such command: {cmd_id}')
                return

            name = cmd.get('name', '')
            self._cmd_names[cmd_id] = format_cmd_name(name)
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
                    for cmd in self.all_commands():
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


    def _get_api_menu_name(self, log_cmd):
        PREFIX = ';m='    # commands 'text's start
        pre_len = len(PREFIX)

        text = log_cmd['text']  # example:  "menuitem=&Tools>less;module=cudatext;func=...
        pre_start = text.rindex(PREFIX)
        mpath_end = text.index(';', pre_start+pre_len) if pre_start >= 0 else -1     # end of menu path
        if pre_start < 0  or  mpath_end < 0:
            pass;       LOG and print(f'NOTE: weird api-menu text: {log_cmd}')
            return

        path = text[pre_start+pre_len: mpath_end]
        if path not in self._apimenu_names:
            cmd_name = None
            # "name": "plugin: Tools: less",  <- command name correspondes to menu path ->  "Tools>less"
            clean_path = [p.strip() for p in path.replace('&', '').split('>')]
            mname = clean_path[-1]
            for c in reversed(self.all_commands()):     # plugin commands are at the end -- reversed
                #if c.get('p_from_api')  and  c['name'].endswith(mname):
                if c['name'].endswith(mname):
                    name_path = [cp.strip() for cp in c['name'].split(':')]
                    path_items_reversed = zip(reversed(clean_path), reversed(name_path))
                    if all(p == cp for p,cp in path_items_reversed):
                        cmd_name = c['name']
                        #self._transients[cmd_id] = c['name']
                        pass;       LOG and print(f'* found app cmd for apimenu item: {c}')
                        break
            else:
                pass;       LOG and print(f'NOTE: no app command for apimenu cmd: {log_cmd}')

            self._apimenu_names[path] = cmd_name
        #end if
        return self._apimenu_names[path]


    def _get_cmd_by_name(self, name):
        for cmd in self.all_commands():
            if cmd.get('name') == name:
                cmd_id = cmd['cmd']
                return cmd


    def _save_history(self):

        def to_cmd_names(l): #SKIP
            for cmd_item in l:
                if isinstance(cmd_item, str):
                    yield cmd_item      # already a name
                    continue
                name = self._get_cmd_name(cmd_item)     # get name from cmd_id
                if name:
                    yield name

        if self.history or self.pinned:
            _p_real_cmd_names = to_cmd_names(self.pinned)
            _h_real_cmd_names = to_cmd_names(self.history)
            _ptxt = '\n'.join(PINNED_PREFIX+n for n in  _p_real_cmd_names)  # add pinned prefix
            _htxt = '\n'.join(_h_real_cmd_names)
            txt = '\n'.join([_ptxt, _htxt])
            if txt:
                with open(fn_history, 'w') as f:
                    f.write(txt)

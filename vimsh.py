################################################################################
#
# file:     vimsh.py
# purpose:  allows execution of shell commands in a vim buffer
#
# author:   brian m sturk   bsturk@adelphia.net,
#                           http://users.adelphia.net/~bsturk
# created:  12/02/01
# last_mod: 11/29/03
# version:  0.17
#
# usage, etc:   see vimsh.readme
# history:      see ChangeLog
# in the works: see TODO
#
###############################################################################

import vim, sys, os, string, signal, re, time

##  If you're having a problem running vimsh, please
##  change the 0 to a 1 and send me an email of the output.

_DEBUG_   = 0
_BUFFERS_ = []

################################################################################

try:
    if sys.platform == 'win32':
        import popen2, stat
        use_pty   = 0

    else:
        import pty, tty, select
        use_pty   = 1

except ImportError:
    print 'vimsh: import error'

################################################################################
##                             class vimsh                                    ##
################################################################################

class vimsh:
    def __init__( self, _sh, _arg, _filename ):

        self.sh        = _sh
        self.arg       = _arg
        self.filename  = _filename

        self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos( )

        self.password_regex   = [ '^Password:',            ##  su
                                  'password:',             ##  ssh
                                  'Password required' ]    ##  ftp

        self.last_cmd_executed     = "foobar"
        self.keyboard_interrupt    = 0
        self.shell_exited          = 0
        self.buffer                = vim.current.buffer

################################################################################

    def setup_pty( self, _use_pty ):

        self.using_pty = _use_pty

        if _use_pty:

            ##  The lower this number is the more responsive some commands
            ##  may be ( printing prompt, ls ), but also the quicker others
            ##  may timeout reading their output ( ping, ftp )

            self.delay = 0.1

            ##  Hack to get pty name until I can figure out to get name
            ##  of slave pty using pty.fork( ) I've tried everything
            ##  including using all of the python src for pty.fork( ).
            ##  I'm probably trying to do something I can't do. However,
            ##  there does seem to be a std call named ptsname( ) which
            ##  returns the slave pty name i.e. /dev/pty/XX

            ##  Assumption is, that between the dummy call to
            ##  master_open is done and the pty.fork happens, we'll be
            ##  the next pty entry after the one from pty.master_open( )
            ##  According to SysV docs it will look for the first
            ##  unused, so this shouldn't be too bad besides its looks.
            ##  Only have to make sure they're not already in use and
            ##  if it is try the next one etc.

            self.master, pty_name = pty.master_open( )
            dbg_print ( 'setup_pty: slave pty name is ' + pty_name )

            self.pid, self.fd = pty.fork( )

            self.outd = self.fd
            self.ind  = self.fd
            self.errd = self.fd

            signal.signal( signal.SIGCHLD, self.sigchld_handler )

            if self.pid == 0:

                ##  In spawned shell process, NOTE: any 'print'ing done within
                ##  here will corrupt vim.

                attrs = tty.tcgetattr( 1 )

                attrs[ 6 ][ tty.VMIN ]  = 1
                attrs[ 6 ][ tty.VTIME ] = 0
                attrs[ 0 ] = attrs[ 0 ] | tty.BRKINT
                attrs[ 0 ] = attrs[ 0 ] & tty.IGNBRK
                attrs[ 3 ] = attrs[ 3 ] & ~tty.ICANON & ~tty.ECHO

                tty.tcsetattr( 1, tty.TCSANOW, attrs )

                dbg_print( 'setup_pty: terminal attributes after setting them' )
                dump_attrs( attrs )

                if self.arg != '':
                    os.execv( self.sh, [ self.sh, self.arg ] )

                else:
                    os.execv( self.sh, [ self.sh, ] )

                ##  dump_attrs( attrs )  <-- TODO: Get this to work

            else:

                try:
                    attrs = tty.tcgetattr( 1 )

                    termios_keys = attrs[ 6 ]

                except:
                    dbg_print ( 'setup_pty: tcgetattr failed' )
                    return

                #  Get *real* key-sequence for standard input keys, i.e. EOF

                self.eof_key   = termios_keys[ tty.VEOF ]
                self.eol_key   = termios_keys[ tty.VEOL ]
                self.erase_key = termios_keys[ tty.VERASE ]
                self.intr_key  = termios_keys[ tty.VINTR ]
                self.kill_key  = termios_keys[ tty.VKILL ]
                self.susp_key  = termios_keys[ tty.VSUSP ]

        else:

            ##  Use pipes on Win32. not as reliable/nice. works OK but with limitations.

            self.delay = 0.2

            try:
                import win32pipe

                dbg_print ( 'setup_pty: using windows extensions' )
                self.stdin, self.stdout, self.stderr = win32pipe.popen3( self.sh + " " + self.arg )

            except ImportError:

                dbg_print ( 'setup_pty: not using windows extensions' )
                self.stdout, self.stdin, self.stderr = popen2.popen3( self.sh + " " + self.arg, -1, 'b' )

            self.outd = self.stdout.fileno( )
            self.ind  = self.stdin.fileno ( )
            self.errd = self.stderr.fileno( )

            self.intr_key = ''
            self.eof_key  = ''

################################################################################

    def execute_cmd( self, _cmd = None, _null_terminate = 1 ):

        if self.keyboard_interrupt:
            dbg_print( 'execute_cmd: keyboard interrupt earlier, cleaning up' )

            self.page_output( 1 )
            self.keyboard_interrupt = 0

            return

        ##  This is the main worker function

        try:
            print ""            ## Clears the ex command window

            cur = self.buffer
            cur_line, cur_row = self.get_vim_cursor_pos( )

            if _cmd == None:

                ## Grab everything from the prompt to the current cursor position.

                _cmd    = cur[ self.prompt_line - 1 : cur_line ]
                _cmd[0] = _cmd[0][ self.prompt_cursor : ]          # remove prompt

            if re.search( r'^\s*\bclear\b', _cmd[0] ) or re.search( r'^\s*\bcls\b', _cmd[0] ):
                dbg_print ( 'execute_cmd: Matched clear' )

                clear_screen()

            elif self.shell_exited or re.search( r'^\s*\exit\b', _cmd[0] ):

                dbg_print ( 'execute_cmd: exit detected' )

                if not self.shell_exited:           ##  process is still around
                    dbg_print ( 'execute_cmd: shell is still around, writing exit command' )
                    self.write( _cmd[0] + '\n' )

                self.shell_exited = 1

                ##  when exiting this way can't have the autocommand
                ##  for BufDelete run.  It crashes vim.  TODO:  Figure this out.

                vim.command( 'au! BufDelete ' + self.filename )
                vim.command( 'bdelete ' + self.filename )

                ## Remove ourself from the list of buffers
                idx = 0

                for key, val in _BUFFERS_:
                    if key == self.filename:
                        break
                    idx = idx + 1

                if ( len( _BUFFERS_ ) >= idx ) & ( len( _BUFFERS_ ) != 0 ):
                    del _BUFFERS_[ idx ]

                return

            else:

                for c in _cmd:
                    if _null_terminate:
                        self.write( c + '\n' )

                    else:
                        self.write( c )

                self.end_exe_line( )

            vim.command( 'startinsert!' )

        except KeyboardInterrupt:

            dbg_print( 'execute_cmd: in keyboard interrupt exception, sending SIGINT' )

            self.keyboard_interrupt = 1

            ##  TODO: Sending Ctrl-C isn't working on Windows yet, so
            ##        executing something like 'findstr foo' will hang.

            if sys.platform != 'win32':
                self.send_intr()

################################################################################

    def end_exe_line( self ):

        ##  read anything that's left on stdout

        cur = self.buffer

        cur.append( "" )
        vim.command( "normal G$" )

        self.read( cur )

        self.check_for_passwd( )

################################################################################

    def write( self, _cmd ):

        dbg_print( "write: Executing cmd --> " + _cmd )

        os.write( self.ind, _cmd )
        self.last_cmd_executed = _cmd

################################################################################

    def read( self, _buffer ):

        num_iterations       = 0      ##  counter for periodic redraw
        iters_before_redraw  = 10
        any_lines_read       = 0      ##  sentinal for reading anything at all

        if sys.platform == 'win32':
            iters_before_redraw = 1 

        while 1:
            if self.using_pty:
                r, w, e = select.select( [ self.outd ], [], [], self.delay )

            else:
                r = [1,]  ##  pipes, unused, fake it out so I don't have to special case

            for file_iter in r:

                lines = ''

                if self.using_pty:
                    lines = os.read( self.outd, 32 )

                else:
                    lines = self.pipe_read( self.outd, 2048 )

                if lines == '':
                    dbg_print( 'read: No more data on stdout pipe_read' )

                    r = []          ##  Sentinel, end of data to read
                    break

                any_lines_read  = 1 
                num_iterations += 1

                lines = self.process_read( lines )
                self.print_lines( lines, _buffer )

                ##  Give vim a little cpu time, so programs that spit
                ##  output or are long operations seem more responsive

                if not num_iterations % iters_before_redraw:
                    dbg_print ( 'read: Letting vim redraw' )
                    vim.command( 'call VimShRedraw()' )

            if r == []:
                dbg_print( 'read: end of data to self.read()' )
                self.end_read( any_lines_read )

                break

################################################################################

    def process_read( self, _lines ):

        dbg_print( 'process_read: Raw lines read from stdout:' )
        dbg_print( _lines )

        lines_to_print = string.split( _lines, '\n' )

        ##  On windows cmd is "echoed" and output sometimes has leading empty line

        if sys.platform == 'win32':
            m = re.search( re.escape( self.last_cmd_executed.strip( ) ), lines_to_print[ 0 ] )

            if m != None or lines_to_print[ 0 ] == "":
                dbg_print( 'process_read: Win32, removing leading blank line' )
                lines_to_print = lines_to_print[ 1: ]

        num_lines = len( lines_to_print )

        ##  Split on '\n' sometimes returns n + 1 entries

        if num_lines > 1:
            last_line = lines_to_print[ num_lines - 1 ].strip( )

            if last_line == "":
                lines_to_print = lines_to_print[ :-1 ]

        errors = self.chk_stderr( )

        if errors:
            dbg_print( 'process_read: Prepending stderr --> ' )
            lines_to_print = errors + lines_to_print

        return lines_to_print

################################################################################

    def print_lines( self, _lines, _buffer ):

        num_lines = len( _lines )

        dbg_print( 'print_lines: Number of lines to print--> ' + str( num_lines ) )

        for line_iter in _lines:

            dbg_print( 'print_lines: Current line is --> %s' %  line_iter )

            m = None

            while re.search( '\r$', line_iter ):

                dbg_print( 'print_lines: removing trailing ^M' )

                line_iter = line_iter[ :-1 ]   #  Force it
                m = True

            ##  Jump to the position of the last insertion to the buffer
            ##  if it was a new line it should be 1, if it wasn't
            ##  terminated by a '\n' it should be the end of the string

            vim.command( "normal " + str( self.prompt_cursor ) + "|" )

            cur_line, cur_row = self.get_vim_cursor_pos( )
            dbg_print( 'print_lines: After jumping to end of last cmd: line %d row %d' % ( cur_line, cur_row ) )

            dbg_print( 'print_lines: Pasting ' + line_iter + ' to current line' )
            _buffer[ cur_line - 1 ] += line_iter

            ##  If there's a '\n' or using pipes and it's not the last line

            if not self.using_pty or m != None:

                dbg_print( 'print_lines: Appending new line since ^M or not using pty' )
                _buffer.append( "" )

            vim.command( "normal G$" )

            self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos( )
            dbg_print( 'print_lines: Saving cursor location: line %d row %d ' % ( self.prompt_line, self.prompt_cursor ) )

################################################################################

    def end_read( self, any_lines_read ):

        cur_line, cur_row = self.get_vim_cursor_pos( )

        if not self.using_pty and any_lines_read:

            ##  remove last line for last read only if lines were
            ##  read from stdout.  TODO: any better way to do this?

            vim.command( 'normal dd' )

        vim.command( "normal G$" )

        ##  Tuck away location, all data read is in buffer

        self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos()

################################################################################

    def pipe_read( self, pipe, minimum_to_read ):

        ##  Hackaround since Windows doesn't support select( ) except for sockets.

        dbg_print( 'pipe_read: minimum to read is ' + str( minimum_to_read ) )
        dbg_print( 'pipe_read: sleeping for ' + str( self.delay ) + ' seconds' )

        time.sleep( self.delay )

        count = 0
        count = os.fstat( pipe )[stat.ST_SIZE]
            
        data = ''

        dbg_print( 'pipe_read: initial count via fstat is ' + str( count ) )

        while ( count > 0 ):

            tmp = os.read( pipe, 1 )
            data += tmp

            count = os.fstat( pipe )[stat.ST_SIZE]

            if len( tmp ) == 0:
                dbg_print( 'pipe_read: count ' + str( count ) + ' but nothing read' )
                break

            ##  Be sure to break the read, if asked to do so,
            ##  after we've read in a line termination.

            if minimum_to_read != 0 and len( data ) > 0 and data[ len( data ) -1 ] == '\n':

                if len( data ) >= minimum_to_read:
                    dbg_print( 'pipe_read: found termination and read at least the minimum asked for' )
                    break

                else:
                    dbg_print( 'pipe_read: not all of the data has been read: count is ' + str( count ) )

        dbg_print( 'pipe_read: returning' )

        return data

################################################################################

    def chk_stderr( self ):

        errors  = ''
        dbg_print( 'chk_stderr: enter' )

        if sys.platform == 'win32':

            err_txt  = self.pipe_read( self.errd, 0 )
            errors   = string.split( err_txt, '\n' )

            num_lines = len( errors )
            dbg_print( 'chk_stderr: Number of error lines is ' + `num_lines` )

            last_line = errors[ num_lines - 1 ].strip( )

            if last_line == "":
                dbg_print( "chk_stderr: Removing last line, it's empty" )
                errors = errors[ :-1 ]

        return errors

################################################################################

    def check_for_passwd( self ):

        cur_line, cur_row = self.get_vim_cursor_pos( )

        prev_line = self.buffer[ cur_line - 1 ]

        for regex in self.password_regex:
            if re.search( regex, prev_line ):

                try:
                    vim.command( 'let password = inputsecret( "Password? " )' )

                except KeyboardInterrupt:
                    return

                password = vim.eval( "password" )

                self.execute_cmd( [password] )       ##  recursive call here...

################################################################################

    def page_output( self, _add_new_line = 0 ):

        dbg_print( 'page_output: enter' )

        try:

            ##  read anything that's left on stdout

            cur = self.buffer

            if _add_new_line :

                cur.append( "" )
                vim.command( "normal G$" )

            self.read( cur )

            self.check_for_passwd( )

            vim.command( "startinsert!" )

        except KeyboardInterrupt:

            dbg_print( 'page_output: exception' )
            pass

################################################################################

    def set_timeout( self ):

        timeout_ok = 0

        while not timeout_ok:

            try:
                vim.command( 'let timeout = input( "Enter new timeout in seconds (i.e. 0.1), currently set to ' + str( self.delay ) + ' :  " )' )

            except KeyboardInterrupt:
                return

            timeout = vim.eval( "timeout" )

            if timeout == "":               ##  usr cancelled dialog, break out
                timeout_ok = 1

            else:
                timeout = float( timeout )
            
                if timeout >= 0.1:
                    print '      --->   New timeout is ' + str( timeout ) + ' seconds'
                    self.delay = timeout
                    timeout_ok = 1

################################################################################

    def clear_screen( self ):

        self.write( "" + "\n" )    ##   new prompt

        if clear_all == '1':
            vim.command( "normal ggdG" )

        self.end_exe_line( )

        if clear_all == '0':
            vim.command( "normal zt" )

################################################################################

    def new_prompt( self ):

        self.execute_cmd( [""] )        #  just press enter

        vim.command( "normal G$" )
        vim.command( "startinsert!" )

################################################################################

    def get_vim_cursor_pos( self ):

        cur_line, cur_row = vim.current.window.cursor
        return cur_line, cur_row + 1

################################################################################

    def cleanup( self ):

        dbg_print( 'cleanup: enter' )

        if self.shell_exited:
            dbg_print( 'cleanup: process is already dead, nothing to do' )
            return

        try:

            if not self.using_pty:
                os.close( self.ind )
                os.close( self.outd )

            os.close( self.errd )       ##  all the same if pty

            if self.using_pty:
                os.kill( self.pid, signal.SIGKILL )

        except:
            dbg_print( 'cleanup: Exception, process probably already killed' )

################################################################################

    def send_intr( self ):

        if show_workaround_msgs == '1':
            print 'If you do NOT see a prompt in the vimsh buffer, press F5 or go into insert mode and press Enter'
            print 'If you need a new prompt press F4'
            print 'NOTE: To disable this help message set \'g:vimsh_show_workaround_msgs\' to 0 in your .vimrc'

        dbg_print( 'send_intr: enter' )

        ##  This triggers another KeyboardInterrupt async

        try:
            dbg_print( 'send_intr: writing intr_key' )
            self.write( self.intr_key )

            dbg_print( 'send_intr: calling page_output' )
            self.page_output( 1 )

            ##  Hack city, doesn't always work either
            #print ' sending carriage return'
            #vim.command( 'let foo = remote_send( v:servername, "<esc>:<CR>" )' )

        except KeyboardInterrupt:

            dbg_print( 'send_intr: caught KeyboardInterrupt in send_intr' )
            pass

################################################################################

    def send_eof( self ):

        dbg_print( 'send_eof: enter' )

        try:
            dbg_print( 'send_eof: writing eof_key' )

            self.write( self.eof_key )

            dbg_print( 'send_eof: calling page_output' )
            self.page_output( 1 )

        except KeyboardInterrupt:
            dbg_print( 'send_eof: caught KeyboardInterrupt in send_eof' )
            pass

################################################################################

    def sigchld_handler( self, sig, frame ):

        dbg_print( 'sigchld_handler: caught SIGCHLD' )
        self.shell_exited = 1

        os.wait()

################################################################################

    def sigint_handler( self, sig, frame ):

        dbg_print( 'sigint_handler: caught SIGINT' )
        dbg_print( '' )

################################################################################
        
    def thread_worker( self ):

        ##  **** Currently unused as it has nasty side effects. ****

        #import thread

        self.idle = 0
        #thread.start_new_thread( self.thread_worker,( ) )

        try:
            while 1:

                if self.idle:
                    r, w, e = select.select( [ self.outd ], [], [], .5 )

                    if r != []:

                        ##  signal vim to come do a read, doesn't
                        ##  work.  Vim can't seem to handle anything
                        ##  coming from another thread.

                        ##  vim.command( 'call VimShReadUpdate()' )

                        ##  interestingly this sorta works
                        ##      gvim --remote-send "<esc>:python vim_shell.page_output(0)<CR>"

                        ##  vim.command( 'let dummy = remote_send( v:servername, "<esc>:call VimShReadUpdate()<cr>" )' )
                        ##  
                        ##  see vimsh.vim for why it's not included

                        dbg_print( 'thread_worker: shouldn\'t be seeing this' )

        except KeyboardInterrupt:                        
            self.send_intr()

################################################################################
##                           Helper functions                                 ##
################################################################################
        
def test_and_set( vim_var, default_val ):

    ret = default_val

    vim.command( 'let dummy = exists( "' + vim_var + '" )' )
    exists = vim.eval( "dummy" )

    ##  exists will always be a string representation of the evaluation

    if exists != '0':
        ret = vim.eval( vim_var )
        dbg_print( 'test_and_set: variable ' + vim_var + ' exists, using supplied ' + ret )

    else:
        dbg_print( 'test_and_set: variable ' + vim_var + ' doesn\'t exist, using default ' + ret )

    return ret

################################################################################

def procs_in_pty( pty_num ):

    procs_in_this_pty = {}

    ##  Hopefully the aux flags are usable on all *nix platforms

    output = os.popen( 'ps aux','r' ).readlines( )

    procs_in_this_pty = {}

    regex = r'\bpts/' + `pty_num` + r'\b'

    for line in output:
        if re.search( regex, line ):
            entries = string.split( line )
            procs_in_this_pty[ entries[ 1 ] ] = entries[ 10 ]  ##  pid is unique

    return procs_in_this_pty

################################################################################

def dump_str_as_hex( _str ):

    hex_str = ''

    print 'length of string is ' + str( len( _str ) )

    for x in range( 0, len( _str ) ):
        hex_str = hex_str + hex( ord( _str[x] ) ) + "\n"

    print 'raw line ( hex ) is:'
    print hex_str

################################################################################

def dump_attrs( _attrs ):

    if not _DEBUG_:
        return

    in_flags = _attrs[ 0 ]

    _flags = []

    if in_flags & tty.BRKINT:   _flags.append( 'BRKINT' )
    if in_flags & tty.ICRNL:    _flags.append( 'ICRNL' )
    if in_flags & tty.IGNBRK:   _flags.append( 'IGNBRK' )
    #if in_flags & tty.IGNCR:    _flags.append( 'IGNCR' )
    #if in_flags & tty.IGNPAR:   _flags.append( 'IGNPAR' )
    #if in_flags & tty.IMAXBEL:  _flags.append( 'IMAXBEL' )
    #if in_flags & tty.INLCR:    _flags.append( 'INLCR' )
    #if in_flags & tty.INPCK:    _flags.append( 'INPCK' )
    #if in_flags & tty.ISTRIP:   _flags.append( 'ISTRIP' )
    #if in_flags & tty.IUCLC:    _flags.append( 'IUCLC' )
    #if in_flags & tty.IXANY:    _flags.append( 'IXANY' )
    #if in_flags & tty.IXOFF:    _flags.append( 'IXOFF' )
    #if in_flags & tty.IXON:     _flags.append( 'IXON' )
    #if in_flags & tty.PARMRK:   _flags.append( 'PARMRK' )

    print 'dump_attrs: input flags are'

    for each in _flags:
        print each

    out_flags = _attrs[ 1 ]

    _flags = []

    #if out_flags & tty.BSDLY:    _flags.append( 'BSDLY' )
    #if out_flags & tty.CLDLY:    _flags.append( 'CLDLY' )
    #if out_flags & tty.FFDLY:    _flags.append( 'FFDLY' )
    #if out_flags & tty.NLDLY:    _flags.append( 'NLDLY' )
    #if out_flags & tty.OCRNL:    _flags.append( 'OCRNL' )
    #if out_flags & tty.OFDEL:    _flags.append( 'OFDEL' )
    #if out_flags & tty.OFILL:    _flags.append( 'OFILL' )
    #if out_flags & tty.OLCUC:    _flags.append( 'OLCUC' )
    #if out_flags & tty.ONLCR:    _flags.append( 'ONLCR' )
    #if out_flags & tty.ONLRET:   _flags.append( 'ONLRET' )
    #if out_flags & tty.ONOCR:    _flags.append( 'ONOCR' )
    #if out_flags & tty.ONOEOT:   _flags.append( 'ONOEOT' )
    #if out_flags & tty.OPOST:    _flags.append( 'OPOST' )
    #if out_flags & tty.OXTABS:   _flags.append( 'OXTABS' )
    #if out_flags & tty.TABDLY:   _flags.append( 'TABDLY' )
    #if out_flags & tty.VTDLY:    _flags.append( 'VTDLY' )

    print 'dump_attrs: output flags are'

    for each in _flags:
        print each

    local_flags = _attrs[ 3 ]

    _flags = []
    
    #if local_flags & tty.ALTWERASE:  _flags.append( 'ALTWERASE' )
    #if local_flags & tty.ECHO:       _flags.append( 'ECHO' )
    #if local_flags & tty.ECHOCTL:    _flags.append( 'ECHOCTL' )
    #if local_flags & tty.ECHOE:      _flags.append( 'ECHOE' )
    #if local_flags & tty.ECHOK:      _flags.append( 'ECHOK' )
    #if local_flags & tty.ECHOKE:     _flags.append( 'ECHOKE' )
    #if local_flags & tty.ECHONL:     _flags.append( 'ECHONL' )
    #if local_flags & tty.ECHOPRT:    _flags.append( 'ECHOPRT' )
    #if local_flags & tty.FLUSHO:     _flags.append( 'FLUSHO' )
    #if local_flags & tty.ICANON:     _flags.append( 'ICANON' )
    #if local_flags & tty.IEXTEN:     _flags.append( 'IEXTEN' )
    #if local_flags & tty.ISIG:       _flags.append( 'ISIG' )
    #if local_flags & tty.NOFLSH:     _flags.append( 'NOFLSH' )
    #if local_flags & tty.NOKERNINFO: _flags.append( 'NOKERNINFO' )
    #if local_flags & tty.PENDIN:     _flags.append( 'PENDIN' )
    #if local_flags & tty.TOSTOP:     _flags.append( 'TOSTOP' )
    #if local_flags & tty.XCASE:      _flags.append( 'XCASE' )

    print 'dump_attrs: local flags are'

    for each in _flags:
        print each

################################################################################

def dbg_print( _str ):

    if _DEBUG_:
        print _str

################################################################################

def new_buf( _filename ):

    ##  If a buffer named vimsh doesn't exist create it, if it
    ##  does, switch to it.  Use the config options for splitting etc.

    filename = _filename

    try:
        vim.command( 'let dummy = buflisted( "' + filename + '" )' )
        exists = vim.eval( "dummy" )

        if exists == '0':
            if split_open == '0':
                vim.command( 'edit ' + filename )

            else:
                vim.command( 'new ' + filename )

            vim.command( 'setlocal buftype=nofile' )
            vim.command( 'setlocal bufhidden=hide' )
            vim.command( 'setlocal noswapfile' )
            vim.command( 'setlocal tabstop=4' )
            vim.command( 'setlocal modifiable' )
            vim.command( 'setlocal nowrap' )
            vim.command( 'setlocal textwidth=999' )   #  BMS: Temporary, see TODO
            vim.command( 'setfiletype vim_shell' )

            vim.command( 'au BufDelete ' + filename + ' :python lookup( "' + filename + '" ).cleanup( )' )
            vim.command( 'inoremap <buffer> <CR>  <ESC>:python lookup( "' + filename + '" ).execute_cmd( )<CR>' )

            vim.command( 'inoremap <buffer> ' + timeout_key + ' <ESC>:python lookup( "' + filename + '" ).set_timeout()<CR>' )
            vim.command( 'nnoremap <buffer> ' + timeout_key + ' :python lookup( "' + filename + '" ).set_timeout()<CR>' )

            vim.command( 'inoremap <buffer> ' + new_prompt_key + ' <ESC>:python lookup ( "' + filename + '" ).new_prompt()<CR>' )
            vim.command( 'nnoremap <buffer> ' + new_prompt_key + ' :python lookup( "' + filename + '" ).new_prompt()<CR>' )

            vim.command( 'inoremap <buffer> ' + page_output_key + ' <ESC>:python lookup ( "' + filename + '" ).page_output()<CR>' )
            vim.command( 'nnoremap <buffer> ' + page_output_key + ' :python lookup( "' + filename + '" ).page_output()<CR>' )

            vim.command( 'inoremap <buffer> ' + eof_signal_key + ' <ESC>:python lookup ( "' + filename + '" ).send_eof()<CR>' )
            vim.command( 'nnoremap <buffer> ' + eof_signal_key + ' :python lookup( "' + filename + '" ).send_eof()<CR>' )

            vim.command( 'inoremap <buffer> ' + intr_signal_key + ' <ESC>:python lookup ( "' + filename + '" ).send_intr()<CR>' )
            vim.command( 'nnoremap <buffer> ' + intr_signal_key + ' :python lookup( "' + filename + '" ).send_intr()<CR>' )

            vim.command( 'inoremap <buffer> ' + clear_key + ' <ESC>:python lookup ( "' + filename + '" ).clear_screen()<CR>')
            vim.command( 'nnoremap <buffer> ' + clear_key + ' :python lookup( "' + filename + '").clear_screen()<CR>' )

            ##  TODO:  Get this working to eliminate need for separate .vim file
            ##  NOTE:  None of the below works... according to a vim developer
            ##         it will work via a patch that has been submitted for
            ##         inclusion

            #vim.command( """\
            #function VimSh()
            #redraw
            #endfunction
            #""" )

            #vim.command('function VimSh(); redraw; endfunction')
            #vim.command('function VimSh()\n redraw\n endfunction')

            #vim.command( 'function VimSh()' )
            #vim.command( 'redraw' )
            #vim.command( 'endfunction' )

            return 0

        else:

            dbg_print( 'new_buf: file ' + filename + ' exists' )

            vim.command( "edit " + filename )
            return 1

    except:
        dbg_print( "new_buf: exception!" + str( sys.exc_info( )[0] ) )

################################################################################

def spawn_buf( _filename ):

    exists = new_buf( _filename )

    if not exists:

        dbg_print( 'spawn_buf: buffer doesn\'t exist so creating a new one' )
        
        cur = vim.current.buffer

        ## Make vimsh associate it with _filename and add to list of buffers
        vim_shell = vimsh( sh, arg, _filename )

        _BUFFERS_.append( ( _filename, vim_shell ) )
        vim_shell.setup_pty( use_pty )

        vim_shell.read( cur )
        cur_line, cur_row = vim_shell.get_vim_cursor_pos( )

        ##  last line *should* be prompt, tuck it away for syntax hilighting
        hi_prompt = cur[ cur_line - 1 ]

    else:
        dbg_print( 'main: buffer does exist' )
        vim.command( "normal G$" )

    vim.command( "startinsert!" )

################################################################################

def lookup ( _filename ):
    for key, val in _BUFFERS_:
        if key == _filename:
            return val

############################# customization ###################################
#
#  Don't edit the lines below, instead set the g:<variable> in your
#  .vimrc to the value you would like to use.  For numeric settings
#  *DO NOT* put quotes around them.  The quotes are only needed in
#  this script.  See vimsh.readme for more details
#
###############################################################################

##  Allow pty prompt override, useful if you have an ansi prompt, etc
#

prompt_override = int( test_and_set( "g:vimsh_pty_prompt_override", "1" ) )

##  Prompt override, used for pty enabled.  Just use a very simple prompt
##  and make no definitive assumption about the shell being used if
##  vimsh_prompt_pty is not set.  This will only be used if
##  vimsh_pty_prompt_override (above) is 1.
##
##  NOTE: [t]csh doesn't use an environment variable for setting the prompt so setting 
##        an override prompt will not work.
#

if use_pty:
    if prompt_override:
        new_prompt = test_and_set( 'g:vimsh_prompt_pty', r'> ' )

        os.environ['prompt'] = new_prompt
        os.environ['PROMPT'] = new_prompt
        os.environ['PS1']    = new_prompt

## shell program and supplemental arg to shell.  If no supplemental
## arg, just use ''
#

if sys.platform == 'win32':
    sh  = test_and_set( 'g:vimsh_sh',     'cmd.exe' )       # NT/Win2k
    arg = test_and_set( 'g:vimsh_sh_arg', '-i' )            

else:    
    sh  = test_and_set( 'g:vimsh_sh',     '/bin/sh' )       # Unix
    arg = test_and_set( 'g:vimsh_sh_arg', '-i' )

## clear shell command behavior
# 0 just scroll for empty screen
# 1 delete contents of buffer
#

clear_all  = test_and_set( "g:vimsh_clear_all", "0" )
                                
## new vimsh window behavior
# 0 use current buffer if not modified
# 1 always split
#

split_open = test_and_set( "g:vimsh_split_open", "1" )

## show helpful (hopefully) messages, mostly for issues that aren't resolved but
## have workarounds
# 0 don't show them, you know what your doing
# 1 show them
#

show_workaround_msgs = test_and_set( "g:vimsh_show_workaround_msgs", "1" )

##  Prompts for the timeouts for read( s )
#
#      set low for local usage, higher for network apps over slower link
#      0.1 sec is the lowest setting
#      over a slow link ( 28.8 ) 1+ seconds works well
#

timeout_key = test_and_set( "g:vimsh_timeout_key", "<F3>" )

##  Create a new prompt at the bottom of the buffer, useful if stuck.
##  Please try to give me a bug report of how you got stuck if possible.

new_prompt_key = test_and_set( "g:vimsh_new_prompt_key", "<F4>" )

##  If output just stops, could be because of short timeouts, allow a key
##  to attempt to read more, rather than sending the <CR> which keeps
##  spitting out prompts.

page_output_key = test_and_set( "g:vimsh_page_output_key", "<F5>" )

##  Send a process SIGINT (INTR) (usually control-C)

intr_signal_key = test_and_set( "g:vimsh_intr_key", "<C-c>" )

##  Send a process EOF (usually control-D) python needs it to
##  quit interactive shell.

eof_signal_key = test_and_set( "g:vimsh_eof_key", "<C-d>" )

##  Clear screen

clear_key = test_and_set( "g:vimsh_clear_key", "<F9>" )

############################ end customization #################################

################################################################################
##                           Main execution code                              ##
################################################################################

dbg_print( 'main: in main execution code' )

##  TODO:  Get this to work for *any* prompt
#vim.command( 'let g:vimsh_prompt="' + hi_prompt + '"' )
#vim.command( 'execute "syntax match VimShPrompt " . "\\"".  escape( g:vimsh_prompt, "~@$" ) . "\\""' )
#vim.command( 'hi link VimShPrompt LineNr' )

#!/usr/bin/env python
#
# file:     vimsh.py
# purpose:  allows execution of shell commands in a vim buffer
#
# author:   brian m sturk   bsturk@nh.ultranet.com,
#                           http://www.nh.ultranet.com/~bsturk
# created:  12/02/01
# last_mod: 12/21/01
# version:  0.10
#
# usage, etc:   see vimsh.readme
# history:      see ChangeLog
# in the works: see TODO
#
###############################################################################

import vim, sys, os, string, signal, re, time

_DEBUG_ = 0

################################################################################
##  If for some reason you'd rather use popenX on a unix variant or
##  the platform doesn't suppport pty, add a check for your platform
##  below also please shoot me an email if you're on a platform besides
##  Windows that doesn't support pty so I can add it to this list
################################################################################

try:
    if sys.platform == 'win32':
        import popen2, stat
        use_pty   = 0

    else:
        import pty, tty, select 
        use_pty   = 1

except ImportError:
    print ( "vimsh: import error" )

################################################################################
##                             class vimsh                                    ##
################################################################################

class vimsh:
    def __init__( self, _sh, _arg, _prompt ):

        self.sh    = _sh
        self.arg   = _arg

        self.pipe_prompt = _prompt
        self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos( )

        self.password_regex   = [ "^Password:",            ##  su
                                  "Password required" ]    ##  ftp

        ##  TODO:  Handle the .exe/.com extension here

        self.unsupp_regex = [ r'^\s*\bftp\b',           ##  ftp
                              r'^\s*\btelnet\b',        ##  telnet
                              r'^\s*\bcleartool\b',     ##  cleartool
                              r'^\s*\bssh\b',           ##  ssh
                              r'^\s*\bpython\b' ]       ##  python ( -u too ATM )

        self.last_cmd_executed = "foobar"

################################################################################

    def write( self, _cmd ):

        dbg_print( "write: Executing cmd --> " + _cmd )

        os.write( self.ind, _cmd )
        self.last_cmd_executed = _cmd

################################################################################

    def read( self, _buffer ):

        num_iterations = 0      ##  counter for periodic redraw

        while 1:

            if self.using_pty:
                r, w, e = select.select( [ self.outd ], [], [], self.delay )

            else:
                r = [1,]  ##  unused, fake it out so I don't have to special case

            for file_iter in r:

                lines = ''

                try:
                    if self.using_pty:

                        lines = os.read( self.outd, 64 )

                    else:
                        lines = self.pipe_read( self.outd )

                        if lines == '':
                            dbg_print( "read: No more data on stdout pipe_read" )

                            r = [];          ##  signal end of data to read
                            break;           ##  out of for loop

                except KeyboardInterrupt:
                    self.interrupt( )

                except:
                    dbg_print ( "read: Unexpected error:" + str( sys.exc_info( )[0] ) )

                num_iterations += 1

                lines = self.process_read( lines )

                self.print_lines( lines, _buffer )

                ##  Give vim a little cpu time, so programs that spit
                ##  output or are long operations seem more responsive

                if not num_iterations % 10:
                    dbg_print ( "read: Letting vim redraw" )
                    vim.command( 'call VimShRedraw()' )

            if r == []:
                dbg_print( "read: No more data read in" );
                self.end_read( _buffer )
                break

################################################################################

    def process_read( self, _lines ):

        try:
            dbg_print( "process_read: Raw lines read from stdout:" ); dbg_print( _lines )

            print_lines = string.split( _lines, '\n' )

            ##  on windows cmd is "echoed" and output sometimes has leading empty line

            if sys.platform == 'win32':
                m = re.search( re.escape( self.last_cmd_executed.strip( ) ), print_lines[ 0 ] )

                if m != None or print_lines[ 0 ] == "":
                    dbg_print( "process_read: Win32, removing leading blank line" )
                    print_lines = print_lines[ 1: ]

            num_lines = len( print_lines )

            ##  split on '\n' sometimes returns n + 1 entries

            if num_lines > 1:
                last_line = print_lines[ num_lines - 1 ].strip( )

                if last_line == "":
                    print_lines = print_lines[ :-1 ]

            errors = self.chk_stderr( )

            if errors:
                dbg_print( "process_read: Prepending stderr --> " )
                print_lines = errors + print_lines

            return print_lines

        except KeyboardInterrupt:
            self.interrupt( )

################################################################################

    def print_lines( self, _lines, _buffer ):

        try:
            num_lines = len( _lines )

            dbg_print( "print_lines: Number of lines to print--> " + `num_lines` )

            for line_iter in _lines:

                dbg_print( "print_lines: Current line is --> %s" %  line_iter )

                m = re.search( "$", line_iter )

                ##  Jump to the position of the last insertion to the buffer
                ##  if it was a new line it should be 1, if it wasn't
                ##  terminated by a '\n' it should be the end of the string

                vim.command( "normal " + str( self.prompt_cursor ) + "|" )

                cur_line, cur_row = self.get_vim_cursor_pos( )
                dbg_print( "print_lines: After jumping to end of last cmd: line %d row %d" % ( cur_line, cur_row ) )

                if self.using_pty and m:          # pty leaves trailing 
                
                    dbg_print( "print_lines: pty, removing trailing ^M" )

                    #  neither of these remove the trailing \n why??
                    #   line_iter.strip( )          
                    #   re.sub( "\n", "", line_iter )

                    line_iter = line_iter[ :-1 ]   # force it

                dbg_print( "print_lines: Pasting " + line_iter + " to current line" )
                _buffer[ cur_line - 1 ] += line_iter

                ##  if there's a '\n' or using pipes and it's not the last line

                if m != None or not self.using_pty:
                    dbg_print( "print_lines: Appending new line since ^M or not using pty" )
                    _buffer.append( "" )

                vim.command( "normal G$" )

                self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos( )
                dbg_print( "print_lines: Saving cursor location: line %d row %d " % ( self.prompt_line, self.prompt_cursor ) )

        except KeyboardInterrupt:
            self.interrupt( )
                
################################################################################

    def end_read( self, _buffer ):

        cur_line, cur_row = self.get_vim_cursor_pos( )

        if not self.using_pty:

            ##  Windows prints out prompt, pipes on Linux do not

            if sys.platform != 'win32':
                dbg_print( "end_read: Printing our pipe prompt" )
                _buffer[ cur_line - 1 ] = self.pipe_prompt 

            else:

                ##  remove last line for last read, TODO: any better way to do this?

                vim.command( 'normal dd' )
        
        vim.command( "normal G$" )

        ##  tuck away location all data read is in buffer

        self.prompt_line, self.prompt_cursor = self.get_vim_cursor_pos( )

################################################################################

    def execute_cmd( self, _cmd = None, _null_terminate = 1 ):

        print ""            ## clears the ex command window

        cur = vim.current.buffer
        cur_line, cur_row = self.get_vim_cursor_pos( )

        if _cmd == None:            ##  grab it current line in buffer
            if cur_line == self.prompt_line and cur_row >= self.prompt_cursor:
                whole_line = cur[ cur_line - 1 ]
                _cmd = whole_line[ self.prompt_cursor: ]

            else:
                return

        ##  check for commands that should be handled differently first

        ##  exit could be handled here, or forwarded on to shell in else

        if re.search( r'^\s*\bexit\b', _cmd ):
            dbg_print ( "execute_cmd: Matched exit" )

            num_procs = 1       ##  default, for windows since no interactive

            if self.using_pty:
                num_procs = procs_in_pty( self.cur_pty )

            if num_procs == 1: 
                self.cleanup( )
                vim.command( "bd!" )

                return

        if re.search( r'^\s*\bclear\b', _cmd ):
            dbg_print ( "execute_cmd: Matched clear" )

            self.write( "" + "\n" )    ##  new prompt

            if clear_all:
                vim.command( "normal ggdG" )

            ret = self.end_exe_line( )

            if ret == -1:
                return

            if not clear_all:
                vim.command( "normal zt" )

        else:

            ##  first check for interactive commands under windows

            if sys.platform == 'win32':

                dbg_print( "execute_cmd: Checking for unsupported windows cmds" )

                for regex in self.unsupp_regex:
                    m = re.search( regex, _cmd )

                    if m:
                        dbg_print( "execute_cmd: Found a match" )

                        vim.command( 'let continue = input( "The console version of ' + m.group( 0 ) + ' is unsupported on Windows.  Continue anyway? y/n " )' )
                        exe_it = vim.eval( "continue" )

                        print ""            ## clears the ex command window

                        if( string.upper( exe_it ) != 'Y' ):
                            dbg_print( "execute_cmd: Unsupported and not-executing" )
                            return

            if _null_terminate:
                self.write( _cmd + "\n" )

            else:
                ##  TODO:  Not working yet, allows for sending <Tab> etc...
                self.write( _cmd )

            ret = self.end_exe_line( )

            if ret == -1:
                return

        vim.command( "startinsert" )

################################################################################

    def end_exe_line ( self ):

        cur = vim.current.buffer
        cur.append( "" )
        vim.command( "normal G$" )

        try:
            ret = self.read( cur )

        except KeyboardInterrupt:

            ##  handle cmds like ping <host> which will have output
            ##  until interrupted

            dbg_print( "end_exe_line: Cmd interrupted while reading" )
            self.interrupt( )

            ret = -1

        if ret == -1:
            return -1

        self.check_for_passwd( )

################################################################################

    ##  Hackaround since Windows doesn't support select( ) except for sockets.

    def pipe_read( self, pipe ):

        time.sleep( self.delay )

        count = 0
        count = os.fstat( pipe )[stat.ST_SIZE]
            
        data = ''
        while ( count > 0 ):
            data += os.read( pipe, 1 )
            count = os.fstat( pipe )[stat.ST_SIZE]

        return data

################################################################################

    def chk_stderr( self ):

        errors = ''

        if sys.platform == 'win32':

            err_txt  = self.pipe_read( self.errd )
            errors   = string.split( err_txt, '\n' )

            num_lines = len( errors )
            dbg_print( "chk_stderr: Number of error lines is " + `num_lines` )

            last_line = errors[ num_lines - 1 ].strip( )

            if last_line == "":
                dbg_print( "chk_stderr: Removing last line, it's empty" )
                errors = errors[ :-1 ]

        return errors

################################################################################

    def setup_pty( self, _use_pty ):

        self.using_pty = _use_pty

        if _use_pty:
            self.delay = 0.2

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
            ##  unused, so this shouldn't be too bad besides it's looks.
            ##  Only have to make sure they're not already in use and
            ##  if it is try the next one etc.

            self.master, pty_name = pty.master_open( )
            dbg_print ( "setup_pty: Dummy pty name is " + pty_name )

            m = re.search( r"pts/(\d*)", pty_name )
            self.cur_pty = int( m.group( 1 ) ) + 1

            dbg_print ( "setup_pty: Next pty num is " + `self.cur_pty` )

            ##  TODO: Check to see if it's already in use, and keep
            ##        bumping number until we find the one not in use

            entries = os.listdir( "/dev/pts" ) 

            if entries:         ##  just in case, don't most *nix have /dev?
                while 1:
                    if str( self.cur_pty ) in entries:
                        self.cur_pty += 1
                    else:
                        dbg_print ( "setup_pty: Found a non-matching pty " + `self.cur_pty` + "using it" )
                        break

            self.pid, self.fd = pty.fork( )

            self.outd = self.fd
            self.ind  = self.fd
            self.errd = self.fd

            if self.pid == 0:

                attrs = tty.tcgetattr( 1 )
                attrs[6][tty.VMIN]  = 1
                attrs[6][tty.VTIME] = 0
                attrs[0] = attrs[0] | tty.BRKINT
                attrs[3] = attrs[3] & ~tty.ICANON & ~tty.ECHO
                tty.tcsetattr( 1, tty.TCSANOW, attrs )

                os.execv( self.sh, [ self.sh, self.arg ] )

        else:

            ##  use pipes. not as reliable/nice. works OK but with limitations.
            ##  Needed for Windows support.

            self.delay = 0.2

            self.stdout, self.stdin, self.stderr = popen2.popen3( self.sh + " " + self.arg, bufsize=-1  )

            #import win32pipe       #  no better :(
            #self.stdin, self.stdout, self.stderr = win32pipe.popen3( self.sh + " " + self.arg )

            self.outd = self.stdout.fileno( )
            self.ind  = self.stdin.fileno ( )
            self.errd = self.stderr.fileno( )

################################################################################

    def page_output( self ):

        ##  read anything that's left on stdout

        ret = self.read( cur )

        vim.command( "normal G$" )
        vim.command( "startinsert" )

################################################################################

    def check_for_passwd( self ):

        cur_line, cur_row = self.get_vim_cursor_pos( )

        prev_line = cur[ cur_line - 1 ]

        for regex in self.password_regex:

            if re.search( regex, prev_line ):

                try:
                    vim.command( 'let password = inputsecret( "Password? " )' )

                except KeyboardInterrupt:
                    return

                password = vim.eval( "password" )

                self.execute_cmd( password )       ##  recursive call here...

################################################################################

    def set_timeout( self ):

        timeout_ok = 0

        while not timeout_ok:

            try:
                vim.command( 'let timeout = input( "New timeout ( in seconds, i.e. 1.2 ) " )' )

            except KeyboardInterrupt:
                return

            timeout = vim.eval( "timeout" )

            if timeout == "":               ##  usr cancelled dialog, break out
                timeout_ok = 1

            else:
                timeout = float( timeout )
            
                if timeout >= 0.1:
                    print "      --->   New timeout is " + str( timeout ) + " seconds"
                    self.delay = timeout
                    timeout_ok = 1

################################################################################

    def new_prompt( self ):

        if use_pty:
            self.execute_cmd( "" )        #  just press enter

        else:
            cur[ cur_line - 1 ] = self.pipe_prompt

        vim.command( "normal G$" )
        vim.command( "startinsert" )

################################################################################

    def get_vim_cursor_pos( self ):

        cur_line, cur_row = vim.current.window.cursor
        return cur_line, cur_row + 1

################################################################################

    def cleanup( self ):

        dbg_print( "cleanup" )

        try:
            if not self.using_pty:
                os.close( self.outd )
                os.close( self.ind )

            os.close( self.errd )       ##  all the same if pty

            os.kill( self.pid, signal.SIGKILL )

        except:
            dbg_print( "cleanup: Exception, process probably already killed" )

################################################################################

    def interrupt( self ):

        if not self.using_pty:     ##  these types of cmds only work on pty
            return

        ##  Give a lists of currently running children if more than one
        ##  and ask which one to send signal to, or if there's only one besides
        ##  the shell send it there

        procs_in_this_pty = procs_in_pty( self.cur_pty )

        num_found = len( procs_in_this_pty )
        dbg_print ( "interrupt: Number procs found is " + `num_found` )

        pid_to_signal = 0

        if num_found > 1:
            ##  just kill one other than shell we exec'ed
            if num_found == 2:
                for key in procs_in_this_pty.keys( ):

                    app = procs_in_this_pty[ key ]

                    if not re.search( app, self.sh ):
                        pid_to_signal = key

            ##  put up a list to select from

            else:
                print "** Current processes for this shell session **"

                for key in procs_in_this_pty.keys( ):
                    print key, procs_in_this_pty[ key ]

                    try:
                        vim.command( 'let choice = input( "Enter which pid to send SIGINT ? " )' )

                    except KeyboardInterrupt:
                        return

                    choice = vim.eval( "choice" )

                    if choice == "":               ##  usr cancelled dialog, break out
                        return

                    else:
                        if choice not in procs_in_this_pty.keys( ):
                            return

                        pid_to_signal = choice
           
            dbg_print( "\\n" )
            dbg_print( "interrupt: Sending SIGINT to pid " + pid_to_signal )

            os.kill( int( pid_to_signal ), signal.SIGINT )

            ##  read anything that's left on stdout

            dbg_print( "interrupt: Reading what's left" )
            self.read( cur )

            vim.command( "normal G$" )
            vim.command( "startinsert" )

        print ""

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

    return ret

################################################################################

def procs_in_pty( pty_num ):

    procs_in_this_pty = {}

    ##  hopefully the aux flags are usable on all *nix platforms

    output = os.popen( 'ps aux','r' ).readlines( )

    procs_in_this_pty = {}

    regex = r'\bpts/' + `pty_num` + r'\b'

    for line in output:
        if re.search( regex, line ):
            entries = string.split( line )
            procs_in_this_pty[ entries[ 1 ] ] = entries[ 10 ]  ##  pid is unique

    return procs_in_this_pty

################################################################################

def dbg_dump_str_as_hex( _str ):

    if _DEBUG_:
        hex_str = ''

        for x in range( 0, len( _str ) ):
             hex_str = hex_str + hex( ord( _str[x] ) ) + "\n"

        print "raw line ( hex ) is:"
        print hex_str

################################################################################
        
def dbg_print( _str ):

    if _DEBUG_:
        print _str

################################################################################

def new_buf( ):

    ##  If a buffer named vim_shell doesn't exist create it, if it
    ##  does, switch to it.  Use the config options for splitting etc.

    filename = "vim_shell"

    try:
        vim.command( 'let dummy = buflisted( "' + filename + '" )' )
        exists = vim.eval( "dummy" )

        if exists == '0':

            if split_open:
                vim.command( "new vim_shell" )
            else:
                vim.command( "edit vim_shell" )

            vim.command( "setlocal buftype=nofile" )
            vim.command( "setlocal tabstop=8" )
            vim.command( "setlocal modifiable" )
            vim.command( "setlocal noswapfile" )
            vim.command( "setlocal nowrap" )

            vim.command( "inoremap <buffer> <CR>  <esc>:python vim_shell.execute_cmd( )<CR>" )
            vim.command( "au BufWipeout vim_shell <esc>:python vim_shell.cleanup( )<CR>" )

            vim.command( 'inoremap <buffer> <C-c> <esc>:python vim_shell.interrupt( )<CR>' )
            vim.command( 'nnoremap <buffer> <C-c> :python vim_shell.interrupt( )<CR>' )

            vim.command( 'inoremap <buffer> ' + timeout_key + ' <esc>:python vim_shell.set_timeout( )<CR>' )
            vim.command( 'nnoremap <buffer> ' + timeout_key + ' :python vim_shell.set_timeout( )<CR>' )

            vim.command( 'inoremap <buffer> ' + new_prompt_key + '  <esc>:python vim_shell.new_prompt( )<CR>' )
            vim.command( 'nnoremap <buffer> ' + new_prompt_key + '  :python vim_shell.new_prompt( )<CR>' )

            vim.command( 'inoremap <buffer> ' + page_output_key + ' <esc>:python vim_shell.page_output( )<CR>' )
            vim.command( 'nnoremap <buffer> ' + page_output_key + ' :python vim_shell.page_output( )<CR>' )

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

            vim.command( "edit vim_shell" )
            return 1

    except:
        dbg_print( "new_buffer: exception!" + str( sys.exc_info( )[0] ) )

################################################################################

############################# customization ###################################
#
#  Don't edit the lines below, instead set the g:<variable> in your
#  .vimrc to the value you would like to use.  For numeric settings
#  *DO NOT* put quotes around them.  The quotes are only needed in
#  this script.  See vimsh.readme for more details
#
###############################################################################

#  Non pty prompt, unused on Windows also.  May not be needed
#

pipe_prompt = test_and_set( "g:vimsh_pipe_prompt", "%> " )

#  Allow pty prompt override, useful if you have a ansi prompt
#

prompt_override = int( test_and_set( "g:vimsh_pty_prompt_override", "1" ) )

##  Prompt override, used for pty enabled
#

if use_pty:
    if prompt_override:

        if re.search( "bsd", sys.platform ):        ##  csh
            tmp = test_and_set( "g:vimsh_prompt_pty", r"[%m:%c3] %n%#" )

        else:                                       ##  bash
            tmp = test_and_set( "g:vimsh_prompt_pty", r"\u@\h:\w\$ " )

        os.environ['prompt'] = tmp
        os.environ['PROMPT'] = tmp
        os.environ['PS1']    = tmp

## shell program and supplemental arg to shell
#

if sys.platform == 'win32':
    sh  = test_and_set( "g:vimsh_sh",     "cmd.exe" )       # NT/Win2k
    arg = test_and_set( "g:vimsh_sh_arg", "" )

else:    
    sh  = test_and_set( "g:vimsh_sh",     "/bin/sh" )       # Unix
    arg = test_and_set( "g:vimsh_sh_arg", "-i" )

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

##  Prompts for the timeouts for read( s )
#
#      set low for local usage, higher for network apps over slower link
#      0.1 sec is the lowest setting
#      over a slow link ( 28.8 ) 1+ seconds works well
#

timeout_key = test_and_set( "g:vimsh_timeout_key", "<F3>" )

##  Create a new prompt at the bottom of the buffer, useful if stuck

new_prompt_key = test_and_set( "g:vimsh_new_prompt_key", "<F4>" )

##  If output just stops, could be because of short timeouts, allow a key
##  to attempt to read more, rather than sending the <CR> which keeps
##  spitting out prompts.

page_output_key = test_and_set( "g:vimsh_page_output_key", "<F5>" )

############################ end customization #################################

################################################################################
##                           Main execution code                              ##
################################################################################

exists = new_buf( )

if not exists:
    cur = vim.current.buffer

    vim_shell = vimsh( sh, arg, pipe_prompt )
    vim_shell.setup_pty( use_pty )

    vim_shell.read( cur )
    cur_line, cur_row = vim_shell.get_vim_cursor_pos( )

    if use_pty or sys.platform == 'win32':

        ##  last line *should* be prompt, tuck it away for syntax hilighting
        hi_prompt = cur[ cur_line - 1 ]

    else:
        hi_prompt = pipe_prompt          ##  print non-pty prompt on platforms that don't
        vim_shell.new_prompt( )

else:
    vim.command( "normal G$" )

vim.command( "startinsert" )

##  TODO:  Get this to work for *any* prompt
#vim.command( 'let g:vimsh_prompt="' + hi_prompt + '"' )
#vim.command( 'execute "syntax match VimShPrompt " . "\\"".  escape( g:vimsh_prompt, "~@$" ) . "\\""' )
#vim.command( 'hi link VimShPrompt LineNr' )

"  file:     vimsh.vim
"  purpose:  support file for vimsh, when sourced starts a vimsh buffer
" 
" author:   brian m sturk   bsturk@nh.ultranet.com,
"                           http://www.nh.ultranet.com/~bsturk
" created:  12/20/01
" last_mod: 12/21/01
" version:  see vimsh.py
" 
" usage:          :so[urce] vimsh.vim

function! VimShRedraw()
    redraw
endfunction

pyfile <sfile>:p:h/vimsh.py

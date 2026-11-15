# HAMCP-371 S7: promoted receipts and the output-key contract

## Component 2.2.1 tree list

`component-2.2.1-tree.txt` lists the `ha_mcp_tools` component tree at upstream
tag `v8.6.0`, commit `fc54437a`, as C4 requires for the SR 8.5.0 control. It
has 54 files. It was read from the upstream store by git plumbing, with lazy
fetch off.

- sha256 `26524feadbbec8e5815bcd54c32a3b5d01d85896f889e8ca9ddc754e1f70bdf0`.

The leak check passed on it before the copy, and `s6/comparison/scan.py`
found 0 hits. Before SR, the component tree built for the control is checked
against this list (the upstream store gap).

import gedit, gtk, gtk.glade
import gconf
import pygtk
pygtk.require('2.0')
import os, os.path, gobject
from urllib import pathname2url, url2pathname
from suggestion import FuzzySuggestion
from util import debug

max_result = 50
app_string = "Fuzzy open"

ui_str="""<ui>
<menubar name="MenuBar">
  <menu name="FileMenu" action="File">
    <placeholder name="FileOps_2">
      <menuitem name="FuzzyOpen" action="FuzzyOpenAction"/>
    </placeholder>
  </menu>
</menubar>
</ui>
"""

# essential interface
class FuzzyOpenPluginInstance:
  def __init__( self, plugin, window ):
    self._window = window
    self._plugin = plugin
    self._encoding = gedit.encoding_get_current()
    self._rootpath = os.getcwd()
    self._rootdir = "file://" + self._rootpath
    self._show_hidden = False
    self._suggestion = None
    self._git = False
    self._git_with_diff = None
    self._git_files = None
    self._liststore = None
    self._last_pattern = ""
    self._init_glade()
    self._insert_menu()

  def deactivate( self ):
    self._remove_menu()
    self._action_group = None
    self._window = None
    self._plugin = None
    self._liststore = None;

  def update_ui( self ):
    return

  # MENU STUFF
  def _insert_menu( self ):
    manager = self._window.get_ui_manager()
    self._action_group = gtk.ActionGroup( "FuzzyOpenPluginActions" )
    fuzzyopen_menu_action = gtk.Action( name="FuzzyOpenMenuAction", label="Fuzzy", tooltip="Fuzzy tools", stock_id=None )
    self._action_group.add_action( fuzzyopen_menu_action )
    fuzzyopen_action = gtk.Action( name="FuzzyOpenAction", label="Fuzzy Open...\t", tooltip="Open file by autocomplete...", stock_id=gtk.STOCK_JUMP_TO )
    fuzzyopen_action.connect( "activate", lambda a: self.on_fuzzyopen_action() )
    self._action_group.add_action_with_accel( fuzzyopen_action, "<Ctrl><Alt>o" )
    manager.insert_action_group( self._action_group, 0 )
    self._ui_id = manager.new_merge_id()
    manager.add_ui_from_string( ui_str )
    manager.ensure_update()

  def _remove_menu( self ):
    manager = self._window.get_ui_manager()
    manager.remove_ui( self._ui_id )
    manager.remove_action_group( self._action_group )

  # UI DIALOGUES
  def _init_glade( self ):
    self._fuzzyopen_glade = gtk.glade.XML( os.path.dirname( __file__ ) + "/fuzzyopen.glade" )
    #setup window
    self._fuzzyopen_window = self._fuzzyopen_glade.get_widget( "FuzzyOpenWindow" )
    self._fuzzyopen_window.connect("key-release-event", self.on_window_key)
    self._fuzzyopen_window.connect("delete_event", self._fuzzyopen_window.hide_on_delete)
    self._fuzzyopen_window.set_transient_for(self._window)
    #setup buttons
    self._fuzzyopen_glade.get_widget( "ok_button" ).connect( "clicked", self.open_selected_item )
    self._fuzzyopen_glade.get_widget( "cancel_button" ).connect( "clicked", lambda a: self._fuzzyopen_window.hide())
    #setup entry field
    self._glade_entry_name = self._fuzzyopen_glade.get_widget( "entry_name" )
    self._glade_entry_name.connect("key-release-event", self.on_pattern_entry)
    #setup list field
    self._hit_list = self._fuzzyopen_glade.get_widget( "hit_list" )
    self._hit_list.connect("select-cursor-row", self.on_select_from_list)
    self._hit_list.connect("button_press_event", self.on_list_mouse)
    self._liststore = gtk.ListStore(str, str, str)
    self._hit_list.set_model(self._liststore)
    column0 = gtk.TreeViewColumn("Token", gtk.CellRendererText(), markup=0)
    column0.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
    column1 = gtk.TreeViewColumn("File", gtk.CellRendererText(), markup=1)
    column1.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
    self._hit_list.append_column(column0)
    self._hit_list.append_column(column1)
    self._hit_list.get_selection().set_mode(gtk.SELECTION_MULTIPLE)

  #mouse event on list
  def on_list_mouse( self, widget, event ):
    if event.type == gtk.gdk._2BUTTON_PRESS:
      self.open_selected_item( event )

  #key selects from list (passthrough 3 args)
  def on_select_from_list(self, widget, event):
    self.open_selected_item(event)

  #keyboard event on entry field
  def on_pattern_entry( self, widget, event ):
    oldtitle = self._fuzzyopen_window.get_title().replace(" * too many hits", "")
    if event.keyval == gtk.keysyms.Return:
      self.open_selected_item( event )
      return
    pattern = self._glade_entry_name.get_text()
    if pattern == self._last_pattern:
      return
    self._last_pattern = pattern
    suggestions = self._suggestion.suggest(pattern)
    self._liststore.clear()
    maxcount = 0
    for highlight, file in suggestions:
      fileroot = self._rootpath
      highlight += "\nMODIFY " + util.relative_time(os.stat(fileroot + "/" + file).st_mtime)
      if self._git:
        try:
          index = self._git_files.index(file)
          highlight += self.get_git_string(index)
        except ValueError:
          pass
      self._liststore.append([self.get_token_string( file ), highlight, file])
      if maxcount > max_result:
        break
      maxcount = maxcount + 1
    if maxcount > max_result:
      oldtitle = oldtitle + " * too many hits"
    self._fuzzyopen_window.set_title(oldtitle)

    selected = []
    self._hit_list.get_selection().selected_foreach(self.foreach, selected)

    if len(selected) == 0:
      iter = self._liststore.get_iter_first()
      if iter != None:
        self._hit_list.get_selection().select_iter(iter)

  def get_token_string( self, file ):
    token = os.path.splitext(file)[-1]
    if token != '':
      token = token[1:]
    else:
      token = '.'
    return "<span variant='smallcaps' size='x-large' foreground='#FFFFFF' background='#929292'><b>" + token.upper() + '</b></span>'

  def get_git_diff( self ):
    self._git_with_diff = os.popen("cd " + self._rootpath + "; git diff --numstat ").readlines()
    self._git_with_diff = [ s.strip().split('\t') for s in self._git_with_diff ]
    self._git_files = [ s[2] for s in self._git_with_diff ]

  def get_git_string( self, line_id ):
    add = int(self._git_with_diff[line_id][0])
    delete = int(self._git_with_diff[line_id][1])
    if add != 0 or delete != 0:
      return "  GIT <tt><span foreground='green'>" + ('+' * add) + "</span><span foreground='red'>" + ('-' * delete) + "</span></tt>"
    else:
      return ""

  #on menuitem activation (incl. shortcut)
  def on_fuzzyopen_action( self ):
    fbroot = util.filebrowser_root()
    if fbroot[0] != "" and fbroot[0] is not None:
      self._rootdir = fbroot[0]
      self._show_hidden = fbroot[1]
      self._fuzzyopen_window.set_title(app_string + " (File Browser root)")
    else:
      eddtroot = util.eddt_root()
      if eddtroot != "" and eddtroot is not None:
        self._rootdir = eddtroot
        self._fuzzyopen_window.set_title(app_string + " (EDDT integration)")
      else:
        self._fuzzyopen_window.set_title(app_string + " (Working dir): " + self._rootdir)
    # Get rid of file://
    debug("Rootdir = " + self._rootdir)
    self._rootpath = url2pathname(self._rootdir)[7:]
    debug("Rootpath = "+ self._rootpath)
    self._suggestion = FuzzySuggestion( self._rootpath )
    if os.path.exists( os.path.join( self._rootpath, ".git" ) ):
      self._git = True
      self.get_git_diff()
    debug("Use Git = " + str(self._git))
    self._fuzzyopen_window.show()
    self._glade_entry_name.select_region(0,-1)
    self._glade_entry_name.grab_focus()

  #on any keyboard event in main window
  def on_window_key( self, widget, event ):
    if event.keyval == gtk.keysyms.Escape:
      self._fuzzyopen_window.hide()

  def foreach(self, model, path, iter, selected):
    selected.append(model.get_value(iter, 2))

  #open file in selection and hide window
  def open_selected_item( self, event ):
    selected = []
    self._hit_list.get_selection().selected_foreach(self.foreach, selected)
    for selected_file in  selected:
      self._open_file ( selected_file )
    self._fuzzyopen_window.hide()

  #opens (or switches to) the given file
  def _open_file( self, filename ):
    uri = self._rootdir + "/" + pathname2url(filename)
    gedit.commands.load_uri(self._window, uri, self._encoding)

# STANDARD PLUMMING
class FuzzyOpenPlugin( gedit.Plugin ):
  DATA_TAG = "FuzzyOpenPluginInstance"

  def __init__( self ):
    gedit.Plugin.__init__( self )

  def _get_instance( self, window ):
    return window.get_data( self.DATA_TAG )

  def _set_instance( self, window, instance ):
    window.set_data( self.DATA_TAG, instance )

  def activate( self, window ):
    self._set_instance( window, FuzzyOpenPluginInstance( self, window ) )

  def deactivate( self, window ):
    self._get_instance( window ).deactivate()
    self._set_instance( window, None )

  def update_ui( self, window ):
    self._get_instance( window ).update_ui()


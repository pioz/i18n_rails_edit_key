# -*- coding: utf-8 -*-
import os
import re
import json
import tempfile
import subprocess
import sublime
import sublime_plugin

SETTINGS_FILE = "I18nRailsEditKey.sublime-settings"

# ---------- Utilities ----------

def find_project_root(start_path, marker_subpath=os.path.join("config", "locales")):
    """Find Rails project root by locating config/locales upwards from start_path."""
    if start_path and os.path.isfile(start_path):
        path = os.path.dirname(start_path)
    else:
        path = start_path or ""
    while path:
        candidate = os.path.join(path, marker_subpath)
        if os.path.isdir(candidate):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    # fallback: check opened folders
    win = sublime.active_window()
    if win:
        for f in win.folders() or []:
            candidate = os.path.join(f, marker_subpath)
            if os.path.isdir(candidate):
                return f
    return None


def strip_view_extensions(fname):
    """Remove Rails template extensions like .html.erb, .erb, .slim, .haml, etc., and leading '_' in partials."""
    base = os.path.basename(fname)
    base = base[1:] if base.startswith("_") else base
    while True:
        name, ext = os.path.splitext(base)
        if not ext:
            break
        if ext.lower() in (".erb", ".haml", ".slim", ".rhtml", ".builder", ".jbuilder",
                           ".html", ".text", ".txt", ".json", ".xml", ".js", ".css",
                           ".scss", ".sass"):
            base = name
        else:
            break
    return base


def infer_controller_action_at_point(view, pt):
    """
    Best effort: find the closest 'def <action>' above the cursor.
    Returns the action name as string or None if not found.
    """
    text = view.substr(sublime.Region(0, pt))
    matches = list(re.finditer(r'^[ \t]*def[ \t]+([a-zA-Z0-9_!?]+)', text, flags=re.MULTILINE))
    if not matches:
        return None
    return matches[-1].group(1)


def resolve_relative_key(view, rel_key):
    """
    Resolve a relative i18n key (e.g., '.search') using Rails conventions.

    - In views:  app/views/foo/bar/_baz.html.erb -> 'foo.bar.baz.<rel>'
    - In controllers: app/controllers/admin/users_controller.rb inside def show
                      -> 'admin.users.show.<rel>'
    """
    file_path = view.file_name() or ""
    if not file_path:
        return None

    norm_path = (file_path.replace("\\", "/"))

    # Controllers
    if "/app/controllers/" in norm_path and norm_path.endswith("_controller.rb"):
        after = norm_path.split("/app/controllers/", 1)[1]
        ctrl_base = re.sub(r"_controller\.rb$", "", after)
        controller_scope = ".".join([p for p in ctrl_base.split("/") if p])
        caret = view.sel()[0].begin() if view.sel() else 0
        action = infer_controller_action_at_point(view, caret) or "index"
        return "{}.{}.{}".format(controller_scope, action, rel_key.lstrip("."))

    # Views
    parts = norm_path.split("/")
    scope_parts = None
    try:
        idx_app = parts.index("app")
        if idx_app + 1 < len(parts) and parts[idx_app + 1] == "views":
            path_after_views = parts[idx_app + 2:]
            if not path_after_views:
                return None
            scope_parts = path_after_views[:-1] + [strip_view_extensions(path_after_views[-1])]
    except ValueError:
        pass
    if scope_parts is None:
        try:
            idx_views = parts.index("views")
            path_after_views = parts[idx_views + 1:]
            if not path_after_views:
                return None
            scope_parts = path_after_views[:-1] + [strip_view_extensions(path_after_views[-1])]
        except ValueError:
            return None

    scope_parts = [p for p in scope_parts if p]
    scope = ".".join(scope_parts)
    return scope + "." + rel_key.lstrip(".")


def list_locales(locales_dir):
    """
    Detect locales by filename patterns:
      - Primary:   <locale>.yml
      - Extra:     <anything>.<locale>.yml  (e.g., doorkeeper.en.yml)
    Return list of tuples: [(locale, primary_path_or_default), ...] with unique locales.
    """
    if not os.path.isdir(locales_dir):
        return []

    primary = {}
    seen_locales = set()

    for name in os.listdir(locales_dir):
        if not name.lower().endswith(".yml"):
            continue
        base = os.path.splitext(name)[0]

        m_primary = re.match(r"^([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)$", base)
        if m_primary:
            loc = m_primary.group(1).replace("_", "-")
            primary[loc] = os.path.join(locales_dir, name)
            seen_locales.add(loc)
            continue

        m_extra = re.match(r"^.+\.([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)$", base)
        if m_extra:
            loc = m_extra.group(1).replace("_", "-")
            seen_locales.add(loc)

    out = []
    for loc in sorted(seen_locales):
        prim = primary.get(loc, os.path.join(locales_dir, loc + ".yml"))
        out.append((loc, prim))
    return out


def run_ruby(ruby_path, script, args):
    """Run an inline Ruby script with arguments, return (ok, stdout)."""
    ruby = ruby_path or "ruby"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as tf:
        tf.write(script.encode("utf-8"))
        tf.flush()
        cmd = [ruby, tf.name] + list(args)
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return (True, out.decode("utf-8", errors="replace"))
        except subprocess.CalledProcessError as e:
            return (False, e.output.decode("utf-8", errors="replace"))
        finally:
            try:
                os.unlink(tf.name)
            except Exception:
                pass


def extract_key_from_t_call(view):
    """
    Return the i18n key (string) if the current selection/caret is inside
    a t('...') or I18n.t('...') call on the same line. Otherwise return None.

    Accepts selections like:
      - my_key          (inside the quotes)
      - 'my_key'        (selection includes quotes)
      - t('my_key')     (selection covers the whole call)
    """
    # Use the first selection (caret allowed)
    sel = None
    for r in view.sel():
        sel = r
        break
    if sel is None:
        return None

    # Consider just the current line for robustness
    line_reg = view.line(sel.begin())
    line_txt = view.substr(line_reg)

    # Regex matching t('...') or I18n.t("...") (single-line)
    pattern = r"(?:\bI18n\.t|\bt)\s*\(\s*(['\"])([^'\"\n]+)\1\s*\)"
    for m in re.finditer(pattern, line_txt):
        call_start = line_reg.begin() + m.start()
        call_end   = line_reg.begin() + m.end()
        key_rel    = m.group(2)
        # spans for overlap checks
        q1_rel, q2_rel = m.start(1), m.end(1)
        k_rel_s, k_rel_e = m.start(2), m.end(2)
        key_start = line_reg.begin() + k_rel_s
        key_end   = line_reg.begin() + k_rel_e
        quoted_start = line_reg.begin() + q1_rel
        quoted_end   = line_reg.begin() + q2_rel

        call_region   = sublime.Region(call_start, call_end)
        key_region    = sublime.Region(key_start, key_end)
        quoted_region = sublime.Region(quoted_start, quoted_end)

        # If selection overlaps ANY of: raw key, quoted key, entire call, accept
        if sel.intersects(key_region) or sel.intersects(quoted_region) or sel.intersects(call_region):
            return key_rel

    # Also allow when nothing is selected but caret is inside the call
    if sel.empty():
        for m in re.finditer(pattern, line_txt):
            call_start = line_reg.begin() + m.start()
            call_end   = line_reg.begin() + m.end()
            if call_start <= sel.begin() <= call_end:
                return m.group(2)

    return None


def is_rails_file(view):
    """Return True if the file looks like a Rails Ruby/view file (not JS/TXT)."""
    fname = view.file_name() or ""
    if not fname:
        return False
    lowered = fname.lower()
    allowed_exts = (".rb", ".erb", ".haml", ".slim", ".rhtml", ".builder", ".jbuilder", ".rake")
    return any(lowered.endswith(ext) for ext in allowed_exts)


def is_applicable_context(view):
    """Visible/Enabled only in Rails-like files AND when caret/selection is inside t('...')."""
    return is_rails_file(view) and (extract_key_from_t_call(view) is not None)


# ---------- Ruby helpers (embedded) ----------
# - allow Symbol in safe_load (Ruby 2.6 compatible)
# - try positional-args signature first, then keyword-args (newer Psych)
# - deep_get tries both String and Symbol keys
# - read from all *.locale.yml, but write only to <locale>.yml

RUBY_FETCH_VALUES = r'''
  require "yaml"
  require "json"

  locales_dir = ARGV[0]
  key_path    = ARGV[1]
  parts       = key_path.split(".")

  def deep_get(hash, keys)
    cur = hash
    keys.each do |k|
      opts = [k]
      begin; opts << k.to_s; rescue; end
      begin; opts << k.to_sym; rescue; end
      found = false
      opts.each do |kk|
        if cur.is_a?(Hash) && cur.key?(kk)
          cur = cur[kk]
          found = true
          break
        end
      end
      return nil unless found
    end
    cur
  end

  def safe_load_yaml(raw)
    return {} if raw.nil? || raw.strip == ""
    begin
      return YAML.safe_load(raw, [Symbol], [], true) || {}
    rescue ArgumentError
      return YAML.safe_load(raw, permitted_classes: [Symbol], permitted_symbols: [], aliases: true) || {}
    end
  rescue
    {}
  end

  def load_yaml(path)
    raw = File.exist?(path) ? File.read(path) : ""
    safe_load_yaml(raw)
  end

  fallback = {}
  primary  = {}
  locales  = {}

  Dir.glob(File.join(locales_dir, "*.yml")).each do |file|
    data = load_yaml(file)
    tops = data.keys
    if tops.empty?
      base = File.basename(file, ".yml")
      if base =~ /^([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)$/
        data = { base => {} }
        tops = [base]
      elsif base =~ /^.+\.([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)$/
        data = { $1 => {} }
        tops = [$1]
      end
    end

    tops.each do |loc|
      loc_norm = loc.to_s.tr("_", "-")
      locales[loc_norm] = true
      v = deep_get(data, [loc] + parts)
      next unless v.is_a?(String)

      base = File.basename(file)
      if base == "#{loc}.yml" || base == "#{loc_norm}.yml" || base == "#{loc.to_s.tr('-', '_')}.yml"
        primary[loc_norm] = v if v.strip != ""
      else
        fallback[loc_norm] ||= v if v.strip != ""
      end
    end
  end

  result = {}
  locales.keys.sort.each do |loc|
    result[loc] = primary[loc] || fallback[loc] || ""
  end

  puts JSON.generate(result)
'''

RUBY_WRITE_VALUES = r'''
  require "yaml"
  require "json"
  require "fileutils"

  locales_dir = ARGV[0]
  key_path    = ARGV[1]
  json_values = ARGV[2]
  parts       = key_path.split(".")
  values      = JSON.parse(json_values)

  def deep_set(hash, keys, v)
    cur = hash
    keys[0..-2].each do |k|
      k = k.to_s
      cur[k] ||= {}
      cur[k] = {} unless cur[k].is_a?(Hash)
      cur = cur[k]
    end
    cur[keys[-1].to_s] = v
  end

  def sorted_hash(h)
    return h unless h.is_a?(Hash)
    Hash[h.keys.map(&:to_s).sort.map { |k| [k, sorted_hash(h[k] || h[k.to_sym])] }]
  end

  def safe_load_yaml(raw)
    return {} if raw.nil? || raw.strip == ""
    begin
      return YAML.safe_load(raw, [Symbol], [], true) || {}
    rescue ArgumentError
      return YAML.safe_load(raw, permitted_classes: [Symbol], permitted_symbols: [], aliases: true) || {}
    end
  rescue
    {}
  end

  values.each do |loc, v|
    loc_norm_dash = loc.tr("_", "-")
    loc_norm_und  = loc.tr("-", "_")

    primary_path = File.join(locales_dir, "#{loc_norm_dash}.yml")
    primary_path = File.join(locales_dir, "#{loc_norm_und}.yml") unless File.exist?(primary_path)

    raw = File.exist?(primary_path) ? File.read(primary_path) : ""
    data = safe_load_yaml(raw)
    data = {} unless data.is_a?(Hash)

    top_key = (data.keys.find { |k| k.to_s == loc || k.to_s == loc_norm_dash || k.to_s == loc_norm_und } || loc_norm_dash).to_s

    data[top_key] ||= {}
    deep_set(data, [top_key] + parts, v)

    data = sorted_hash(data)
    FileUtils.mkdir_p(File.dirname(primary_path))
    File.open(primary_path, "w") { |f| f.write(data.to_yaml) }
  end
'''

# ---------- Commands ----------

class I18nRailsEditKeyCommand(sublime_plugin.TextCommand):
    def is_enabled(self):
        return is_applicable_context(self.view)

    def is_visible(self):
        # return is_applicable_context(self.view)
        return True

    """
    Edit per-locale values for the key inside t('...') / I18n.t('...') at selection.
    Only runs if selection/caret is within such a call.
    """
    def run(self, edit):
        if not is_rails_file(self.view):
          return

        key_from_call = extract_key_from_t_call(self.view)
        if not key_from_call:
            return

        selected = key_from_call.strip()
        self.settings = sublime.load_settings(SETTINGS_FILE) or sublime.Settings()
        self.project_root = find_project_root(self.view.file_name())
        if not self.project_root:
            sublime.error_message("Could not find Rails project root (config/locales).")
            return

        self.locales_dir = os.path.join(self.project_root, self.settings.get("locales_dir", "config/locales"))
        if not os.path.isdir(self.locales_dir):
            sublime.error_message("Locales directory not found: {}".format(self.locales_dir))
            return

        # resolve key if relative
        if selected.startswith("."):
            abs_key = resolve_relative_key(self.view, selected)
            if not abs_key:
                sublime.error_message("Could not resolve relative key '{}' from this file path.".format(selected))
                return
            self.key = abs_key
        else:
            self.key = selected

        pairs = list_locales(self.locales_dir)
        locales = [loc for (loc, _p) in pairs]
        if not locales:
            sublime.error_message("No <locale>.yml files found in {}".format(self.locales_dir))
            return

        ruby_path = self.settings.get("ruby_path", "ruby")
        ok, out = run_ruby(ruby_path, RUBY_FETCH_VALUES, [self.locales_dir, self.key])
        if not ok:
            sublime.error_message("Failed reading YAML values.\n\n{}".format(out))
            return

        try:
            existing_map = json.loads(out) if out.strip() else {}
        except Exception as e:
            sublime.error_message("Invalid fetch response: {}\n\n{}".format(e, out[:4000]))
            return

        default_locale = self.settings.get("default_locale_first", "")
        self.locales = locales[:]
        if default_locale and default_locale in self.locales:
            self.locales.remove(default_locale)
            self.locales.insert(0, default_locale)

        self.values = {}
        self.ruby_path = ruby_path
        self.existing_map = existing_map

        sublime.status_message("i18n: Editing key '{}'".format(self.key))
        self._prompt_next_locale(0)

    def _prompt_next_locale(self, idx):
        if idx >= len(self.locales):
            self._write_values()
            return
        loc = self.locales[idx]
        prefill = self.existing_map.get(loc, "")
        if prefill == "":
            alt = loc.replace("-", "_") if "-" in loc else loc.replace("_", "-")
            prefill = self.existing_map.get(alt, "")
        caption = "Value for [{}] {}".format(loc, self.key)
        self.view.window().show_input_panel(
            caption,
            prefill if isinstance(prefill, str) else "",
            lambda v, i=idx, l=loc: self._on_input(v, i, l),
            None,
            lambda i=idx: self._on_cancel(i)
        )

    def _on_input(self, value, idx, loc):
        self.values[loc] = value
        self._prompt_next_locale(idx + 1)

    def _on_cancel(self, idx):
        sublime.status_message("i18n: cancelled at locale prompt.")
        self.values = {}

    def _write_values(self):
        if not self.values:
            return
        payload = json.dumps(self.values)
        ok, out = run_ruby(self.ruby_path, RUBY_WRITE_VALUES, [self.locales_dir, self.key, payload])
        if not ok:
            sublime.error_message("Failed writing YAML values.\n\n{}".format(out))
            return
        sublime.status_message("i18n: Updated key '{}' for {} locale(s).".format(self.key, len(self.values)))


class I18nRailsJumpToKeyCommand(sublime_plugin.TextCommand):
    def is_enabled(self):
        return is_applicable_context(self.view)

    def is_visible(self):
        # return is_applicable_context(self.view)
        return True

    """
    Ask for a locale, then open <locale>.yml and jump to the VALUE of the key
    extracted ONLY from t('...') / I18n.t('...') at selection.
    YAML-aware traversal.
    """
    def run(self, edit):
        if not is_rails_file(self.view):
          return

        key_from_call = extract_key_from_t_call(self.view)
        if not key_from_call:
            return

        selected = key_from_call.strip()
        self.settings = sublime.load_settings(SETTINGS_FILE) or sublime.Settings()
        self.project_root = find_project_root(self.view.file_name())
        if not self.project_root:
            sublime.error_message("Could not find Rails project root (config/locales).")
            return

        self.locales_dir = os.path.join(self.project_root, self.settings.get("locales_dir", "config/locales"))
        if not os.path.isdir(self.locales_dir):
            sublime.error_message("Locales directory not found: {}".format(self.locales_dir))
            return

        if selected.startswith("."):
            abs_key = resolve_relative_key(self.view, selected)
            if not abs_key:
                sublime.error_message("Could not resolve relative key '{}'".format(selected))
                return
            self.key = abs_key
        else:
            self.key = selected

        pairs = list_locales(self.locales_dir)
        locales = [loc for (loc, _p) in pairs]
        if not locales:
            sublime.error_message("No <locale>.yml files found in {}".format(self.locales_dir))
            return

        self.locales_map = dict(pairs)
        self.view.window().show_quick_panel(locales, self._on_pick_locale)

    # ---------- YAML traversal helpers (same as previous reliable version) ----------

    def _indent(self, s):
        i = 0
        for ch in s:
            if ch == " ":
                i += 1
            elif ch == "\t":
                i += 2
            else:
                break
        return i

    def _line_starts_key(self, line, key_name, exact_indent):
        if self._indent(line) != exact_indent:
            return False
        pattern = r'^\s*(?:"{}"|{}):\s*(.*)$'.format(re.escape(key_name), re.escape(key_name))
        return re.match(pattern, line) is not None

    def _find_key_line_within_block(self, lines, start_idx, parent_indent, key_name):
        child_indent = None
        i = start_idx + 1
        n = len(lines)
        while i < n:
            line = lines[i]
            if line.strip() != "" and self._indent(line) <= parent_indent:
                break
            if line.strip().startswith("#") or line.strip() == "":
                i += 1
                continue
            ind = self._indent(line)
            if ind > parent_indent:
                if child_indent is None:
                    child_indent = ind
                if ind == child_indent and self._line_starts_key(line, key_name, child_indent):
                    return i, child_indent
            i += 1
        return None, None

    def _find_locale_top(self, lines, locale):
        candidates = {locale, locale.replace("-", "_"), locale.replace("_", "-")}
        for idx, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue
            ind = self._indent(line)
            if ind != 0:
                continue
            for loc in candidates:
                if re.match(r'^\s*(?:"{}"|{}):\s*$'.format(re.escape(loc), re.escape(loc)), line):
                    return idx
        return None

    def _value_region_on_line(self, v, line_region, key_name):
        line_text = v.substr(line_region)
        m = re.search(r'(?:"{}"|{}):\s*(.+)$'.format(re.escape(key_name), re.escape(key_name)), line_text)
        if not m:
            return None
        start = line_region.begin() + m.start(1)
        end = line_region.begin() + m.end(1)
        value = v.substr(sublime.Region(start, end))
        hash_idx = value.find(" #")
        if hash_idx != -1:
            end = start + hash_idx
        return sublime.Region(start, end)

    # ---------- flow ----------

    def _on_pick_locale(self, idx):
        if idx == -1:
            return
        locale = list(self.locales_map.keys())[idx]
        file_path = self.locales_map[locale]
        self._locale = locale
        win = self.view.window()
        if not win:
            return
        v = win.open_file(file_path)
        sublime.set_timeout_async(lambda: self._jump_loaded(v), 200)

    def _jump_loaded(self, v):
        if not v or v.is_loading():
            sublime.set_timeout_async(lambda: self._jump_loaded(v), 200)
            return

        parts = self.key.split(".")
        if not parts:
            return
        key_parts = parts[:]

        text = v.substr(sublime.Region(0, v.size()))
        lines = text.splitlines()

        top_idx = self._find_locale_top(lines, self._locale)
        if top_idx is None:
            top_idx = -1
            parent_indent = -1
        else:
            parent_indent = 0

        current_idx = top_idx
        current_indent = parent_indent
        for i, name in enumerate(key_parts):
            if i == 0 and name in {self._locale, self._locale.replace("-", "_"), self._locale.replace("_", "-")}:
                continue
            found_idx, child_indent = self._find_key_line_within_block(lines, current_idx, current_indent, name)
            if found_idx is None:
                leaf = key_parts[-1]
                m = re.search(r'(^|\s)(?:"{}"|{}):\s*(.+)$'.format(re.escape(leaf), re.escape(leaf)), text, flags=re.MULTILINE)
                if m:
                    a = m.start(3); b = m.end(3)
                    region = sublime.Region(a, b)
                    v.show(region); v.sel().clear(); v.sel().add(region)
                    sublime.status_message("Jumped to value of '{}' (fallback)".format(self.key))
                else:
                    sublime.status_message("Key '{}' not found in file.".format(self.key))
                return
            current_idx = found_idx
            current_indent = child_indent

        line_region = v.line(v.text_point(current_idx, 0))
        value_region = self._value_region_on_line(v, line_region, key_parts[-1])
        if value_region:
            v.show(value_region); v.sel().clear(); v.sel().add(value_region)
            sublime.status_message("Jumped to value of key '{}'".format(self.key))
        else:
            v.show(line_region); v.sel().clear(); v.sel().add(line_region)
            sublime.status_message("Jumped to key '{}' (no scalar value on line)".format(self.key))
